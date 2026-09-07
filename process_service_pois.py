#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import shutil
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = ROOT / "data" / "source" / "services"
DEFAULT_DISCOVERY = SOURCE_ROOT / "discovery.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "derived" / "services"

MIN_ZOOM = 12
BUCKET_SCALE = 4  # 0.25 degree WGS84 buckets
COORDINATE_SCALE = 100_000
DEDUP_DISTANCE_M = 25.0

CATEGORIES = (
    {"id": "supermarket", "label": "Supermarkets", "order": 0},
    {"id": "post_office", "label": "Post offices", "order": 1},
    {"id": "pharmacy", "label": "Pharmacies", "order": 2},
)
CATEGORY_INDEX = {item["id"]: index for index, item in enumerate(CATEGORIES)}

OSMCONF = """[general]
closed_ways_are_polygons=aeroway,amenity,boundary,building,craft,geological,historic,landuse,leisure,military,natural,office,place,shop,sport,tourism,highway=platform,public_transport=platform

[points]
osm_id=yes
attributes=name,amenity,shop,brand,operator
unsignificant=created_by,converted_by,source,time,ele,attribution
ignore=created_by,converted_by,source,time,ele,note,todo,openGeoDB:,fixme,FIXME
other_tags=no

[lines]
osm_id=yes
attributes=name,highway
other_tags=no

[multipolygons]
osm_id=yes
attributes=name,type,amenity,shop,brand,operator
other_tags=no

[multilinestrings]
osm_id=yes
attributes=name,type
other_tags=no

[other_relations]
osm_id=yes
attributes=name,type
other_tags=no
"""


class Poi(dict):
    pass


def run_ogr_extract(pbf: Path, osmconf: Path, output: Path, *, polygons: bool) -> None:
    if polygons:
        sql = (
            "SELECT CASE WHEN osm_id IS NOT NULL THEN 'r/' || CAST(osm_id AS TEXT) "
            "ELSE 'w/' || CAST(osm_way_id AS TEXT) END AS osm_ref, name, amenity, shop, brand, operator, "
            "ST_PointOnSurface(geometry) AS geometry FROM multipolygons "
            "WHERE shop = 'supermarket' OR amenity IN ('post_office','pharmacy')"
        )
    else:
        sql = (
            "SELECT 'n/' || CAST(osm_id AS TEXT) AS osm_ref, name, amenity, shop, brand, operator, geometry "
            "FROM points WHERE shop = 'supermarket' OR amenity IN ('post_office','pharmacy')"
        )
    command = [
        "ogr2ogr",
        "-f",
        "GeoJSONSeq",
        str(output),
        str(pbf),
        "-oo",
        f"CONFIG_FILE={osmconf}",
        "-dialect",
        "SQLITE",
        "-sql",
        sql,
        "-lco",
        "RS=NO",
    ]
    print("Extracting", "area POIs" if polygons else "node POIs", flush=True)
    subprocess.run(command, check=True)


def category_ids(properties: dict) -> list[str]:
    result: list[str] = []
    if properties.get("shop") == "supermarket":
        result.append("supermarket")
    amenity = properties.get("amenity")
    if amenity == "post_office":
        result.append("post_office")
    if amenity == "pharmacy":
        result.append("pharmacy")
    return result


def display_name(properties: dict, category_id: str) -> str:
    for key in ("name", "brand", "operator"):
        value = properties.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:200]
    return {
        "supermarket": "Unnamed supermarket",
        "post_office": "Unnamed post office",
        "pharmacy": "Unnamed pharmacy",
    }[category_id]


