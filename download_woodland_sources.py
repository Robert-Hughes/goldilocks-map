#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = ROOT / "data" / "source" / "woodland"
USER_AGENT = "GoldilocksMap/0.1 (+https://github.com/)"

NFI_ITEM_ID = "5a3efa283e81431b911b5c9872527ca5"
NFI_ITEM_MODIFIED = 1787928470000
ONS_LAD_ITEM_ID = "54b2a8f849f743aea669488cab415c8f"
ONS_LAD_ITEM_MODIFIED = 1777451653000


@dataclass(frozen=True)
class SourceFile:
    id: str
    provider: str
    dataset: str
    filename: str
    url: str
    expected_bytes: int | None = None
    expected_sha256: str | None = None
    expected_zip_member: str | None = None
    sqlite: bool = False


SOURCES = (
    SourceFile(
        id="nfi-gb-2024",
        provider="Forestry Commission",
        dataset="National Forest Inventory GB 2024",
        filename="nfi_gb_2024.shp.zip",
        url=(
            "https://data-forestry.opendata.arcgis.com/api/download/v1/items/"
            f"{NFI_ITEM_ID}/shapefile?layers=0"
        ),
        expected_bytes=707678658,
        expected_sha256="4983e5e04b4a1865cdbe1245f51b32ae9f698d6d723254854614ac6e8ecdd084",
        expected_zip_member="National_Forest_Inventory_GB_2024.shp",
    ),
    SourceFile(
        id="ancient-england-revised",
        provider="Natural England",
        dataset="Ancient Woodland - Revised (England) - Completed Counties",
        filename="ancient_england_revised.gpkg.zip",
        url=(
            "https://environment.data.gov.uk/api/file/download?"
            "fileDataSetId=ae5441f3-ca68-4799-82ed-e1d8847b5c84&"
            "fileName=Ancient_Woodland_Revised_England_Completed_Counties.gpkg.zip"
        ),
        expected_bytes=100326277,
        expected_sha256="9380b3a4215e22f137a5093e499e361d3d4c65394dacd0391ad704e3aa66736d",
        expected_zip_member="Ancient_Woodland_Revised_England_Completed_Counties.gpkg",
    ),
    SourceFile(
        id="ancient-england-legacy",
        provider="Natural England",
        dataset="Ancient Woodland (England)",
        filename="ancient_england_legacy.gpkg.zip",
        url=(
            "https://environment.data.gov.uk/api/file/download?"
            "fileDataSetId=008cdfcf-893b-48f6-937a-3891a2a698c9&"
            "fileName=Ancient_Woodland_England.gpkg.zip"
        ),
        expected_bytes=117391537,
        expected_sha256="530791ecda3dd57a8c472a2d85024b4f0c8d13a677e3c2f0797bb233ed53b4c9",
        expected_zip_member="Ancient_Woodland_England.gpkg",
    ),
    SourceFile(
        id="ancient-wales-2021",
        provider="Natural Resources Wales",
        dataset="Ancient Woodland Inventory 2021",
        filename="ancient_wales.gpkg",
        url=(
            "https://datamap.gov.wales/geoserver/ows?outputFormat=gpkg&request=GetFeature&"
            "service=WFS&srs=EPSG%3A27700&"
            "typename=inspire-nrw%3ANRW_ANCIENT_WOODLAND_INVENTORY_2021&version=1.0.0"
        ),
        expected_bytes=118648832,
        expected_sha256="5ac1178a6b57d571674cc77c92417ac68053fd16863164d8c46be25eecccb883",
        sqlite=True,
    ),
    SourceFile(
        id="ancient-scotland",
        provider="NatureScot",
        dataset="Ancient Woodland Inventory (Scotland)",
        filename="ancient_scotland.gpkg.zip",
        url="https://gis-downloads.nature.scot/AWI_SCOTLAND_GPKG_27700.zip",
        expected_bytes=15175269,
        expected_sha256="9865c929a5f14e4d108da8e435e1c04a307ec41c1ff6df9051fde7a081d207ed",
        expected_zip_member="AWI_SCOTLAND.gpkg",
    ),
    SourceFile(
        id="ons-lad-2025",
        provider="Office for National Statistics",
        dataset="Local Authority Districts (December 2025) Boundaries UK BGC",
        filename="ons_lad_dec_2025_bgc.shp.zip",
        url=(
            "https://open-geography-portalx-ons.hub.arcgis.com/api/download/v1/items/"
            f"{ONS_LAD_ITEM_ID}/shapefile?layers=0"
        ),
        expected_bytes=4872982,
        expected_sha256="6df4eafce016aea7db1d7e04de990e78679acdf56e6532586c865b7b633896a6",
        expected_zip_member="LAD_DEC_2025_UK_BGC.shp",
    ),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def validate_arcgis_pin(item_id: str, expected_modified: int, label: str) -> dict:
    metadata = fetch_json(f"https://www.arcgis.com/sharing/rest/content/items/{item_id}?f=json")
    actual = int(metadata.get("modified", -1))
    if actual != expected_modified:
        raise RuntimeError(
            f"{label} ArcGIS item changed: modified={actual}, expected pinned {expected_modified}. "
            "Review the new release before updating the Goldilocks source pin."
        )
    return {
        "item_id": item_id,
        "title": metadata.get("title"),
        "modified": actual,
        "url": metadata.get("url"),
    }


def validate_source(path: Path, source: SourceFile, *, enforce_exact: bool = True) -> dict:
    if not path.is_file():
        raise RuntimeError(f"Missing source file: {path}")
    size = path.stat().st_size
    if enforce_exact and source.expected_bytes is not None and size != source.expected_bytes:
        raise RuntimeError(f"{source.id}: {size:,} bytes; expected {source.expected_bytes:,}")
    digest = sha256_file(path)
    if source.expected_sha256 and digest != source.expected_sha256:
        raise RuntimeError(
            f"{source.id}: SHA-256 {digest} does not match pinned {source.expected_sha256}"
        )
    if source.expected_zip_member:
        with zipfile.ZipFile(path) as archive:
            names = {Path(name).name for name in archive.namelist()}
            if source.expected_zip_member not in names:
                raise RuntimeError(
                    f"{source.id}: archive does not contain expected {source.expected_zip_member}"
                )
    if source.sqlite:
        with path.open("rb") as handle:
            if handle.read(16) != b"SQLite format 3\x00":
                raise RuntimeError(f"{source.id}: downloaded file is not a GeoPackage/SQLite database")
    return {
        "id": source.id,
        "provider": source.provider,
        "dataset": source.dataset,
        "file": source.filename,
        "url": source.url,
        "bytes": size,
        "sha256": digest,
    }


def download(source: SourceFile, *, force: bool) -> dict:
    SOURCE_ROOT.mkdir(parents=True, exist_ok=True)
    target = SOURCE_ROOT / source.filename
    if target.exists() and not force:
        record = validate_source(target, source)
        record["cached"] = True
        return record

    target.unlink(missing_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    part.unlink(missing_ok=True)
    request = urllib.request.Request(source.url, headers={"User-Agent": USER_AGENT})
    print(f"Downloading {source.id}...", flush=True)
    with urllib.request.urlopen(request, timeout=120) as response, part.open("wb") as handle:
        while True:
            block = response.read(4 * 1024 * 1024)
            if not block:
                break
            handle.write(block)
    part.replace(target)
    record = validate_source(target, source)
    record["cached"] = False
    return record


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download the pinned Forestry Commission and national ancient-woodland vector sources."
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--verify", action="store_true", help="re-hash all cached sources")
    args = parser.parse_args()

    pins = {
        "nfi": validate_arcgis_pin(NFI_ITEM_ID, NFI_ITEM_MODIFIED, "NFI GB 2024"),
        "ons_lad": validate_arcgis_pin(ONS_LAD_ITEM_ID, ONS_LAD_ITEM_MODIFIED, "ONS LAD December 2025"),
    }
    print("Pinned source metadata is unchanged.")
    for source in SOURCES:
        expected = f"{source.expected_bytes:,} bytes" if source.expected_bytes is not None else "size unpinned"
        print(f"  {source.id:26s} {expected}  {source.url}")
    if args.dry_run:
        return 0

    records = []
    for source in SOURCES:
        target = SOURCE_ROOT / source.filename
        if target.exists() and not args.force and not args.verify:
            # Pinned woodland inputs are deliberately immutable for a reproducible
            # build, so ordinary runs re-check size, structure and SHA-256.
            record = validate_source(target, source, enforce_exact=True)
            record["cached"] = True
        else:
            record = download(source, force=args.force)
        records.append(record)
        state = "cached" if record.get("cached") else "downloaded"
        print(f"{state:10s} {source.id:26s} {record['bytes']:,} bytes")

    discovery = {
        "format_version": 1,
        "dataset": "Goldilocks woodland source set",
        "retrieved_at_unix": int(time.time()),
        "pins": pins,
        "files": records,
    }
    path = SOURCE_ROOT / "discovery.json"
    path.write_text(json.dumps(discovery, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
