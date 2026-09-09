from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from training_capture import capture_training_images as capture


ROOT = Path(__file__).resolve().parents[1]


def example_config() -> dict:
    return json.loads((ROOT / "training_capture" / "capture-config.json").read_text(encoding="utf-8"))


class FakeCamera:
    instances: list["FakeCamera"] = []

    def __init__(self, config: dict, timeout: float):
        self.config = config
        self.timeout = timeout
        self.closed = False
        self.index = 0
        self.__class__.instances.append(self)

    def __enter__(self) -> "FakeCamera":
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def readback(self) -> dict:
        return {
            "camera_name": self.config["name"],
            "offset_x": self.config["offset_x"],
            "offset_y": self.config["offset_y"],
            "exposure_us": 1234.0,
            "gain": 2.0,
            "acquisition_mode": self.config["acquisition_mode"],
        }

    def capture(self) -> capture.CapturedFrame:
        self.index += 1
        pixels = bytes([self.index, 2, 3, 4, 5, 6])
        return capture.CapturedFrame(
            rgb=pixels,
            width=2,
            height=1,
            pixel_format="RGB",
            camera_readback=self.readback(),
            received_utc=f"2026-09-07T10:15:3{self.index}.123456Z",
        )


class TrainingCaptureTests(unittest.TestCase):
    def isolated_config(self) -> dict:
        config = copy.deepcopy(example_config())
        config["camera"]["width"] = 2
        config["camera"]["height"] = 1
        config["safety"]["blocked_process_patterns"] = []
        config["safety"]["blocked_local_ports"] = []
        config["safety"]["minimum_free_bytes"] = 0
        return config

    def test_example_config_is_valid(self) -> None:
        capture.validate_config(example_config())

    def test_part_id_validation(self) -> None:
        for part_id in ("PB-000123", "part_1", "A.b-c"):
            self.assertEqual(capture.validate_part_id(part_id), part_id)
        for part_id in ("", "../escape", "has space", "a/b", "x" * 129):
            with self.subTest(part_id=part_id), self.assertRaises(capture.CaptureError):
                capture.validate_part_id(part_id)

    def test_pipeline_is_camera_only(self) -> None:
        description = capture.GstCamera(example_config()["camera"], 1).pipeline_description()
        self.assertIn("aravissrc", description)
        self.assertNotIn("trigger=Software", description)
        self.assertIn("format=RGB,width=3200,height=2256", description)
        for forbidden in ("simaaiprocess", "boxdecode", "overlay", "flask", "light", "serial"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, description.lower())

    def test_save_artifacts_is_atomic_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            png = b"fake-lossless-png"
            artifact = capture.save_artifacts(directory, "capture-1", png, {"part_id": "P1"})
            self.assertEqual(artifact.image.read_bytes(), png)
            self.assertEqual(artifact.image_sha256, capture.sha256_bytes(png))
            metadata = json.loads(artifact.metadata.read_text(encoding="utf-8"))
            self.assertEqual(metadata["image_sha256"], artifact.image_sha256)
            self.assertTrue(artifact.checksum.read_text(encoding="ascii").endswith("  capture-1.png\n"))
            self.assertFalse(list(directory.glob(f"*{capture.TEMP_SUFFIX}")))
            with self.assertRaisesRegex(capture.CaptureError, "overwrite"):
                capture.save_artifacts(directory, "capture-1", png, {"part_id": "P1"})

    def test_capture_orchestration_with_fake_camera(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            FakeCamera.instances.clear()
            with mock.patch.object(capture, "encode_png", side_effect=lambda rgb, *_args: b"PNG" + rgb):
                artifacts = capture.capture_images(
                    config=self.isolated_config(),
                    part_id="PB-1",
                    count=2,
                    interval_ms=0,
                    timeout_seconds=1,
                    local_root=Path(temporary),
                    require_copy=False,
                    camera_factory=FakeCamera,
                )
            self.assertEqual(len(artifacts), 2)
            self.assertTrue(all(item.image.exists() for item in artifacts))
            self.assertTrue(FakeCamera.instances[-1].closed)
            metadata = json.loads(artifacts[0].metadata.read_text(encoding="utf-8"))
            self.assertEqual(metadata["camera_serial"], "40735904")
            self.assertEqual(metadata["exposure_us"], 1234.0)
            self.assertEqual(metadata["copy_status"], "not_requested")

    def test_camera_is_released_when_capture_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            FakeCamera.instances.clear()
            with mock.patch.object(capture, "encode_png", side_effect=capture.CaptureError("encode failed")):
                with self.assertRaisesRegex(capture.CaptureError, "encode failed"):
                    capture.capture_images(
                        config=self.isolated_config(),
                        part_id="PB-1",
                        count=1,
                        interval_ms=0,
                        timeout_seconds=1,
                        local_root=Path(temporary),
                        require_copy=False,
                        camera_factory=FakeCamera,
                    )
            self.assertTrue(FakeCamera.instances[-1].closed)

    def test_admin_powershell_uses_encoded_command(self) -> None:
        commands: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object):
            commands.append(command)
            return type("Completed", (), {"returncode": 0, "stdout": "True\n", "stderr": ""})()

        transfer = capture.AdminTransfer(example_config()["transfer"], runner=runner)
        transfer.preflight()
        command = commands[0]
        self.assertIn("-EncodedCommand", command)
        encoded = command[command.index("-EncodedCommand") + 1]
        self.assertEqual(__import__("base64").b64decode(encoded).decode("utf-16le"), "$true")
        self.assertNotIn("-Command", command)


if __name__ == "__main__":
    unittest.main()
