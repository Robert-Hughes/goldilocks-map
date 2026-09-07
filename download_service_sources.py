#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
SOURCE_DIR = ROOT / "data" / "source" / "services"
DISCOVERY_PATH = SOURCE_DIR / "discovery.json"

PINNED_EXTRACT_DATE = "2026-09-06"
PINNED_FILENAME = "great-britain-260906.osm.pbf"
PINNED_URL = "https://download.geofabrik.de/europe/great-britain-260906.osm.pbf"
PINNED_BYTES = 2_169_620_686
PINNED_MD5 = "a63b85616bfb5e4998fcf4ba5c2dcae3"
CHUNK_SIZE = 4 * 1024 * 1024


def md5_file(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - source publishes MD5 for integrity pinning
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_resumable(url: str, destination: Path) -> None:
    part = destination.with_suffix(destination.suffix + ".part")
    existing = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "Goldilocks-Map/0.1 (+https://github.com/Robert-Hughes/goldilocks-map)"}
    if existing:
        headers["Range"] = f"bytes={existing}-"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=120) as response:
        status = getattr(response, "status", response.getcode())
        if existing and status != 206:
            existing = 0
            part.unlink(missing_ok=True)
        mode = "ab" if existing else "wb"
        with part.open(mode) as handle:
            shutil.copyfileobj(response, handle, length=CHUNK_SIZE)
    if part.stat().st_size != PINNED_BYTES:
        raise RuntimeError(f"Downloaded {part.stat().st_size:,} bytes; expected {PINNED_BYTES:,}")
    digest = md5_file(part)
    if digest != PINNED_MD5:
        raise RuntimeError(f"Downloaded PBF MD5 {digest} does not match pinned {PINNED_MD5}")
    part.replace(destination)


def write_discovery(path: Path) -> None:
    discovery = {
        "format_version": 1,
        "dataset": "Goldilocks service POI source set",
        "generated_at_unix": int(time.time()),
        "source": {
            "provider": "OpenStreetMap contributors",
            "distributor": "Geofabrik GmbH",
            "dataset": "OpenStreetMap Great Britain extract",
            "extract_date": PINNED_EXTRACT_DATE,
            "file": PINNED_FILENAME,
            "url": PINNED_URL,
            "bytes": PINNED_BYTES,
            "md5": PINNED_MD5,
        },
    }
    DISCOVERY_PATH.write_text(json.dumps(discovery, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Download the pinned Geofabrik OpenStreetMap Great Britain extract used for service POIs.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true", help="recompute MD5 for an existing cached PBF")
    args = parser.parse_args()

    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    destination = SOURCE_DIR / PINNED_FILENAME
    print(f"Pinned OSM GB extract: {PINNED_EXTRACT_DATE} · {PINNED_BYTES / (1024**3):.2f} GiB")
    print(PINNED_URL)
    if args.dry_run:
        return 0

    if destination.is_file() and destination.stat().st_size == PINNED_BYTES:
        if args.verify:
            digest = md5_file(destination)
            if digest != PINNED_MD5:
                raise RuntimeError(f"Cached PBF MD5 {digest} does not match pinned {PINNED_MD5}")
            print(f"Verified {destination.name}: {digest}")
        else:
            print(f"Using cached {destination} ({destination.stat().st_size:,} bytes)")
    else:
        download_resumable(PINNED_URL, destination)
        print(f"Downloaded and verified {destination}")

    write_discovery(destination)
    print(f"Wrote {DISCOVERY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
