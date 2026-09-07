from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def upload(base_url: str, path: Path, frame_id: str, timeout: float) -> dict:
    boundary = f"----evaluation-{uuid.uuid4().hex}"
    media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = b"".join((
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"frame_id\"\r\n\r\n{frame_id}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{path.name}\"\r\nContent-Type: {media}\r\n\r\n".encode(),
        path.read_bytes(), f"\r\n--{boundary}--\r\n".encode()))
    request = Request(base_url.rstrip("/") + "/infer", data=body, method="POST",
                      headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "corpus" / "manifest.json")
    parser.add_argument("--base-url", default="http://192.168.1.20:5004")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "board")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    frames = [frame for frame in manifest["frames"] if frame["role"] != "calibration"]
    if len(frames) != 25:
        parser.error(f"expected exactly 25 evaluation frames, found {len(frames)}")
    args.output.mkdir(parents=True, exist_ok=True)
    overlay_dir = args.output / "overlays"
    overlay_dir.mkdir(exist_ok=True)
    for index, frame in enumerate(frames, 1):
        frame_id = frame["source_sha256"][:16]
        result = upload(args.base_url, ROOT / frame["path"], frame_id, args.timeout)
        if result.get("frame_id") != frame_id:
            raise RuntimeError(f"frame ID mismatch for {frame_id}")
        (args.output / f"{frame_id}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        with urlopen(args.base_url.rstrip("/") + result["artifacts"]["overlay"], timeout=args.timeout) as response:
            (overlay_dir / f"{frame_id}.png").write_bytes(response.read())
        print(f"{index}/25 {frame_id}: {len(result['detections'])} detections")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
