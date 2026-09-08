#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = ROOT / "data" / "derived" / "military-areas" / "manifest.json"
DEFAULT_DESTINATION = ROOT / "published-data" / "goldilocks-military-areas"


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy the validated military-area bundle into the tracked public snapshot directory.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    destination = args.destination.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1 or manifest.get("kind") != "goldilocks-military-areas":
        raise SystemExit("Unsupported military-area bundle")
    payload_name = manifest.get("payload_file")
    if not isinstance(payload_name, str) or Path(payload_name).name != payload_name:
        raise SystemExit(f"Invalid military-area payload filename: {payload_name!r}")
    payload_path = manifest_path.parent / payload_name
    if not payload_path.is_file():
        raise SystemExit(f"Military-area payload is missing: {payload_path}")
    compressed = payload_path.read_bytes()
    if len(compressed) != int(manifest.get("compressed_bytes", -1)):
        raise SystemExit("Military-area payload compressed size does not match manifest")
    raw = gzip.decompress(compressed)
    if len(raw) != int(manifest.get("raw_bytes", -1)):
        raise SystemExit("Military-area payload raw size does not match manifest")
    if hashlib.sha256(raw).hexdigest() != manifest.get("sha256_raw"):
        raise SystemExit("Military-area payload failed raw SHA-256 validation")

    destination.mkdir(parents=True, exist_ok=True)
    for existing in destination.iterdir():
        if existing.is_file() and existing.name not in {"manifest.json", payload_name}:
            existing.unlink()
    shutil.copy2(manifest_path, destination / "manifest.json")
    shutil.copy2(payload_path, destination / payload_name)
    print(f"Published military-area snapshot: {payload_name}, {len(compressed):,} bytes")
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
