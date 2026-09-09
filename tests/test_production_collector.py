import hashlib
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from production_frame_capture.collector import Collector, CollectorConfig
from production_frame_capture.capture_stream import CaptureError
from production_changes.training_capture_spool import TrainingCaptureSpool


FRAME_ID = "0123456789abcdef0123456789abcdef"
CAPTURED_UTC = "2026-09-07T10:15:30.123456Z"


def make_png(width=4, height=3, color=(10, 20, 30)):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanlines = b"".join(b"\x00" + bytes(color) * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b"")


def make_sidecar(frame_id=FRAME_ID, image=None):
    image = image or make_png()
    return {
        "schema_version": "production-training-frame-1.0",
        "frame_id": frame_id,
        "captured_utc": CAPTURED_UTC,
        "pipeline": "PowerBoard_simaaisrc.py",
        "image": {
            "file": frame_id + ".png",
            "media_type": "image/png",
            "width": 4,
            "height": 3,
            "pixel_format": "BGR8",
            "sha256": hashlib.sha256(image).hexdigest(),
            "bytes": len(image),
        },
        "detections": [],
        "training_label_status": "unannotated",
    }


class FakeClient:
    def __init__(self, image=None, metadata=None, fail_ack_once=False):
        self.image_payload = image or make_png()
        self.metadata_document = metadata or make_sidecar(image=self.image_payload)
        self.fail_ack_once = fail_ack_once
        self.acks = []

    def health(self):
        return {"schema_version": "production-training-frame-1.0", "status": "ok"}

    def pending(self):
        return [{"frame_id": self.metadata_document["frame_id"], "captured_utc": CAPTURED_UTC}]

    def metadata(self, _frame_id):
        payload = (json.dumps(self.metadata_document, indent=2) + "\n").encode()
        return payload, self.metadata_document

    def image(self, _frame_id):
        return self.image_payload

    def acknowledge(self, frame_id, digest):
        if self.fail_ack_once:
            self.fail_ack_once = False
            raise CaptureError("simulated lost acknowledgement")
        self.acks.append((frame_id, digest))


class CollectorTests(unittest.TestCase):
    def config(self, root):
        return CollectorConfig("http://board:5001", Path(root), minimum_free_bytes=0)

    def test_saves_exact_pair_then_acknowledges(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = FakeClient()
            collector = Collector(self.config(temporary), client)
            self.assertEqual(collector.collect_once(), 1)
            image_path = Path(temporary) / "2026-09-07" / "images" / f"{FRAME_ID}.png"
            metadata_path = Path(temporary) / "2026-09-07" / "metadata" / f"{FRAME_ID}.json"
            self.assertEqual(image_path.read_bytes(), client.image_payload)
            self.assertEqual(json.loads(metadata_path.read_text())["frame_id"], FRAME_ID)
            self.assertEqual(client.acks[0][1], hashlib.sha256(client.image_payload).hexdigest())
            self.assertFalse(list(Path(temporary).rglob("*.part")))

    def test_restart_reuses_verified_files_after_lost_ack(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = FakeClient(fail_ack_once=True)
            collector = Collector(self.config(temporary), client)
            with self.assertRaisesRegex(CaptureError, "lost acknowledgement"):
                collector.collect_once()
            image_path = Path(temporary) / "2026-09-07" / "images" / f"{FRAME_ID}.png"
            original = image_path.read_bytes()
            self.assertEqual(collector.collect_once(), 1)
            self.assertEqual(image_path.read_bytes(), original)
            self.assertEqual(len(client.acks), 1)

    def test_refuses_hash_mismatch_without_ack(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = make_png()
            metadata = make_sidecar(image=image)
            metadata["image"]["sha256"] = "0" * 64
            client = FakeClient(image=image, metadata=metadata)
            with self.assertRaisesRegex(CaptureError, "SHA-256"):
                Collector(self.config(temporary), client).collect_once()
            self.assertEqual(client.acks, [])

    def test_refuses_existing_file_collision_without_ack(self):
        with tempfile.TemporaryDirectory() as temporary:
            collision = Path(temporary) / "2026-09-07" / "images" / f"{FRAME_ID}.png"
            collision.parent.mkdir(parents=True)
            collision.write_bytes(b"different")
            client = FakeClient()
            with self.assertRaisesRegex(CaptureError, "colliding"):
                Collector(self.config(temporary), client).collect_once()
            self.assertEqual(collision.read_bytes(), b"different")
            self.assertEqual(client.acks, [])


class DummyFrame:
    shape = (3, 4, 3)


class WorkerSpoolTests(unittest.TestCase):
    def test_publish_index_download_and_idempotent_ack(self):
        image = make_png()
        with tempfile.TemporaryDirectory() as temporary:
            spool = TrainingCaptureSpool("worker.py", root=temporary, png_encoder=lambda _frame: image)
            capture = spool.prepare(DummyFrame())
            frame_id = capture["frame_id"]
            self.assertEqual(spool.publish(capture, [{"class_id": 1}])[0], "ready")
            self.assertEqual(spool.index()["frames"][0]["frame_id"], frame_id)
            metadata = json.loads(spool.read_metadata(frame_id))
            self.assertEqual(metadata["detections"], [{"class_id": 1}])
            self.assertEqual(spool.read_image(frame_id), image)
            digest = hashlib.sha256(image).hexdigest()
            self.assertEqual(spool.acknowledge(frame_id, "f" * 64), ("digest-mismatch", 409))
            self.assertIsNotNone(spool.read_image(frame_id))
            self.assertEqual(spool.acknowledge(frame_id, digest), ("acknowledged", 200))
            self.assertEqual(spool.acknowledge(frame_id, digest), ("acknowledged", 200))
            self.assertEqual(spool.health()["pending_frames"], 0)

    def test_flask_routes_expose_and_acknowledge_immutable_pair(self):
        from flask import Flask

        image = make_png()
        with tempfile.TemporaryDirectory() as temporary:
            app = Flask(__name__)
            spool = TrainingCaptureSpool("worker.py", root=temporary, png_encoder=lambda _frame: image)
            spool.register_routes(app)
            capture = spool.prepare(DummyFrame())
            frame_id = capture["frame_id"]
            spool.publish(capture, [])
            client = app.test_client()
            self.assertEqual(client.get("/training_capture/health").status_code, 200)
            self.assertEqual(client.get("/training_frames").json["frames"][0]["frame_id"], frame_id)
            self.assertEqual(client.get(f"/training_frames/{frame_id}.png").data, image)
            metadata = client.get(f"/training_frames/{frame_id}").json
            response = client.post(
                f"/training_frames/{frame_id}/ack",
                json={"image_sha256": metadata["image"]["sha256"]},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["status"], "acknowledged")
            self.assertEqual(client.get(f"/training_frames/{frame_id}.png").status_code, 404)

    def test_full_spool_does_not_evict_pending_frame(self):
        image = make_png()
        with tempfile.TemporaryDirectory() as temporary:
            spool = TrainingCaptureSpool(
                "worker.py", root=temporary, maximum_pending_frames=1, png_encoder=lambda _frame: image
            )
            first = spool.prepare(DummyFrame())
            first_id = first["frame_id"]
            self.assertEqual(spool.publish(first, [])[0], "ready")
            second = spool.prepare(DummyFrame())
            self.assertEqual(spool.publish(second, [])[0], "spool-full")
            self.assertEqual([item["frame_id"] for item in spool.index()["frames"]], [first_id])


if __name__ == "__main__":
    unittest.main()
