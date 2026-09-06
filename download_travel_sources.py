#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PINS_PATH = ROOT / "travel-time-sources.json"
SOURCE_ROOT = ROOT / "data" / "source" / "travel-time"
DISCOVERY_PATH = SOURCE_ROOT / "discovery.json"
USER_AGENT = "GoldilocksMap/0.1 (+https://github.com/)"


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def md5_file(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_file(url: str, target: Path, *, force: bool = False) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and not force:
        return
    part = target.with_suffix(target.suffix + ".part")
    part.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    print(f"Downloading {url}", flush=True)
    with urllib.request.urlopen(request, timeout=180) as response, part.open("wb") as handle:
        shutil.copyfileobj(response, handle, length=4 * 1024 * 1024)
    part.replace(target)


def validate_hash(path: Path, *, sha256: str | None = None, md5: str | None = None) -> None:
    if sha256 is not None:
        actual = sha256_file(path)
        if actual.lower() != sha256.lower():
            raise RuntimeError(f"SHA-256 mismatch for {path}: {actual} != {sha256}")
    if md5 is not None:
        actual = md5_file(path)
        if actual.lower() != md5.lower():
            raise RuntimeError(f"MD5 mismatch for {path}: {actual} != {md5}")


def os_download(pins: dict[str, Any], *, force: bool, dry_run: bool) -> dict[str, Any]:
    product_url = f"https://api.os.uk/downloads/v1/products/{pins['product_id']}"
    product = fetch_json(product_url)
    if product.get("version") != pins["version"]:
        raise RuntimeError(
            f"{pins['product_id']} current version is {product.get('version')!r}; expected pinned {pins['version']!r}"
        )
    downloads = fetch_json(pins["downloads_api"])
    matches = [
        item for item in downloads
        if item.get("area") == pins["area"] and item.get("format") == pins["format"]
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one {pins['product_id']} {pins['area']} {pins['format']} download; found {len(matches)}"
        )
    item = matches[0]
    if item.get("fileName") != pins["file_name"] or item.get("md5", "").lower() != pins["md5"].lower():
        raise RuntimeError(f"Pinned {pins['product_id']} file metadata no longer matches the OS Downloads API")
    query = {"area": pins["area"], "format": pins["format"]}
    url = pins["downloads_api"] + "?" + urllib.parse.urlencode(query) + "&redirect"
    folder = SOURCE_ROOT / pins["product_id"] / pins["version"]
    target = folder / pins["file_name"]
    print(f"{pins['product_id']} {pins['version']}: {target.name} ({int(item['size']):,} bytes)")
    if not dry_run:
        download_file(url, target, force=force)
        if target.stat().st_size != int(item["size"]):
            raise RuntimeError(f"Unexpected file size for {target}")
        validate_hash(target, md5=pins["md5"])
        extract = folder / "extracted"
        marker = extract / ".complete"
        if force or not marker.is_file():
            if extract.exists():
                shutil.rmtree(extract)
            extract.mkdir(parents=True)
            print(f"Extracting {target.name}", flush=True)
            with zipfile.ZipFile(target) as archive:
                archive.extractall(extract)
            marker.write_text(f"{pins['version']}\n", encoding="utf-8")
    return {
        "product_id": pins["product_id"],
        "version": pins["version"],
        "file": str(target.relative_to(SOURCE_ROOT)),
        "file_name": target.name,
        "bytes": int(item["size"]),
        "md5": pins["md5"],
        "download_url": url,
        "product_url": pins["product_url"],
        "documentation_url": product.get("documentationUrl"),
    }


def download_dft(pins: dict[str, Any], *, force: bool, dry_run: bool) -> dict[str, Any]:
    folder = SOURCE_ROOT / "dft-local-a"
    result: dict[str, Any] = {"dataset_page": pins["dataset_page"], "release_note": pins["release_note"], "files": {}}
    for country, spec in pins["files"].items():
        target = folder / spec["name"]
        print(f"DfT {country}: {spec['url']}")
        if not dry_run:
            download_file(spec["url"], target, force=force)
            validate_hash(target, sha256=spec["sha256"])
        result["files"][country] = {
            "file": str(target.relative_to(SOURCE_ROOT)),
            "url": spec["url"],
            "sha256": spec["sha256"],
            "bytes": target.stat().st_size if target.exists() else None,
        }
    return result


def download_ons_lad(pins: dict[str, Any], *, force: bool, dry_run: bool) -> dict[str, Any]:
    target = SOURCE_ROOT / "ons-lad" / "lad_dec_2025_uk_bgc.geojson"
    params = {
        "where": "1=1",
        "outFields": "LAD25CD,LAD25NM",
        "returnGeometry": "true",
        "outSR": "27700",
        "f": "geojson",
    }
    url = pins["feature_service"] + "?" + urllib.parse.urlencode(params)
    print(f"ONS LAD December 2025: {url}")
    if not dry_run and (force or not target.is_file()):
        download_file(url, target, force=True)
    if not dry_run:
        document = json.loads(target.read_text(encoding="utf-8"))
        features = document.get("features", [])
        if len(features) < 300:
            raise RuntimeError(f"ONS LAD download appears incomplete: {len(features)} features")
    return {
        "release": pins["release"],
        "dataset": pins["dataset"],
        "file": str(target.relative_to(SOURCE_ROOT)),
        "url": url,
        "homepage": pins["homepage"],
        "sha256": sha256_file(target) if target.exists() else None,
        "bytes": target.stat().st_size if target.exists() else None,
    }


def download_nh_aggregation(pins: dict[str, Any], aggregation: str, *, force: bool, dry_run: bool) -> dict[str, Any]:
    slug = aggregation.lower().replace(" ", "_")
    target = SOURCE_ROOT / "national-highways" / "2024-25" / f"{slug}.json"
    if target.is_file() and not force:
        document = json.loads(target.read_text(encoding="utf-8"))
        return {
            "aggregation": aggregation,
            "file": str(target.relative_to(SOURCE_ROOT)),
            "feature_count": len(document.get("features", [])),
            "sha256": sha256_file(target),
            "bytes": target.stat().st_size,
        }
    where = f"reportingperiod='{pins['reporting_period']}' AND aggregation='{aggregation}'"
    print(f"National Highways {aggregation}: {where}")
    if dry_run:
        return {"aggregation": aggregation, "file": str(target.relative_to(SOURCE_ROOT))}
    features: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = {
            "where": where,
            "outFields": (
                "linkid,roadnumber,averagespeedmph,averagejourneytime,averagetraveltime,"
                "lengthmetres,direction,linklocationname,roadclass"
            ),
            "returnGeometry": "true",
            "outSR": "27700",
            "resultOffset": str(offset),
            "resultRecordCount": "1000",
            "f": "json",
        }
        url = pins["feature_service"] + "?" + urllib.parse.urlencode(params)
        document = fetch_json(url)
        if "error" in document:
            raise RuntimeError(f"National Highways ArcGIS error: {document['error']}")
        batch = document.get("features", [])
        features.extend(batch)
        print(f"  {aggregation}: {len(features):,} features", flush=True)
        if not document.get("exceededTransferLimit") or len(batch) < 1000:
            break
        offset += len(batch)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "source": pins["feature_service"],
                "reporting_period": pins["reporting_period"],
                "aggregation": aggregation,
                "features": features,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return {
        "aggregation": aggregation,
        "file": str(target.relative_to(SOURCE_ROOT)),
        "feature_count": len(features),
        "sha256": sha256_file(target),
        "bytes": target.stat().st_size,
    }


def download_national_highways(pins: dict[str, Any], *, force: bool, dry_run: bool) -> dict[str, Any]:
    aggregations = [
        download_nh_aggregation(pins, aggregation, force=force, dry_run=dry_run)
        for aggregation in pins["aggregations"]
    ]
    return {
        "dataset": pins["dataset"],
        "reporting_period": pins["reporting_period"].strip(),
        "feature_service": pins["feature_service"],
        "homepage": pins["homepage"],
        "aggregations": aggregations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Download the pinned open-data inputs for Goldilocks travel-time metrics.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    pins = json.loads(PINS_PATH.read_text(encoding="utf-8"))
    if pins.get("format_version") != 1:
        raise RuntimeError(f"Unsupported travel source pin format in {PINS_PATH}")

    discovery = {
        "format_version": 1,
        "retrieved_at_unix": int(time.time()),
        "source_pins": str(PINS_PATH.relative_to(ROOT)),
        "os_open_roads": os_download(pins["os_open_roads"], force=args.force, dry_run=args.dry_run),
        "os_open_built_up_areas": os_download(
            pins["os_open_built_up_areas"], force=args.force, dry_run=args.dry_run
        ),
        "dft_local_a": download_dft(pins["dft_local_a"], force=args.force, dry_run=args.dry_run),
        "ons_lad": download_ons_lad(pins["ons_lad"], force=args.force, dry_run=args.dry_run),
        "national_highways": download_national_highways(
            pins["national_highways"], force=args.force, dry_run=args.dry_run
        ),
        "licence": pins["licence"],
    }
    if args.dry_run:
        return 0
    SOURCE_ROOT.mkdir(parents=True, exist_ok=True)
    DISCOVERY_PATH.write_text(json.dumps(discovery, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {DISCOVERY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
