import io
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest import mock

from production_frame_capture import capture_inference
from production_frame_capture import capture_stream
from test_production_frame_capture import png


FRAME_ID = "0123456789abcdef0123456789abcdef"


class FakeResponse:
    def __init__(self, payload: bytes, content_type: str, frame_id: str | None = None):
        self.payload = payload
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if frame_id is not None:
            self.headers[capture_inference.FRAME_ID_HEADER] = frame_id

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.payload


class ExactInferenceCaptureTests(unittest.TestCase):
    def test_saves_frame_matching_inference_header(self) -> None:
        inference = {"detections": [{"class_id": 1}]}
        responses = [
            FakeResponse(json.dumps(inference).encode(), "application/json", FRAME_ID),
            FakeResponse(png(4, 3), "image/png", FRAME_ID),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch("urllib.request.urlopen", side_effect=responses) as urlopen:
                saved, returned = capture_inference.capture_inference_frame(
                    "http://board:5001/", Path(temporary), "PB-1", 5.0
                )
            self.assertEqual(returned, inference)
            self.assertEqual(saved.image.read_bytes(), png(4, 3))
            metadata = json.loads(saved.metadata.read_text(encoding="utf-8"))
            self.assertEqual(metadata["production_frame_id"], FRAME_ID)
            self.assertEqual(metadata["inference_result"], inference)
            self.assertEqual(urlopen.call_count, 2)
            first_request = urlopen.call_args_list[0].args[0]
            second_request = urlopen.call_args_list[1].args[0]
            self.assertEqual(first_request.full_url, "http://board:5001/run_inference")
            self.assertEqual(second_request.full_url, f"http://board:5001/source_frame/{FRAME_ID}")

    def test_refuses_uncorrelated_unmodified_response(self) -> None:
        response = FakeResponse(b'{"detections": []}', "application/json")
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch("urllib.request.urlopen", return_value=response):
                with self.assertRaisesRegex(capture_stream.CaptureError, "compatibility diff is not installed"):
                    capture_inference.capture_inference_frame(
                        "http://board:5001", Path(temporary), "PB-1", 5.0
                    )

    def test_refuses_mismatched_frame_response(self) -> None:
        responses = [
            FakeResponse(b'{"detections": []}', "application/json", FRAME_ID),
            FakeResponse(png(), "image/png", "f" * 32),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch("urllib.request.urlopen", side_effect=responses):
                with self.assertRaisesRegex(capture_stream.CaptureError, "does not match"):
                    capture_inference.capture_inference_frame(
                        "http://board:5001", Path(temporary), "PB-1", 5.0
                    )


if __name__ == "__main__":
    unittest.main()
