#!/usr/bin/env python3
"""Read camera geometry and list optical-control GenICam feature names."""

from __future__ import annotations

import ctypes
import re
import sys
import xml.etree.ElementTree as ET


CAMERA = b"Basler-a2A4504-5gcBAS-40735904"
LIBRARY = "/lib/aarch64-linux-gnu/libaravis-0.8.so.0"


def main() -> int:
    library = ctypes.CDLL(LIBRARY)
    library.arv_camera_new.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    library.arv_camera_new.restype = ctypes.c_void_p
    library.arv_camera_get_device.argtypes = [ctypes.c_void_p]
    library.arv_camera_get_device.restype = ctypes.c_void_p
    library.arv_device_get_genicam_xml.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
    library.arv_device_get_genicam_xml.restype = ctypes.c_void_p
    library.arv_device_get_integer_feature_value.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    library.arv_device_get_integer_feature_value.restype = ctypes.c_int64

    error = ctypes.c_void_p()
    camera = library.arv_camera_new(CAMERA, ctypes.byref(error))
    if not camera:
        print(f"camera open failed; GError pointer={error.value}", file=sys.stderr)
        return 1
    device = library.arv_camera_get_device(camera)
    if not device:
        print("camera device is unavailable", file=sys.stderr)
        return 1

    print("geometry:")
    for name in ("SensorWidth", "SensorHeight", "Width", "Height", "OffsetX", "OffsetY"):
        feature_error = ctypes.c_void_p()
        value = library.arv_device_get_integer_feature_value(device, name.encode(), ctypes.byref(feature_error))
        print(f"  {name}={value}" if not feature_error.value else f"  {name}=<read error>")

    size = ctypes.c_size_t()
    address = library.arv_device_get_genicam_xml(device, ctypes.byref(size))
    if not address or not size.value:
        print("GenICam XML is unavailable", file=sys.stderr)
        return 1
    root = ET.fromstring(ctypes.string_at(address, size.value))
    matcher = re.compile(r"focus|lens|zoom|iris|aperture|motor|center[xy]", re.IGNORECASE)
    features: list[tuple[str, str]] = []
    for node in root.iter():
        name = node.attrib.get("Name", "")
        if name and matcher.search(name):
            tag = node.tag.rsplit("}", 1)[-1]
            features.append((name, tag))
    print("optical/centering feature names:")
    if features:
        for name, tag in sorted(set(features)):
            print(f"  {name} ({tag})")
    else:
        print("  <none>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
