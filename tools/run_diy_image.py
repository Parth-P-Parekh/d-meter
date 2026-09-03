"""Windows-friendly one-image client for the independent board demo."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def multipart_body(frame_id: str, image_path: Path) -> tuple[bytes, str]:
    boundary = f"----diy-inference-{uuid.uuid4().hex}"
    image_bytes = image_path.read_bytes()
    media_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    parts = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="frame_id"\r\n\r\n',
        frame_id.encode("utf-8"), b"\r\n",
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{image_path.name}"\r\n'.encode(),
        f"Content-Type: {media_type}\r\n\r\n".encode(),
        image_bytes,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts), boundary


def get_bytes(url: str, timeout: float) -> bytes:
    with urlopen(url, timeout=timeout) as response:
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload one image to the DIY SiMa-board demo and save its JSON and overlay.")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--frame-id", required=True)
    parser.add_argument("--output", type=Path, default=Path("results/diy"))
    parser.add_argument("--base-url", default="http://192.168.1.20:5003")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if not args.image.is_file():
        parser.error(f"image does not exist: {args.image}")
    body, boundary = multipart_body(args.frame_id, args.image)
    request = Request(
        f"{args.base_url.rstrip('/')}/infer", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urlopen(request, timeout=args.timeout) as response:
            result = json.loads(response.read())
        if result.get("frame_id") != args.frame_id or "run_id" not in result:
            raise ValueError(f"board returned an invalid result: {result}")
        args.output.mkdir(parents=True, exist_ok=True)
        result_path = args.output / f"{args.frame_id}.json"
        overlay_path = args.output / f"{args.frame_id}.png"
        result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        overlay_url = f"{args.base_url.rstrip('/')}{result['artifacts']['overlay']}"
        overlay_path.write_bytes(get_bytes(overlay_url, args.timeout))
    except (HTTPError, URLError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"DIY INFERENCE FAILED: {error}", file=sys.stderr)
        return 1
    print(f"Result JSON: {result_path.resolve()}")
    print(f"Overlay PNG: {overlay_path.resolve()}")
    print(f"Detections: {len(result['detections'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
