from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--require-mpk", action="store_true")
    args = parser.parse_args()
    try:
        application = json.loads((args.project / "application.json").read_text(encoding="utf-8"))
        if application.get("name") != "yolo26n_coco_simaaisrc":
            raise ValueError(f"wrong application name: {application.get('name')}")
        serialized = json.dumps(application).lower()
        gst = str(application["pipelines"][0]["gst"]).lower()
        if "detess" not in serialized or "0_postproc.json" not in gst:
            raise ValueError("detess/dequant stage is absent")
        if "simaaiboxdecode" in gst or "genericboxdecode" in gst:
            raise ValueError("the active GStreamer path contains a YOLO box decoder")
        if "1280" not in serialized or "720" not in serialized:
            raise ValueError("1280x720 pipeline input contract is not evident")
        postproc_path = args.project / "plugins" / "processcvu" / "modalix" / "cfg" / "0_postproc.json"
        postproc = json.loads(postproc_path.read_text(encoding="utf-8"))
        if postproc.get("graph_name") != "detessdequant" or postproc.get("num_in_tensor") != 1:
            raise ValueError("postprocessing must detess/dequant exactly one YOLO26 output tensor")
        mpk = args.project / "project.mpk"
        if args.require_mpk and (not mpk.is_file() or mpk.stat().st_size == 0):
            raise ValueError("project.mpk is missing or empty")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"GENERATED PIPELINE INVALID: {error}", file=sys.stderr)
        return 1
    print(f"GENERATED PIPELINE VALID: {args.project}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
