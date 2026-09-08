#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = ROOT / "data" / "source" / "services"
DEFAULT_DISCOVERY = SOURCE_ROOT / "discovery.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "derived" / "military-areas"
SIMPLIFY_TOLERANCE_DEGREES = 0.00005

CATEGORIES = (
    {"id": "dangerous", "label": "Dangerous", "order": 0},
    {"id": "unspecified", "label": "Unspecified", "order": 1},
)
CATEGORY_INDEX = {item["id"]: index for index, item in enumerate(CATEGORIES)}
DANGEROUS_MILITARY_TAGS = {"danger_area", "range"}

OSMCONF = """[general]
closed_ways_are_polygons=aeroway,amenity,boundary,building,craft,geological,historic,landuse,leisure,military,natural,office,place,shop,sport,tourism,highway=platform,public_transport=platform
[multipolygons]
osm_id=yes
attributes=name,type,landuse,military,access
other_tags=no
"""


def public_source(source: dict) -> dict:
    keys = (
        "id", "provider", "distributor", "dataset", "extract_date", "homepage_url",
        "licence_name", "licence_url", "attribution",
    )
    return {key: source[key] for key in keys if source.get(key) is not None}


def extract_military_areas(pbf: Path, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    osmconf = cache_dir / "military-osmconf.ini"
    osmconf.write_text(OSMCONF, encoding="utf-8")
    output = cache_dir / "military-areas.geojsonl"
    if output.is_file() and output.stat().st_size:
        print(f"Using cached military polygons: {output}")
        return output

    output.unlink(missing_ok=True)
    sql = (
        "SELECT CASE WHEN osm_id IS NOT NULL THEN 'r/' || CAST(osm_id AS TEXT) "
        "ELSE 'w/' || CAST(osm_way_id AS TEXT) END AS osm_ref, name, landuse, military, access, "
        f"ST_SimplifyPreserveTopology(geometry, {SIMPLIFY_TOLERANCE_DEGREES}) AS geometry "
        "FROM multipolygons WHERE landuse = 'military' OR military IN ('danger_area','range')"
    )
    command = [
        "ogr2ogr", "-f", "GeoJSONSeq", str(output), str(pbf),
        "-oo", f"CONFIG_FILE={osmconf}", "-dialect", "SQLITE", "-sql", sql, "-lco", "RS=NO",
    ]
    print("Extracting military polygons from OSM", flush=True)
    subprocess.run(command, check=True)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("OSM military polygon extraction produced no data")
    return output


def classify(properties: dict) -> str | None:
    military = str(properties.get("military") or "").strip()
    if military in DANGEROUS_MILITARY_TAGS:
        return "dangerous"
    if properties.get("landuse") == "military":
        return "unspecified"
    return None


def build_payload(path: Path) -> tuple[dict, Counter]:
    features = []
    counts: Counter = Counter()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            feature = json.loads(line)
            properties = feature.get("properties") or {}
            geometry = feature.get("geometry") or {}
            category = classify(properties)
            if category is None:
                continue
            if geometry.get("type") not in {"Polygon", "MultiPolygon"} or not geometry.get("coordinates"):
                raise RuntimeError(f"{path.name}:{line_number}: expected Polygon/MultiPolygon geometry")
            osm_ref = str(properties.get("osm_ref") or "")
            if not osm_ref:
                raise RuntimeError(f"{path.name}:{line_number}: missing OSM reference")
            name = " ".join(str(properties.get("name") or "").split())[:200]
            features.append([CATEGORY_INDEX[category], name, osm_ref, geometry])
            counts[category] += 1
    features.sort(key=lambda item: (item[0], item[1].lower(), item[2]))
    return {"features": features}, counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Goldilocks military-area polygon overlays from the pinned OSM extract.")
    parser.add_argument("--discovery", type=Path, default=DEFAULT_DISCOVERY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    if shutil.which("ogr2ogr") is None:
        raise RuntimeError("ogr2ogr with GDAL OSM/SQLite support is required")
    discovery = json.loads(args.discovery.read_text(encoding="utf-8"))
    if discovery.get("format_version") != 2:
        raise RuntimeError("Run download_service_sources.py first")
    osm_source = (discovery.get("sources") or {}).get("osm")
    if not osm_source or not osm_source.get("file"):
        raise RuntimeError("Service discovery does not contain the OSM source")
    pbf = args.discovery.parent / osm_source["file"]
    if not pbf.is_file():
        raise RuntimeError(f"Missing OSM source file: {pbf}")

    cache_dir = SOURCE_ROOT / "cache" / pbf.stem
    extracted = extract_military_areas(pbf, cache_dir)
    payload, counts = build_payload(extracted)
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    payload_name = "military-areas.json.gz"
    (output / payload_name).write_bytes(compressed)
    categories = [dict(item, count=int(counts[item["id"]])) for item in CATEGORIES]
    manifest = {
        "format_version": 1,
        "kind": "goldilocks-military-areas",
        "generated_at_unix": int(time.time()),
        "categories": categories,
        "source": public_source(osm_source),
        "classification": {
            "dangerous": "military=danger_area or military=range",
            "unspecified": "landuse=military without a dangerous military tag",
        },
        "simplify_tolerance_degrees": SIMPLIFY_TOLERANCE_DEGREES,
        "payload_file": payload_name,
        "payload_encoding": "gzip+json",
        "raw_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "sha256_raw": hashlib.sha256(raw).hexdigest(),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        "Military polygons: "
        + ", ".join(f"{item['id']}={counts[item['id']]:,}" for item in CATEGORIES)
        + f"; payload {len(raw):,} -> {len(compressed):,} bytes gzip"
    )
    print(f"Wrote {output / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
