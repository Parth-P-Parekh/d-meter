import io
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from production_frame_capture import capture_stream as capture


def png(width: int = 2, height: int = 1) -> bytes:
    signature = capture.PNG_SIGNATURE
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = b"\x00" + b"\x01\x02\x03" * width
    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def jpeg(width: int = 3, height: int = 2) -> bytes:
    # Dimension parsing only needs a structurally valid SOF segment.
    return b"\xff\xd8\xff\xc0\x00\x0b\x08" + struct.pack(">HH", height, width) + b"\x03\x00\x00\xff\xd9"


class ProductionFrameCaptureTests(unittest.TestCase):
    def test_extracts_png_and_jpeg_from_multipart_bytes(self) -> None:
        first, second = png(), jpeg()
        body = (
            b"--frame\r\nContent-Type: image/png\r\n\r\n" + first
            + b"\r\n--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
            + str(len(second)).encode() + b"\r\n\r\n" + second
            + b"\r\n--frame--\r\n"
        )
        iterator = capture.iter_multipart_frames(io.BytesIO(body), "frame", chunk_size=7)
        self.assertEqual(next(iterator), ("image/png", first))
        self.assertEqual(next(iterator), ("image/jpeg", second))

    def test_saves_immutable_image_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            saved = capture.save_frame(Path(temporary), "PB-1", png(), "image/png", "http://board/stream")
            self.assertEqual(saved.image.read_bytes(), png())
            metadata = json.loads(saved.metadata.read_text(encoding="utf-8"))
            self.assertEqual(metadata["image_sha256"], saved.sha256)
            self.assertEqual((metadata["width"], metadata["height"]), (2, 1))
            self.assertFalse(list(Path(temporary).rglob(f"*{capture.TEMP_SUFFIX}")))

    def test_rejects_unsafe_part_ids_and_invalid_png(self) -> None:
        with self.assertRaises(capture.CaptureError):
            capture.validate_part_id("../escape")
        with self.assertRaises(capture.CaptureError):
            capture.png_dimensions(b"not a png")

    def test_reads_jpeg_dimensions(self) -> None:
        self.assertEqual(capture.jpeg_dimensions(jpeg(3200, 2256)), (3200, 2256))


if __name__ == "__main__":
    unittest.main()
