from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yolo26_modalix.comparison import ParityPolicy, compare_detections

import mimetypes
import uuid
from urllib.request import Request, urlopen


def infer(url: str, image: Path, frame_id: str, timeout: float) -> dict:
    boundary = f"----benchmark-{uuid.uuid4().hex}"
    media = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
    body = b"".join((
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"frame_id\"\r\n\r\n{frame_id}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{image.name}\"\r\nContent-Type: {media}\r\n\r\n".encode(),
        image.read_bytes(), f"\r\n--{boundary}--\r\n".encode()))
    with urlopen(Request(url.rstrip("/") + "/infer", data=body,
                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}), timeout=timeout) as response:
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--base-url", default="http://192.168.1.20:5004")
    parser.add_argument("--runs", type=int, default=50)
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "board-benchmark.json")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if args.runs != 50:
        parser.error("acceptance benchmark requires exactly 50 runs")
    records = []
    first_detections = None
    repeatability = []
    for index in range(args.runs):
        result = infer(args.base_url, args.image, f"benchmark-{index:03d}", args.timeout)
        detections = result["detections"]
        if first_detections is None:
            first_detections = detections
        comparison = compare_detections(first_detections, detections, ParityPolicy(0.99, 0.01))
        repeatability.append({"run": index + 1, **comparison})
        records.append({"run": index + 1, "detections": len(detections), **result["timings_ms"]})
        print(f"{index + 1}/50 total={result['timings_ms']['total']:.3f} ms")
    summary = {}
    for field in ("preprocessing", "inference", "postprocessing", "total"):
        values = [float(record[field]) for record in records]
        summary[field] = {"mean": statistics.fmean(values), "median": statistics.median(values),
                          "minimum": min(values), "maximum": max(values),
                          "p95_nearest_rank": sorted(values)[47]}
    passed = all(item["passed"] for item in repeatability)
    report = {"schema_version": "1.0", "runs": records, "summary_ms": summary,
              "repeatability": {"passed": passed, "policy": {"minimum_iou": 0.99, "maximum_confidence_delta": 0.01},
                                "runs": repeatability},
              "sla": None, "note": "Measured integration performance; no production SLA was imposed."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