def load_geojsonseq(path: Path, *, source_rank: int) -> list[Poi]:
    result: list[Poi] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            feature = json.loads(line)
            geometry = feature.get("geometry") or {}
            coordinates = geometry.get("coordinates")
            if geometry.get("type") != "Point" or not isinstance(coordinates, list) or len(coordinates) < 2:
                raise RuntimeError(f"{path.name}:{line_number}: expected Point geometry")
            lon = float(coordinates[0])
            lat = float(coordinates[1])
            if not (-10.0 <= lon <= 4.0 and 49.0 <= lat <= 62.0):
                raise RuntimeError(f"{path.name}:{line_number}: implausible Great Britain coordinate {lat},{lon}")
            properties = feature.get("properties") or {}
            osm_ref = str(properties.get("osm_ref") or "").strip()
            if not osm_ref or osm_ref.endswith("/None"):
                raise RuntimeError(f"{path.name}:{line_number}: missing OSM reference")
            for category_id in category_ids(properties):
                result.append(
                    Poi(
                        category_id=category_id,
                        lat=lat,
                        lon=lon,
                        name=display_name(properties, category_id),
                        osm_ref=osm_ref,
                        source_rank=source_rank,
                    )
                )
    return result


def normalized_name(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def approximate_distance_m(left: Poi, right: Poi) -> float:
    mean_lat = math.radians((float(left["lat"]) + float(right["lat"])) / 2.0)
    dx = (float(left["lon"]) - float(right["lon"])) * 111_320.0 * math.cos(mean_lat)
    dy = (float(left["lat"]) - float(right["lat"])) * 110_540.0
    return math.hypot(dx, dy)


def deduplicate(pois: list[Poi]) -> tuple[list[Poi], int]:
    # Node POIs are preferred to area representative points when a separately mapped
    # node with the same category/name is effectively coincident with the area.
    pois.sort(key=lambda item: (int(item["source_rank"]), str(item["category_id"]), str(item["osm_ref"])))
    cells: dict[tuple[str, str, int, int], list[Poi]] = defaultdict(list)
    kept: list[Poi] = []
    removed = 0
    cell_degrees = 0.0005
    for poi in pois:
        name_key = normalized_name(str(poi["name"]))
        # Do not spatially merge unnamed POIs: two unnamed services can legitimately
        # occupy the same retail complex. Their OSM references still remain unique.
        if name_key.startswith("unnamed"):
            name_key = f"{name_key}:{poi['osm_ref']}"
        lat_cell = math.floor(float(poi["lat"]) / cell_degrees)
        lon_cell = math.floor(float(poi["lon"]) / cell_degrees)
        duplicate = False
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for candidate in cells.get((str(poi["category_id"]), name_key, lat_cell + dy, lon_cell + dx), []):
                    if approximate_distance_m(poi, candidate) <= DEDUP_DISTANCE_M:
                        duplicate = True
                        break
                if duplicate:
                    break
            if duplicate:
                break
        if duplicate:
            removed += 1
            continue
        kept.append(poi)
        cells[(str(poi["category_id"]), name_key, lat_cell, lon_cell)].append(poi)
    return kept, removed


def build_payload(pois: list[Poi]) -> dict:
    buckets: dict[str, list[list]] = defaultdict(list)
    for poi in sorted(pois, key=lambda item: (float(item["lat"]), float(item["lon"]), str(item["category_id"]), str(item["osm_ref"]))):
        lat = float(poi["lat"])
        lon = float(poi["lon"])
        bucket = f"{math.floor(lat * BUCKET_SCALE)}:{math.floor(lon * BUCKET_SCALE)}"
        category_id = str(poi["category_id"])
        osm_ref = str(poi["osm_ref"])
        buckets[bucket].append(
            [
                f"{category_id}:{osm_ref}",
                CATEGORY_INDEX[category_id],
                round(lat * COORDINATE_SCALE),
                round(lon * COORDINATE_SCALE),
                str(poi["name"]),
                osm_ref,
            ]
        )
    return {"buckets": dict(sorted(buckets.items()))}


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract supermarket, post-office and pharmacy POIs from the pinned OSM Great Britain PBF.")
    parser.add_argument("--discovery", type=Path, default=DEFAULT_DISCOVERY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    if shutil.which("ogr2ogr") is None:
        raise RuntimeError("ogr2ogr with the GDAL OSM driver is required to process the OSM PBF")
    discovery = json.loads(args.discovery.read_text(encoding="utf-8"))
    if discovery.get("dataset") != "Goldilocks service POI source set":
        raise RuntimeError(f"Unexpected service discovery metadata: {args.discovery}")
    source = discovery.get("source") or {}
    filename = source.get("file")
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise RuntimeError("Service discovery metadata has an invalid source filename")
    pbf = args.discovery.parent / filename
    if not pbf.is_file():
        raise RuntimeError(f"Pinned OSM source PBF is missing: {pbf}")
    if source.get("bytes") and pbf.stat().st_size != int(source["bytes"]):
        raise RuntimeError(f"OSM PBF size differs from discovery metadata: {pbf}")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = SOURCE_ROOT / "cache" / pbf.stem
    cache_dir.mkdir(parents=True, exist_ok=True)
    osmconf = cache_dir / "osmconf.ini"
    osmconf.write_text(OSMCONF, encoding="utf-8")
    points_path = cache_dir / "points.geojsonl"
    areas_path = cache_dir / "areas.geojsonl"
    if not points_path.is_file() or points_path.stat().st_size == 0:
        points_path.unlink(missing_ok=True)
        run_ogr_extract(pbf, osmconf, points_path, polygons=False)
    else:
        print(f"Using cached node POIs: {points_path}")
    if not areas_path.is_file() or areas_path.stat().st_size == 0:
        areas_path.unlink(missing_ok=True)
        run_ogr_extract(pbf, osmconf, areas_path, polygons=True)
    else:
        print(f"Using cached area POIs: {areas_path}")
    pois = load_geojsonseq(points_path, source_rank=0)
    pois.extend(load_geojsonseq(areas_path, source_rank=1))
    raw_counts = Counter(str(item["category_id"]) for item in pois)
    deduped, duplicate_count = deduplicate(pois)
    counts = Counter(str(item["category_id"]) for item in deduped)
    payload = build_payload(deduped)
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    payload_path = output_dir / "services.json.gz"
    payload_path.write_bytes(compressed)

    categories = [dict(item, count=int(counts[item["id"]])) for item in CATEGORIES]
    manifest = {
        "format_version": 1,
        "kind": "goldilocks-services",
        "generated_at_unix": int(time.time()),
        "min_zoom": MIN_ZOOM,
        "bucket_scale": BUCKET_SCALE,
        "coordinate_scale": COORDINATE_SCALE,
        "categories": categories,
        "counts": {item["id"]: int(counts[item["id"]]) for item in CATEGORIES},
        "raw_counts_before_spatial_dedup": {item["id"]: int(raw_counts[item["id"]]) for item in CATEGORIES},
        "spatial_duplicates_removed": duplicate_count,
        "source": {
            "id": "openstreetmap-geofabrik-gb-2026-09-06",
            "provider": "OpenStreetMap contributors",
            "distributor": "Geofabrik GmbH",
            "dataset": "OpenStreetMap Great Britain extract",
            "extract_date": source.get("extract_date"),
            "homepage_url": "https://www.openstreetmap.org/",
            "download_url": source.get("url"),
            "licence_name": "Open Database License (ODbL) 1.0",
            "licence_url": "https://opendatacommons.org/licenses/odbl/1-0/",
            "attribution": "© OpenStreetMap contributors",
            "note": "Goldilocks extracts shop=supermarket, amenity=post_office and amenity=pharmacy from the pinned Great Britain OSM extract. Area features use a representative point. Separately mapped same-name node/area POIs within 25 m are de-duplicated, preferring the node.",
        },
        "source_file": {
            "file": filename,
            "url": source.get("url"),
            "bytes": source.get("bytes"),
            "md5": source.get("md5"),
        },
        "payload_file": payload_path.name,
        "payload_encoding": "gzip+json",
        "raw_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "sha256_raw": hashlib.sha256(raw).hexdigest(),
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(
        f"Service POIs: {sum(counts.values()):,} total after de-duplication "
        f"({', '.join(f'{key}={counts[key]:,}' for key in CATEGORY_INDEX)}); removed {duplicate_count:,} duplicates"
    )
    print(f"Payload {len(raw):,} bytes raw -> {len(compressed):,} bytes gzip")
    print(f"Wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
