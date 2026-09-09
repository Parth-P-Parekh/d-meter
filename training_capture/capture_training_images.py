#!/usr/bin/env python3
"""Capture unannotated RGB images from the production Basler camera.

This is deliberately a camera-only utility. It does not import the production
application, start inference, expose a service, or communicate with lighting,
PLC, MES, or SQL hardware.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence


PART_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
EXPECTED_SCHEMA = "1.0"
DEFAULT_CONFIG = Path("/home/sima/training-capture/capture-config.json")
TEMP_SUFFIX = ".capture-tmp"


class CaptureError(RuntimeError):
    """A safe, operator-facing capture failure."""


@dataclasses.dataclass(frozen=True)
class CapturedFrame:
    rgb: bytes
    width: int
    height: int
    pixel_format: str
    camera_readback: Mapping[str, Any]
    received_utc: str


@dataclasses.dataclass(frozen=True)
class ArtifactSet:
    image: Path
    metadata: Path
    checksum: Path
    image_sha256: str


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def utc_text(value: dt.datetime | None = None) -> str:
    value = value or utc_now()
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def filename_timestamp(value: dt.datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S.%fZ")


def validate_part_id(value: str) -> str:
    if not PART_ID_RE.fullmatch(value):
        raise CaptureError(
            "part-id must be 1-128 characters and contain only letters, "
            "digits, dot, underscore, or hyphen"
        )
    return value


def load_config(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CaptureError(f"configuration file does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise CaptureError(f"invalid JSON configuration {path}: {error}") from error
    validate_config(payload)
    return payload


def _require(mapping: Mapping[str, Any], key: str, expected: type) -> Any:
    value = mapping.get(key)
    if not isinstance(value, expected):
        raise CaptureError(f"configuration field {key!r} must be {expected.__name__}")
    return value


def validate_config(config: Mapping[str, Any]) -> None:
    if config.get("schema_version") != EXPECTED_SCHEMA:
        raise CaptureError(f"configuration schema_version must be {EXPECTED_SCHEMA}")
    camera = _require(config, "camera", dict)
    safety = _require(config, "safety", dict)
    storage = _require(config, "storage", dict)
    transfer = _require(config, "transfer", dict)

    for key in ("name", "model", "serial", "pixel_format"):
        if not str(camera.get(key, "")).strip():
            raise CaptureError(f"camera.{key} must be non-empty")
    if camera["pixel_format"] != "RGB":
        raise CaptureError("camera.pixel_format must be RGB for this production contract")
    for key in ("width", "height"):
        if not isinstance(camera.get(key), int) or camera[key] <= 0:
            raise CaptureError(f"camera.{key} must be a positive integer")
    for key in ("offset_x", "offset_y", "warmup_frames"):
        if not isinstance(camera.get(key), int) or camera[key] < 0:
            raise CaptureError(f"camera.{key} must be a non-negative integer")
    for key in ("exposure_us", "gain"):
        value = camera.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
            raise CaptureError(f"camera.{key} must be null or a non-negative number")
    if camera.get("acquisition_mode") not in {"software_trigger", "free_running"}:
        raise CaptureError("camera.acquisition_mode must be software_trigger or free_running")
    if not isinstance(camera.get("allow_free_running_fallback"), bool):
        raise CaptureError("camera.allow_free_running_fallback must be boolean")

    patterns = safety.get("blocked_process_patterns")
    ports = safety.get("blocked_local_ports")
    if not isinstance(patterns, list) or not all(isinstance(item, str) and item for item in patterns):
        raise CaptureError("safety.blocked_process_patterns must be a list of non-empty strings")
    if not isinstance(ports, list) or not all(isinstance(item, int) and 1 <= item <= 65535 for item in ports):
        raise CaptureError("safety.blocked_local_ports must be a list of valid ports")
    if not isinstance(safety.get("minimum_free_bytes"), int) or safety["minimum_free_bytes"] < 0:
        raise CaptureError("safety.minimum_free_bytes must be a non-negative integer")
    if not str(storage.get("local_root", "")).startswith("/"):
        raise CaptureError("storage.local_root must be an absolute board path")
    compression = storage.get("png_compression")
    if not isinstance(compression, int) or not 0 <= compression <= 9:
        raise CaptureError("storage.png_compression must be an integer from 0 to 9")
    for key in ("admin_host", "admin_user", "admin_root"):
        if not str(transfer.get(key, "")).strip():
            raise CaptureError(f"transfer.{key} must be non-empty")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}{TEMP_SUFFIX}")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise CaptureError(f"refusing to overwrite existing artifact: {path}")
        temporary.replace(path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def encode_png(rgb: bytes, width: int, height: int, compression: int) -> bytes:
    expected = width * height * 3
    if len(rgb) != expected:
        raise CaptureError(f"RGB frame has {len(rgb)} bytes; expected exactly {expected}")
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as error:
        raise CaptureError("OpenCV and NumPy are required on the board") from error
    array = np.frombuffer(rgb, dtype=np.uint8).reshape((height, width, 3))
    bgr = array[:, :, ::-1]
    ok, encoded = cv2.imencode(".png", bgr, [cv2.IMWRITE_PNG_COMPRESSION, compression])
    if not ok:
        raise CaptureError("OpenCV failed to encode the frame as PNG")
    return bytes(encoded)


def save_artifacts(
    directory: Path,
    stem: str,
    png: bytes,
    metadata: Mapping[str, Any],
) -> ArtifactSet:
    image_path = directory / f"{stem}.png"
    metadata_path = directory / f"{stem}.json"
    checksum_path = directory / f"{stem}.sha256"
    image_hash = sha256_bytes(png)
    final_metadata = dict(metadata)
    final_metadata["image_sha256"] = image_hash
    final_metadata["copy_status"] = "not_requested"
    atomic_write(image_path, png)
    try:
        atomic_write(
            metadata_path,
            (json.dumps(final_metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        atomic_write(checksum_path, f"{image_hash}  {image_path.name}\n".encode("ascii"))
    except Exception:
        # An image is valuable even if a sidecar write fails. Keep it recoverable.
        raise
    return ArtifactSet(image_path, metadata_path, checksum_path, image_hash)


def update_copy_status(artifact: ArtifactSet, status: str) -> None:
    metadata = json.loads(artifact.metadata.read_text(encoding="utf-8"))
    metadata["copy_status"] = status
    replacement = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")
    # This is an intentional metadata update, implemented as atomic replacement.
    temporary = artifact.metadata.with_name(f".{artifact.metadata.name}.{uuid.uuid4().hex}{TEMP_SUFFIX}")
    try:
        with temporary.open("xb") as handle:
            handle.write(replacement)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, artifact.metadata)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def process_matches(patterns: Sequence[str]) -> list[str]:
    if os.name != "posix" or not shutil.which("pgrep"):
        return []
    matches: list[str] = []
    for pattern in patterns:
        completed = subprocess.run(
            ["pgrep", "-af", pattern],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        for line in completed.stdout.splitlines():
            fields = line.strip().split(maxsplit=1)
            if fields and fields[0].isdigit() and int(fields[0]) != os.getpid():
                matches.append(line.strip())
    return sorted(set(matches))


def open_local_ports(ports: Sequence[int]) -> list[int]:
    open_ports: list[int] = []
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.25)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                open_ports.append(port)
    return open_ports


def run_preflight(config: Mapping[str, Any], local_root: Path) -> None:
    safety = config["safety"]
    matches = process_matches(safety["blocked_process_patterns"])
    if matches:
        raise CaptureError("production camera process appears active: " + " | ".join(matches))
    ports = open_local_ports(safety["blocked_local_ports"])
    if ports:
        raise CaptureError("production service port(s) active: " + ", ".join(map(str, ports)))
    local_root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(local_root).free
    required = safety["minimum_free_bytes"]
    if free < required:
        raise CaptureError(f"insufficient board storage: {free} bytes free; {required} required")


class GstCamera:
    """Small, lazy GStreamer adapter for the production Aravis source."""

    def __init__(self, camera_config: Mapping[str, Any], timeout_seconds: float):
        self.config = camera_config
        self.timeout_ns = int(timeout_seconds * 1_000_000_000)
        self.pipeline: Any = None
        self.source: Any = None
        self.sink: Any = None
        self.Gst: Any = None
        self.mode = str(camera_config["acquisition_mode"])

    def _load_gst(self) -> None:
        try:
            import gi  # type: ignore

            gi.require_version("Gst", "1.0")
            from gi.repository import GObject, Gst  # type: ignore
        except (ImportError, ValueError) as error:
            raise CaptureError("Python GStreamer bindings are unavailable") from error
        Gst.init(None)
        self.Gst = Gst
        self.GObject = GObject

    def pipeline_description(self) -> str:
        camera = self.config
        fields = [
            "aravissrc name=camera_src",
            f"camera-name={camera['name']}",
            f"offset-x={camera['offset_x']}",
            f"offset-y={camera['offset_y']}",
        ]
        if camera.get("exposure_us") is not None:
            fields.extend(["exposure-auto=off", f"exposure={float(camera['exposure_us'])}"])
        if camera.get("gain") is not None:
            fields.extend(["gain-auto=off", f"gain={float(camera['gain'])}"])
        if self.mode == "software_trigger":
            fields.append("trigger=Software")
        source = " ".join(fields)
        caps = (
            f"video/x-raw,format={camera['pixel_format']},"
            f"width={camera['width']},height={camera['height']}"
        )
        return (
            f"{source} ! {caps} ! "
            "appsink name=frame_sink emit-signals=false max-buffers=1 drop=true sync=false"
        )

    def __enter__(self) -> "GstCamera":
        self._load_gst()
        try:
            probe = self.Gst.ElementFactory.make("aravissrc", None)
            if probe is None:
                raise CaptureError("GStreamer aravissrc plugin is unavailable")
            trigger_property = probe.find_property("trigger")
            trigger_signal = self.GObject.signal_lookup("software-trigger", probe.__gtype__)
            supports_software_trigger = trigger_property is not None and trigger_signal != 0
            if self.mode == "software_trigger" and not supports_software_trigger:
                if self.config.get("allow_free_running_fallback"):
                    self.mode = "free_running"
                else:
                    raise CaptureError(
                        "installed aravissrc does not support software trigger; "
                        "configure free_running explicitly"
                    )
            self.pipeline = self.Gst.parse_launch(self.pipeline_description())
            self.source = self.pipeline.get_by_name("camera_src")
            self.sink = self.pipeline.get_by_name("frame_sink")
            if self.source is None or self.sink is None:
                raise CaptureError("GStreamer pipeline is missing required named elements")
            result = self.pipeline.set_state(self.Gst.State.PLAYING)
            if result == self.Gst.StateChangeReturn.FAILURE:
                self._raise_bus_error()
                raise CaptureError("camera pipeline failed to enter PLAYING")
            state_result, _, _ = self.pipeline.get_state(self.timeout_ns)
            if state_result == self.Gst.StateChangeReturn.FAILURE:
                self._raise_bus_error()
                raise CaptureError("camera pipeline failed during startup")
            self._raise_bus_error()
            for _ in range(self.config["warmup_frames"] if self.mode == "free_running" else 0):
                self._pull_sample()
            return self
        except Exception:
            self.close()
            raise

    def _raise_bus_error(self) -> None:
        if self.pipeline is None:
            return
        message = self.pipeline.get_bus().pop_filtered(
            self.Gst.MessageType.ERROR | self.Gst.MessageType.EOS
        )
        if message is None:
            return
        if message.type == self.Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            raise CaptureError(f"camera pipeline error: {error}; {debug or 'no debug detail'}")
        raise CaptureError("camera pipeline ended unexpectedly")

    def readback(self) -> dict[str, Any]:
        if self.source is None:
            raise CaptureError("camera is not open")
        values: dict[str, Any] = {
            "camera_name": self.source.get_property("camera-name"),
            "offset_x": int(self.source.get_property("offset-x")),
            "offset_y": int(self.source.get_property("offset-y")),
            "exposure_us": float(self.source.get_property("exposure")),
            "gain": float(self.source.get_property("gain")),
            "acquisition_mode": self.mode,
        }
        expected_name = self.config["name"]
        if values["camera_name"] != expected_name:
            raise CaptureError(
                f"camera identity mismatch: expected {expected_name!r}, got {values['camera_name']!r}"
            )
        for key in ("offset_x", "offset_y"):
            if values[key] != self.config[key]:
                raise CaptureError(f"camera {key} mismatch: expected {self.config[key]}, got {values[key]}")
        for key in ("exposure_us", "gain"):
            requested = self.config.get(key)
            if requested is not None:
                tolerance = max(0.01, abs(float(requested)) * 0.001)
                if abs(values[key] - float(requested)) > tolerance:
                    raise CaptureError(
                        f"camera {key} mismatch: requested {requested}, read back {values[key]}"
                    )
        return values

    def _pull_sample(self) -> Any:
        sample = self.sink.emit("try-pull-sample", self.timeout_ns)
        if sample is None:
            self._raise_bus_error()
            raise CaptureError("timed out waiting for a camera frame")
        return sample

    def capture(self) -> CapturedFrame:
        if self.mode == "software_trigger":
            self.source.emit("software-trigger")
        sample = self._pull_sample()
        caps = sample.get_caps()
        structure = caps.get_structure(0)
        width = int(structure.get_value("width"))
        height = int(structure.get_value("height"))
        pixel_format = str(structure.get_value("format"))
        expected = self.config
        if (width, height, pixel_format) != (
            expected["width"],
            expected["height"],
            expected["pixel_format"],
        ):
            raise CaptureError(
                "unexpected camera caps: "
                f"{width}x{height} {pixel_format}; expected "
                f"{expected['width']}x{expected['height']} {expected['pixel_format']}"
            )
        buffer = sample.get_buffer()
        ok, map_info = buffer.map(self.Gst.MapFlags.READ)
        if not ok:
            raise CaptureError("failed to map camera buffer")
        try:
            rgb = bytes(map_info.data)
        finally:
            buffer.unmap(map_info)
        required = width * height * 3
        if len(rgb) != required:
            raise CaptureError(
                f"camera buffer has unsupported stride/size: {len(rgb)} bytes; expected {required}"
            )
        return CapturedFrame(rgb, width, height, pixel_format, self.readback(), utc_text())

    def close(self) -> None:
        if self.pipeline is not None and self.Gst is not None:
            with contextlib.suppress(Exception):
                self.pipeline.set_state(self.Gst.State.NULL)
                self.pipeline.get_state(min(self.timeout_ns, 5_000_000_000))
        self.pipeline = self.source = self.sink = None

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


def build_metadata(config: Mapping[str, Any], frame: CapturedFrame, part_id: str, capture_id: str) -> dict[str, Any]:
    camera = config["camera"]
    return {
        "schema_version": EXPECTED_SCHEMA,
        "part_id": part_id,
        "capture_id": capture_id,
        "received_utc": frame.received_utc,
        "camera_name": frame.camera_readback["camera_name"],
        "camera_model": camera["model"],
        "camera_serial": camera["serial"],
        "width": frame.width,
        "height": frame.height,
        "pixel_format": f"{frame.pixel_format}8" if frame.pixel_format == "RGB" else frame.pixel_format,
        "offset_x": frame.camera_readback["offset_x"],
        "offset_y": frame.camera_readback["offset_y"],
        "exposure_us": frame.camera_readback["exposure_us"],
        "gain": frame.camera_readback["gain"],
        "acquisition_mode": frame.camera_readback["acquisition_mode"],
    }


def _safe_remote_value(value: str, label: str) -> str:
    if not value or any(char in value for char in "'\r\n\0"):
        raise CaptureError(f"unsafe character in {label}")
    return value


class AdminTransfer:
    def __init__(self, config: Mapping[str, Any], runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run):
        self.config = config
        self.runner = runner
        self.target = f"{config['admin_user']}@{config['admin_host']}"
        self.ssh_options = [
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={int(config.get('connect_timeout_seconds', 5))}",
        ]

    def _run(self, command: list[str]) -> str:
        completed = self.runner(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip().replace("\n", " ")[-1200:]
            raise CaptureError(f"transfer command failed ({completed.returncode}): {detail}")
        return completed.stdout.strip()

    def _powershell(self, script: str) -> str:
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        return self._run([
            "ssh",
            *self.ssh_options,
            self.target,
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ])

    def preflight(self) -> None:
        self._powershell("$true")

    def copy_and_verify(self, artifact: ArtifactSet, date_folder: str) -> None:
        root = _safe_remote_value(str(self.config["admin_root"]).rstrip("/\\"), "admin_root")
        folder = f"{root}/{date_folder}"
        self._powershell(f"[void][System.IO.Directory]::CreateDirectory('{folder}')")
        for local_path in (artifact.image, artifact.metadata, artifact.checksum):
            self._run(["scp", *self.ssh_options, "-p", str(local_path), f"{self.target}:{folder}/{local_path.name}"])
            remote_path = f"{folder}/{local_path.name}"
            hash_command = (
                "$sha=[System.Security.Cryptography.SHA256]::Create();"
                f"$stream=[System.IO.File]::OpenRead('{remote_path}');"
                "try{[System.BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}"
                "finally{$stream.Dispose();$sha.Dispose()}"
            )
            remote_hash = self._powershell(hash_command).splitlines()[-1].strip().lower()
            local_hash = sha256_file(local_path)
            if remote_hash != local_hash:
                raise CaptureError(
                    f"remote hash mismatch for {local_path.name}: local {local_hash}, remote {remote_hash}"
                )


def capture_images(
    config: Mapping[str, Any],
    part_id: str,
    count: int,
    interval_ms: int,
    timeout_seconds: float,
    local_root: Path,
    require_copy: bool,
    camera_factory: Callable[[Mapping[str, Any], float], Any] = GstCamera,
    transfer_factory: Callable[[Mapping[str, Any]], Any] = AdminTransfer,
    sleep: Callable[[float], None] = time.sleep,
) -> list[ArtifactSet]:
    validate_part_id(part_id)
    if not 1 <= count <= 10_000:
        raise CaptureError("count must be between 1 and 10000")
    if interval_ms < 0:
        raise CaptureError("interval-ms must be non-negative")
    if timeout_seconds <= 0:
        raise CaptureError("timeout-seconds must be positive")
    run_preflight(config, local_root)
    transfer = transfer_factory(config["transfer"]) if require_copy else None
    if transfer is not None:
        transfer.preflight()

    capture_time = utc_now()
    date_folder = capture_time.strftime("%Y-%m-%d")
    output_directory = local_root / date_folder
    output_directory.mkdir(parents=True, exist_ok=True)
    artifacts: list[ArtifactSet] = []
    previous_frame_hash: str | None = None
    camera = camera_factory(config["camera"], timeout_seconds)
    with camera:
        readback = camera.readback()
        print("Camera ready:", json.dumps(readback, sort_keys=True))
        for index in range(count):
            frame = camera.capture()
            raw_hash = sha256_bytes(frame.rgb)
            if raw_hash == previous_frame_hash:
                raise CaptureError("camera returned a byte-identical consecutive frame")
            previous_frame_hash = raw_hash
            capture_id = uuid.uuid4().hex
            received = dt.datetime.fromisoformat(frame.received_utc.replace("Z", "+00:00"))
            stem = f"{part_id}_{filename_timestamp(received)}_{capture_id[:8]}"
            png = encode_png(
                frame.rgb,
                frame.width,
                frame.height,
                config["storage"]["png_compression"],
            )
            metadata = build_metadata(config, frame, part_id, capture_id)
            artifact = save_artifacts(output_directory, stem, png, metadata)
            if transfer is not None:
                transfer.copy_and_verify(artifact, date_folder)
                update_copy_status(artifact, "verified")
                # Re-copy changed metadata and verify it. Image/checksum are unchanged.
                transfer.copy_and_verify(artifact, date_folder)
            artifacts.append(artifact)
            print(f"Captured {index + 1}/{count}: {artifact.image}")
            if index + 1 < count and interval_ms:
                sleep(interval_ms / 1000.0)
    return artifacts


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--part-id", required=True)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--interval-ms", type=int, default=500)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--local-root", type=Path)
    parser.add_argument("--require-copy", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args.config)
        local_root = args.local_root or Path(config["storage"]["local_root"])
        artifacts = capture_images(
            config=config,
            part_id=args.part_id,
            count=args.count,
            interval_ms=args.interval_ms,
            timeout_seconds=args.timeout_seconds,
            local_root=local_root,
            require_copy=args.require_copy,
        )
    except (CaptureError, KeyboardInterrupt) as error:
        message = "interrupted" if isinstance(error, KeyboardInterrupt) else str(error)
        print(f"CAPTURE FAILED: {message}", file=sys.stderr)
        return 1
    print(f"CAPTURE COMPLETE: {len(artifacts)} image(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
