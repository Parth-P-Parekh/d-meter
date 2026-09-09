#!/usr/bin/env python3
"""Local protocol simulator for the Admin collector; never use in production."""

import argparse
import struct
import tempfile
import zlib

from flask import Flask, jsonify

from production_changes.training_capture_spool import TrainingCaptureSpool


class FakeFrame:
    shape = (48, 64, 3)


def encode_fake_png(_frame):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", 64, 48, 8, 2, 0, 0, 0)
    scanlines = b"".join(b"\x00" + bytes((32, 64, 96)) * 64 for _ in range(48))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b"")


def create_app(spool_root):
    app = Flask(__name__)
    spool = TrainingCaptureSpool("fake-worker.py", root=spool_root, png_encoder=encode_fake_png)
    spool.register_routes(app)

    @app.route("/fake/enqueue", methods=["POST"])
    def enqueue():
        capture = spool.prepare(FakeFrame())
        frame_id = capture["frame_id"]
        status, error = spool.publish(capture, [])
        return jsonify({"frame_id": frame_id, "status": status, "error": error})

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5051)
    parser.add_argument("--spool-root")
    args = parser.parse_args()
    spool_root = args.spool_root or tempfile.mkdtemp(prefix="fake-production-capture-")
    print("Fake spool:", spool_root)
    create_app(spool_root).run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
