#!/usr/bin/env python3
"""Additive exact-frame spool for a production Flask inference worker.

This module owns only temporary training-capture artifacts and HTTP routes. It
does not access the camera, model, reports, PLC, MES, or SQL interfaces.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import threading
import uuid
from collections import OrderedDict

from flask import Response, jsonify, request


SCHEMA_VERSION = "production-training-frame-1.0"
FRAME_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def _utc_text(value):
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


class TrainingCaptureSpool(object):
    def __init__(
        self,
        pipeline_name,
        root="/tmp/production-training-capture",
        maximum_pending_frames=32,
        maximum_pending_bytes=1024 * 1024 * 1024,
        warning_age_seconds=30,
        critical_age_seconds=120,
        recent_ack_limit=128,
        png_encoder=None,
    ):
        self.pipeline_name = pipeline_name
        self.root = os.path.abspath(root)
        self.ready_dir = os.path.join(self.root, "ready")
        self.staging_dir = os.path.join(self.root, "staging")
        self.events_path = os.path.join(self.root, "events.jsonl")
        self.maximum_pending_frames = maximum_pending_frames
        self.maximum_pending_bytes = maximum_pending_bytes
        self.warning_age_seconds = warning_age_seconds
        self.critical_age_seconds = critical_age_seconds
        self.recent_ack_limit = recent_ack_limit
        self.png_encoder = png_encoder
        self.lock = threading.RLock()
        self.recent_acks = OrderedDict()
        self.last_error = None
        os.makedirs(self.ready_dir, exist_ok=True)
        os.makedirs(self.staging_dir, exist_ok=True)

    def prepare(self, frame):
        """Capture request identity and the exact immutable handler frame."""
        captured = _utc_now()
        return {
            "frame_id": uuid.uuid4().hex,
            "captured_utc": _utc_text(captured),
            "frame": frame,
            "width": int(frame.shape[1]),
            "height": int(frame.shape[0]),
        }

    def _paths(self, frame_id, directory=None):
        base = directory or self.ready_dir
        return (
            os.path.join(base, frame_id + ".png"),
            os.path.join(base, frame_id + ".json"),
        )

    def _ready_metadata_locked(self):
        records = []
        for name in os.listdir(self.ready_dir):
            if not name.endswith(".json"):
                continue
            frame_id = name[:-5]
            if not FRAME_ID_RE.fullmatch(frame_id):
                continue
            image_path, metadata_path = self._paths(frame_id)
            if not os.path.isfile(image_path):
                continue
            try:
                with open(metadata_path, "r", encoding="utf-8") as handle:
                    metadata = json.load(handle)
            except (OSError, ValueError):
                continue
            if metadata.get("frame_id") == frame_id:
                records.append(metadata)
        records.sort(key=lambda item: (item.get("captured_utc", ""), item.get("frame_id", "")))
        return records

    def _pending_bytes_locked(self, records):
        total = 0
        for record in records:
            image = record.get("image", {})
            total += int(image.get("bytes", 0))
            try:
                total += os.path.getsize(self._paths(record["frame_id"])[1])
            except OSError:
                pass
        return total

    def _append_event_locked(self, event, **fields):
        record = {"utc": _utc_text(_utc_now()), "event": event}
        record.update(fields)
        with open(self.events_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _write_staging_file(self, path, payload):
        with open(path, "xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    def publish(self, capture, detections):
        """Encode and atomically publish after existing inspection work succeeds."""
        frame_id = capture["frame_id"]
        try:
            if self.png_encoder is None:
                import cv2

                encoded_ok, encoded = cv2.imencode(".png", capture["frame"])
                if not encoded_ok:
                    raise RuntimeError("OpenCV could not encode the source frame")
                image_payload = encoded.tobytes()
            else:
                image_payload = self.png_encoder(capture["frame"])
            digest = hashlib.sha256(image_payload).hexdigest()
            metadata = {
                "schema_version": SCHEMA_VERSION,
                "frame_id": frame_id,
                "captured_utc": capture["captured_utc"],
                "pipeline": self.pipeline_name,
                "image": {
                    "file": frame_id + ".png",
                    "media_type": "image/png",
                    "width": capture["width"],
                    "height": capture["height"],
                    "pixel_format": "BGR8",
                    "sha256": digest,
                    "bytes": len(image_payload),
                },
                "detections": detections,
                "training_label_status": "unannotated",
            }
            metadata_payload = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")
            token = uuid.uuid4().hex
            stage_image = os.path.join(self.staging_dir, frame_id + "." + token + ".png.part")
            stage_metadata = os.path.join(self.staging_dir, frame_id + "." + token + ".json.part")
            ready_image, ready_metadata = self._paths(frame_id)
            with self.lock:
                records = self._ready_metadata_locked()
                pending_bytes = self._pending_bytes_locked(records)
                if len(records) >= self.maximum_pending_frames or (
                    pending_bytes + len(image_payload) + len(metadata_payload) > self.maximum_pending_bytes
                ):
                    self.last_error = "capture spool is full"
                    self._append_event_locked("spool_full", frame_id=frame_id)
                    return "spool-full", self.last_error
                self._write_staging_file(stage_image, image_payload)
                self._write_staging_file(stage_metadata, metadata_payload)
                os.replace(stage_image, ready_image)
                os.replace(stage_metadata, ready_metadata)
                self.last_error = None
                self._append_event_locked("frame_ready", frame_id=frame_id, image_sha256=digest)
            return "ready", None
        except Exception as error:
            with self.lock:
                self.last_error = str(error)
                self._append_event_locked("publish_error", frame_id=frame_id, error=str(error))
            return "error", str(error)
        finally:
            capture.pop("frame", None)
            for variable_name in ("stage_image", "stage_metadata"):
                path = locals().get(variable_name)
                if path:
                    try:
                        os.unlink(path)
                    except FileNotFoundError:
                        pass

    def health(self):
        with self.lock:
            records = self._ready_metadata_locked()
            pending_bytes = self._pending_bytes_locked(records)
            oldest_age = 0.0
            if records:
                try:
                    captured = datetime.datetime.fromisoformat(records[0]["captured_utc"].replace("Z", "+00:00"))
                    oldest_age = max(0.0, (_utc_now() - captured).total_seconds())
                except (KeyError, TypeError, ValueError):
                    oldest_age = 0.0
            if self.last_error:
                status = "error"
            elif oldest_age >= self.critical_age_seconds:
                status = "critical"
            elif oldest_age >= self.warning_age_seconds:
                status = "warning"
            else:
                status = "ok"
            return {
                "schema_version": SCHEMA_VERSION,
                "status": status,
                "pending_frames": len(records),
                "pending_bytes": pending_bytes,
                "oldest_age_seconds": round(oldest_age, 3),
                "maximum_pending_frames": self.maximum_pending_frames,
                "maximum_pending_bytes": self.maximum_pending_bytes,
                "last_error": self.last_error,
            }

    def index(self):
        with self.lock:
            records = self._ready_metadata_locked()
            frames = [
                {
                    "frame_id": record["frame_id"],
                    "captured_utc": record["captured_utc"],
                    "image_sha256": record["image"]["sha256"],
                    "image_bytes": record["image"]["bytes"],
                }
                for record in records
            ]
        return {"schema_version": SCHEMA_VERSION, "frames": frames}

    def read_metadata(self, frame_id):
        if not FRAME_ID_RE.fullmatch(frame_id):
            return None
        with self.lock:
            path = self._paths(frame_id)[1]
            try:
                with open(path, "rb") as handle:
                    return handle.read()
            except FileNotFoundError:
                return None

    def read_image(self, frame_id):
        if not FRAME_ID_RE.fullmatch(frame_id):
            return None
        with self.lock:
            path = self._paths(frame_id)[0]
            try:
                with open(path, "rb") as handle:
                    return handle.read()
            except FileNotFoundError:
                return None

    def acknowledge(self, frame_id, digest):
        if not FRAME_ID_RE.fullmatch(frame_id) or not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            return "invalid", 400
        with self.lock:
            if self.recent_acks.get(frame_id) == digest:
                return "acknowledged", 200
            image_path, metadata_path = self._paths(frame_id)
            try:
                with open(metadata_path, "r", encoding="utf-8") as handle:
                    metadata = json.load(handle)
            except FileNotFoundError:
                return "not-found", 404
            if metadata.get("image", {}).get("sha256") != digest:
                return "digest-mismatch", 409
            try:
                os.unlink(image_path)
                os.unlink(metadata_path)
            except FileNotFoundError:
                self.last_error = "incomplete spool pair during acknowledgement"
                return "not-found", 404
            self.recent_acks[frame_id] = digest
            while len(self.recent_acks) > self.recent_ack_limit:
                self.recent_acks.popitem(last=False)
            self._append_event_locked("frame_acknowledged", frame_id=frame_id, image_sha256=digest)
            return "acknowledged", 200

    def register_routes(self, app):
        spool = self

        @app.route("/training_capture/health", methods=["GET"])
        def training_capture_health():
            return jsonify(spool.health())

        @app.route("/training_frames", methods=["GET"])
        def training_frames_index():
            return jsonify(spool.index())

        @app.route("/training_frames/<frame_id>.png", methods=["GET"])
        def training_frame_image(frame_id):
            payload = spool.read_image(frame_id)
            if payload is None:
                return jsonify({"status": "not-found"}), 404
            response = Response(payload, mimetype="image/png")
            response.headers["Cache-Control"] = "no-store"
            return response

        @app.route("/training_frames/<frame_id>", methods=["GET"])
        def training_frame_metadata(frame_id):
            payload = spool.read_metadata(frame_id)
            if payload is None:
                return jsonify({"status": "not-found"}), 404
            response = Response(payload, mimetype="application/json")
            response.headers["Cache-Control"] = "no-store"
            return response

        @app.route("/training_frames/<frame_id>/ack", methods=["POST"])
        def training_frame_ack(frame_id):
            document = request.get_json(silent=True)
            digest = document.get("image_sha256") if isinstance(document, dict) else None
            status, http_status = spool.acknowledge(frame_id, digest)
            return jsonify({"status": status, "frame_id": frame_id}), http_status
