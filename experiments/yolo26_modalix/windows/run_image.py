from __future__ import annotations

import argparse
import json
import mimetypes
import uuid
from pathlib import Path
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--frame-id", required=True)
    parser.add_argument("--base-url", default="http://192.168.1.20:5004")
    parser.add_argument("--output", type=Path, default=Path("results/yolo26"))
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    if not args.image.is_file():
        parser.error(f"image not found: {args.image}")
    boundary = f"----yolo26-{uuid.uuid4().hex}"
    content_type = mimetypes.guess_type(args.image.name)[0] or "application/octet-stream"
    body = b"".join((
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"frame_id\"\r\n\r\n{args.frame_id}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{args.image.name}\"\r\nContent-Type: {content_type}\r\n\r\n".encode(),
        args.image.read_bytes(), f"\r\n--{boundary}--\r\n".encode(),
    ))
    request = Request(args.base_url.rstrip("/") + "/infer", data=body,
                      headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    with urlopen(request, timeout=args.timeout) as response:
        result = json.loads(response.read())
    args.output.mkdir(parents=True, exist_ok=True)
    json_path = args.output / f"{args.frame_id}.json"
    image_path = args.output / f"{args.frame_id}.png"
    json_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    with urlopen(args.base_url.rstrip("/") + result["artifacts"]["overlay"], timeout=args.timeout) as response:
        image_path.write_bytes(response.read())
    print(f"{len(result['detections'])} detections; {json_path}; {image_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

