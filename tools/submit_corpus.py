"""Submit every curated image to the isolated board demo and save successful result JSON."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sima_parity.results import load_json


def multipart_body(frame_id: str, image_path: Path) -> tuple[bytes, str]:
    boundary = f"----wood-parity-{uuid.uuid4().hex}"
    image_bytes = image_path.read_bytes()
    content_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    parts = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="frame_id"\r\n\r\n',
        frame_id.encode("utf-8"), b"\r\n",
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{image_path.name}"\r\n'.encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        image_bytes, b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts), boundary


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit a corpus sequentially to the isolated SiMa parity endpoint.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--url", default="http://192.168.1.20:5002/infer")
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args()
    try:
        manifest = load_json(args.manifest)
        if manifest.get("schema_version") != "1.1" or not isinstance(manifest.get("frames"), list):
            raise ValueError("manifest must have schema_version 1.1 and a frames list")
    except ValueError as error:
        print(f"MANIFEST INVALID: {error}", file=sys.stderr)
        return 2
    args.output.mkdir(parents=True, exist_ok=True)
    failed = 0
    for frame in manifest["frames"]:
        try:
            frame_id = frame["frame_id"]
            image_path = args.manifest.parent / frame["image_path"]
            body, boundary = multipart_body(frame_id, image_path)
            request = Request(args.url, data=body, method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            with urlopen(request, timeout=args.timeout) as response:
                payload = response.read()
            result = json.loads(payload)
            if result.get("frame_id") != frame_id or result.get("schema_version") != "1.1":
                raise ValueError("board response is not a successful parity result")
            (args.output / f"{frame_id}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(f"SAVED {frame_id}")
        except (OSError, KeyError, ValueError, HTTPError, URLError) as error:
            failed += 1
            print(f"FAILED {frame.get('frame_id', '<unknown>')}: {error}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
