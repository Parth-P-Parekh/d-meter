from __future__ import annotations

import io
import json

import cv2
import numpy as np

from yolo26_modalix.service import create_app


class EmptyRunner:
    def infer_bgr(self, source_bgr):
        return [], {"preprocessing": 1.0, "inference": 2.0, "postprocessing": 0.1, "total": 3.1}, {
            "source_to_pipeline": {}, "pipeline_to_model": {},
        }


def app(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": "1.0", "artifacts": {"pt": {}, "onnx": {}, "mpk": {}}}))
    value = create_app(EmptyRunner(), tmp_path / "runs", manifest, service_name="test", require_mpk=False)
    value.config["TESTING"] = True
    return value


def png_bytes() -> bytes:
    ok, encoded = cv2.imencode(".png", np.zeros((20, 30, 3), dtype=np.uint8))
    assert ok
    return encoded.tobytes()


def test_valid_upload_result_and_overlay_routes(tmp_path):
    client = app(tmp_path).test_client()
    response = client.post("/infer", data={"frame_id": "frame-1", "image": (io.BytesIO(png_bytes()), "frame.png")})
    assert response.status_code == 200
    result = response.get_json()
    assert result["frame_id"] == "frame-1"
    assert result["detections"] == []
    assert client.get(result["artifacts"]["result"]).status_code == 200
    assert client.get(result["artifacts"]["overlay"]).mimetype == "image/png"


def test_missing_invalid_and_corrupt_uploads(tmp_path):
    client = app(tmp_path).test_client()
    assert client.post("/infer").status_code == 400
    assert client.post("/infer", data={"frame_id": "../bad", "image": (io.BytesIO(png_bytes()), "x.png")}).status_code == 400
    assert client.post("/infer", data={"frame_id": "ok", "image": (io.BytesIO(b"text"), "x.txt")}).status_code == 415
    assert client.post("/infer", data={"frame_id": "ok", "image": (io.BytesIO(b"not-png"), "x.png")}).status_code == 422


def test_unknown_run_and_contract(tmp_path):
    client = app(tmp_path).test_client()
    assert client.get("/runs/../../x/result").status_code == 404
    contract = client.get("/contract").get_json()
    assert contract["postprocessing"]["head"] == "one-to-many raw (1,84,8400)"
    assert "ChipOff" not in contract["result"] or "never" in contract["result"]

