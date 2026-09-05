#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
import zipfile
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = ROOT / "data" / "source" / "os-terrain-50"
PRODUCT_URL = "https://api.os.uk/downloads/v1/products/Terrain50"
DOWNLOADS_URL = PRODUCT_URL + "/downloads"
GRID_FORMAT = "ASCII Grid and GML (Grid)"
PINNED_VERSION = "2026-07"
USER_AGENT = "GoldilocksMap/0.1 (+https://github.com/)"


def fetch_json(url: str) -> object:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def md5_file(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def discover_grid_download(expected_version: str) -> tuple[dict, dict]:
    product = fetch_json(PRODUCT_URL)
    if not isinstance(product, dict):
        raise RuntimeError("OS Downloads API returned an invalid Terrain 50 product document")
    version = product.get("version")
    if version != expected_version:
        raise RuntimeError(
            f"OS Terrain 50 current release is {version!r}; this downloader is pinned to {expected_version!r}. "
            "Review the new release before intentionally updating the pin."
        )
    downloads = fetch_json(DOWNLOADS_URL)
    if not isinstance(downloads, list):
        raise RuntimeError("OS Downloads API returned an invalid Terrain 50 downloads document")
    matches = [
        item for item in downloads
        if isinstance(item, dict) and item.get("format") == GRID_FORMAT and item.get("area") == "GB"
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one GB {GRID_FORMAT!r} download; found {len(matches)}")
    return product, matches[0]


def direct_download_url() -> str:
    query = urlencode({"area": "GB", "format": GRID_FORMAT})
    return f"{DOWNLOADS_URL}?{query}&redirect"


def validate_archive(path: Path, *, expected_size: int, expected_md5: str) -> None:
    if path.stat().st_size != expected_size:
        raise RuntimeError(
            f"OS Terrain 50 archive size {path.stat().st_size:,} does not match API metadata {expected_size:,}"
        )
    digest = md5_file(path)
    if digest.lower() != expected_md5.lower():
        raise RuntimeError(f"OS Terrain 50 archive MD5 {digest} does not match API metadata {expected_md5}")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if not any(name.lower().endswith(".zip") for name in names):
            raise RuntimeError("OS Terrain 50 archive contains no nested tile archives")


def download_archive(url: str, target: Path, *, expected_size: int, expected_md5: str, force: bool) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and not force:
        validate_archive(target, expected_size=expected_size, expected_md5=expected_md5)
        return True
    target.unlink(missing_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    part.unlink(missing_ok=True)
    request = Request(url, headers={"User-Agent": USER_AGENT})
    print(f"Downloading {expected_size:,} bytes from OS Downloads API...", flush=True)
    with urlopen(request, timeout=120) as response, part.open("wb") as handle:
        shutil.copyfileobj(response, handle, length=4 * 1024 * 1024)
    part.replace(target)
    validate_archive(target, expected_size=expected_size, expected_md5=expected_md5)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover and download the pinned OS Terrain 50 GB 50 m ASCII grid release.")
    parser.add_argument("--version", default=PINNED_VERSION, help=f"expected OS product version (default: {PINNED_VERSION})")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    product, download = discover_grid_download(args.version)
    expected_size = int(download["size"])
    expected_md5 = str(download["md5"])
    filename = str(download["fileName"])
    url = direct_download_url()
    target = SOURCE_ROOT / args.version / filename

    print(f"OS Terrain 50 release: {args.version}")
    print(f"Format: {GRID_FORMAT}")
    print(f"Archive: {filename} ({expected_size:,} bytes; MD5 {expected_md5})")
    print(f"Download: {url}")
    if args.dry_run:
        return 0

    cached = download_archive(url, target, expected_size=expected_size, expected_md5=expected_md5, force=args.force)
    status = "Using cached" if cached else "Downloaded"
    print(f"{status} {target} ({target.stat().st_size:,} bytes)")

    discovery = {
        "format_version": 1,
        "provider": "Ordnance Survey",
        "dataset": "OS Terrain 50",
        "product_id": product.get("id"),
        "version": args.version,
        "retrieved_at_unix": int(time.time()),
        "product_url": PRODUCT_URL,
        "documentation_url": product.get("documentationUrl"),
        "format": GRID_FORMAT,
        "area": "GB",
        "file": str(target.relative_to(SOURCE_ROOT)),
        "file_name": filename,
        "bytes": expected_size,
        "md5": expected_md5,
        "download_url": url,
    }
    SOURCE_ROOT.mkdir(parents=True, exist_ok=True)
    discovery_path = SOURCE_ROOT / "discovery.json"
    discovery_path.write_text(json.dumps(discovery, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {discovery_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
