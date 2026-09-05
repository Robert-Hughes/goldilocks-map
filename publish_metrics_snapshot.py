#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = ROOT / "data" / "derived" / "goldilocks-metrics" / "manifest.json"
DEFAULT_DESTINATION = ROOT / "published-data" / "goldilocks-metrics"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy the validated assembled Goldilocks metric bundle into the tracked public snapshot directory."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    destination = args.destination.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != 3 or manifest.get("kind") != "goldilocks-bundle":
        raise SystemExit(
            f"Unsupported metric bundle: version={manifest.get('format_version')!r}, kind={manifest.get('kind')!r}"
        )

    metrics = manifest.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise SystemExit("Manifest does not contain any metrics")

    blob_names: list[str] = []
    for metric in metrics:
        blob_name = metric.get("blob_file")
        if not isinstance(blob_name, str) or Path(blob_name).name != blob_name:
            raise SystemExit(f"Invalid blob filename for metric {metric.get('id')!r}: {blob_name!r}")
        blob_path = manifest_path.parent / blob_name
        if not blob_path.is_file():
            raise SystemExit(f"Referenced metric blob is missing: {blob_path}")
        expected_size = metric.get("compressed_bytes")
        if expected_size is not None and blob_path.stat().st_size != int(expected_size):
            raise SystemExit(
                f"Metric blob {blob_name} size {blob_path.stat().st_size} does not match manifest {expected_size}"
            )
        blob_names.append(blob_name)

    destination.mkdir(parents=True, exist_ok=True)
    keep = {"manifest.json", *blob_names}
    for existing in destination.iterdir():
        if existing.is_file() and existing.name not in keep:
            existing.unlink()

    shutil.copy2(manifest_path, destination / "manifest.json")
    for blob_name in blob_names:
        shutil.copy2(manifest_path.parent / blob_name, destination / blob_name)

    total_bytes = sum((destination / name).stat().st_size for name in keep)
    print(f"Published metric snapshot: {len(blob_names)} metric blobs, {total_bytes:,} bytes")
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
