#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

import fiona
import numpy as np
from shapely import make_valid
from shapely.geometry import Point, box, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from goldilocks_raster import MetricResult, grid_from_manifest, grids_match, load_base_validity_mask, write_dataset_manifest

ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = ROOT / "data" / "source" / "woodland"
DEFAULT_DISCOVERY = SOURCE_ROOT / "discovery.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "derived" / "woodland-metrics"
CATEGORY = {"id": "woodland", "label": "Woodland", "order": 4}
WOODLAND_PALETTE = ("#ffffff", "#006400")
CELL_AREA_M2 = 1_000_000.0

NFI_FILE = "nfi_gb_2024.shp.zip"
ENGLAND_REVISED_FILE = "ancient_england_revised.gpkg.zip"
ENGLAND_LEGACY_FILE = "ancient_england_legacy.gpkg.zip"
WALES_FILE = "ancient_wales.gpkg"
SCOTLAND_FILE = "ancient_scotland.gpkg.zip"
ONS_LAD_FILE = "ons_lad_dec_2025_bgc.shp.zip"

CURRENT_WOODLAND_CLASSES = {
    "Assumed woodland",
    "Broadleaved",
    "Conifer",
    "Coppice",
    "Coppice with standards",
    "Low density",
    "Mixed mainly broadleaved",
    "Mixed mainly conifer",
    "Shrub",
    "Young trees",
}
EXCLUDED_WOODLAND_CLASSES = {"Failed", "Felled", "Ground prep", "Windblow"}
EXPECTED_NFI_WOODLAND_CLASSES = CURRENT_WOODLAND_CLASSES | EXCLUDED_WOODLAND_CLASSES
SCOTLAND_ANCIENT_CODES = {"1a", "2a"}


def default_grid_manifest() -> Path:
    return ROOT / "data" / "derived" / "climate-metrics" / "manifest.json"


def default_gb_mask_manifest() -> Path:
    return ROOT / "data" / "derived" / "terrain-metrics" / "manifest.json"


def source_path(filename: str) -> Path:
    path = SOURCE_ROOT / filename
    if not path.is_file():
        raise RuntimeError(f"Woodland source is missing: {path}. Run download_woodland_sources.py first.")
    return path.resolve()


def zip_uri(path: Path, member: str | None = None) -> str:
    uri = f"zip://{path}"
    return f"{uri}!{member}" if member else uri


def checked_geometry(feature, *, label: str):
    if feature.get("geometry") is None:
        return None
    geom = shape(feature["geometry"])
    if geom.is_empty:
        return None
    if not geom.is_valid:
        geom = make_valid(geom)
    if geom.is_empty:
        return None
    if geom.geom_type not in {"Polygon", "MultiPolygon", "GeometryCollection"}:
        raise RuntimeError(f"{label}: unexpected geometry type {geom.geom_type}")
    return geom


def cell_range(bounds, grid) -> tuple[int, int, int, int] | None:
    minx, miny, maxx, maxy = bounds
    size = grid.cell_size_m
    min_col = max(0, math.floor((minx - grid.west) / size))
    max_col = min(grid.width - 1, math.ceil((maxx - grid.west) / size) - 1)
    min_row = max(0, math.floor((miny - grid.south) / size))
    max_row = min(grid.height - 1, math.ceil((maxy - grid.south) / size) - 1)
    if min_col > max_col or min_row > max_row:
        return None
    return min_col, max_col, min_row, max_row


def add_geometry_area(area_grid: np.ndarray, geom, grid, gb_mask: np.ndarray) -> float:
    candidate = cell_range(geom.bounds, grid)
    if candidate is None:
        return 0.0
    min_col, max_col, min_row, max_row = candidate

    # The majority of woodland polygons are smaller than 1 km and entirely
    # inside one canonical cell; avoid a GEOS intersection for that common case.
    if min_col == max_col and min_row == max_row and gb_mask[min_row, min_col]:
        minx, miny, maxx, maxy = geom.bounds
        west = grid.west + min_col * grid.cell_size_m
        south = grid.south + min_row * grid.cell_size_m
        east = west + grid.cell_size_m
        north = south + grid.cell_size_m
        if minx >= west and maxx <= east and miny >= south and maxy <= north:
            value = float(geom.area)
            area_grid[min_row, min_col] += value
            return value

    added = 0.0
    for row in range(min_row, max_row + 1):
        south = grid.south + row * grid.cell_size_m
        north = south + grid.cell_size_m
        for col in range(min_col, max_col + 1):
            if not gb_mask[row, col]:
                continue
            west = grid.west + col * grid.cell_size_m
            east = west + grid.cell_size_m
            cell = box(west, south, east, north)
            if not geom.intersects(cell):
                continue
            clipped = geom.intersection(cell)
            if clipped.is_empty:
                continue
            value = float(clipped.area)
            if value <= 0:
                continue
            area_grid[row, col] += value
            added += value
    return added


def collect_geometry_fragments(fragments: dict[int, list], geom, grid, gb_mask: np.ndarray) -> float:
    candidate = cell_range(geom.bounds, grid)
    if candidate is None:
        return 0.0
    min_col, max_col, min_row, max_row = candidate
    raw_area = 0.0

    # Keep clipped polygon fragments per canonical cell. Ancient-woodland
    # inventories can contain overlaps, so their areas must be unioned before
    # converting to a percentage rather than simply summed.
    if min_col == max_col and min_row == max_row and gb_mask[min_row, min_col]:
        minx, miny, maxx, maxy = geom.bounds
        west = grid.west + min_col * grid.cell_size_m
        south = grid.south + min_row * grid.cell_size_m
        east = west + grid.cell_size_m
        north = south + grid.cell_size_m
        if minx >= west and maxx <= east and miny >= south and maxy <= north:
            value = float(geom.area)
            if value > 0:
                fragments.setdefault(min_row * grid.width + min_col, []).append(geom)
                return value
            return 0.0

    for row in range(min_row, max_row + 1):
        south = grid.south + row * grid.cell_size_m
        north = south + grid.cell_size_m
        for col in range(min_col, max_col + 1):
            if not gb_mask[row, col]:
                continue
            west = grid.west + col * grid.cell_size_m
            east = west + grid.cell_size_m
            cell = box(west, south, east, north)
            if not geom.intersects(cell):
                continue
            clipped = geom.intersection(cell)
            if clipped.is_empty:
                continue
            value = float(clipped.area)
            if value <= 0:
                continue
            fragments.setdefault(row * grid.width + col, []).append(clipped)
            raw_area += value
    return raw_area


def union_fragment_areas(fragments: dict[int, list], grid, gb_mask: np.ndarray) -> tuple[np.ndarray, dict]:
    area_grid = np.zeros((grid.height, grid.width), dtype=np.float64)
    raw_area = 0.0
    union_area = 0.0
    multi_fragment_cells = 0
    overlap_cells = 0
    max_overlap_removed = 0.0

    for flat_index, parts in fragments.items():
        row, col = divmod(flat_index, grid.width)
        if not gb_mask[row, col]:
            raise RuntimeError(f"Ancient woodland fragment unexpectedly landed outside GB mask at row={row} col={col}")
        raw = sum(float(part.area) for part in parts)
        raw_area += raw
        if len(parts) == 1:
            merged_area = raw
        else:
            multi_fragment_cells += 1
            merged = unary_union(parts)
            merged_area = float(merged.area)
        if merged_area > CELL_AREA_M2 + 1.0:
            raise RuntimeError(
                f"Ancient woodland union exceeds canonical cell row={row} col={col}: {merged_area:,.1f} m²"
            )
        removed = max(0.0, raw - merged_area)
        if removed > 0.01:
            overlap_cells += 1
            max_overlap_removed = max(max_overlap_removed, removed)
        area_grid[row, col] = min(merged_area, CELL_AREA_M2)
        union_area += area_grid[row, col]

    return area_grid, {
        "raw_intersection_area_ha_before_union": float(raw_area / 10_000.0),
        "union_area_ha": float(union_area / 10_000.0),
        "overlap_removed_ha": float((raw_area - union_area) / 10_000.0),
        "cells_with_multiple_fragments": multi_fragment_cells,
        "cells_with_overlap_removed": overlap_cells,
        "max_overlap_removed_in_one_cell_ha": float(max_overlap_removed / 10_000.0),
    }


def validate_area_grid(name: str, area_grid: np.ndarray, gb_mask: np.ndarray) -> None:
    maximum = float(np.nanmax(area_grid[gb_mask]))
    if maximum > CELL_AREA_M2 + 1.0:
        row, col = np.argwhere(area_grid > CELL_AREA_M2 + 1.0)[0]
        raise RuntimeError(
            f"{name} polygon areas overlap in canonical cell row={row} col={col}: "
            f"{area_grid[row, col]:,.1f} m² exceeds the 1 km cell area"
        )
    np.minimum(area_grid, CELL_AREA_M2, out=area_grid)


def derive_current_woodland(grid, gb_mask: np.ndarray) -> tuple[np.ndarray, dict]:
    path = source_path(NFI_FILE)
    uri = zip_uri(path)
    layer = fiona.listlayers(uri)[0]
    area_grid = np.zeros((grid.height, grid.width), dtype=np.float64)
    classes = Counter()
    included_features = 0
    source_area = 0.0
    raster_area = 0.0

    with fiona.open(uri, layer=layer) as src:
        if src.crs.to_epsg() != 27700:
            raise RuntimeError(f"NFI source is not EPSG:27700: {src.crs}")
        feature_count = len(src)
        for index, feature in enumerate(src, start=1):
            props = feature["properties"]
            if props.get("CATEGORY") != "Woodland":
                continue
            forest_type = props.get("IFT_IOA")
            classes[str(forest_type)] += 1
            if forest_type not in CURRENT_WOODLAND_CLASSES:
                continue
            geom = checked_geometry(feature, label=f"NFI feature {props.get('FID')}")
            if geom is None:
                continue
            included_features += 1
            source_area += float(geom.area)
            raster_area += add_geometry_area(area_grid, geom, grid, gb_mask)
            if included_features % 100000 == 0:
                print(f"NFI current woodland: {included_features:,} included polygons", flush=True)

    unknown = set(classes) - EXPECTED_NFI_WOODLAND_CLASSES
    if unknown:
        raise RuntimeError(f"NFI contains unexpected woodland classes: {sorted(unknown)}")
    validate_area_grid("NFI current woodland", area_grid, gb_mask)
    values = np.full(area_grid.shape, np.nan, dtype=np.float32)
    values[gb_mask] = (area_grid[gb_mask] / 10_000.0).astype(np.float32)
    coverage = {
        "source_features": feature_count,
        "included_features": included_features,
        "included_ift_ioa": sorted(CURRENT_WOODLAND_CLASSES),
        "excluded_ift_ioa": sorted(EXCLUDED_WOODLAND_CLASSES),
        "woodland_class_counts": dict(sorted(classes.items())),
        "source_included_area_ha": source_area / 10_000.0,
        "canonical_intersection_area_ha": raster_area / 10_000.0,
        "canonical_valid_cells": int(np.count_nonzero(gb_mask)),
        "method": (
            "Exact polygon intersection area divided by the full 1,000,000 m² canonical cell area. "
            "Only NFI polygons interpreted as current wooded cover are included."
        ),
    }
    return values, coverage


def load_english_lads() -> tuple[list, list[str], STRtree]:
    path = source_path(ONS_LAD_FILE)
    uri = zip_uri(path)
    layer = fiona.listlayers(uri)[0]
    geoms = []
    codes = []
    with fiona.open(uri, layer=layer) as src:
        if src.crs.to_epsg() != 27700:
            raise RuntimeError(f"ONS LAD source is not EPSG:27700: {src.crs}")
        for feature in src:
            code = str(feature["properties"].get("LAD25CD") or "")
            if not code.startswith("E"):
                continue
            geom = checked_geometry(feature, label=f"ONS LAD {code}")
            if geom is None:
                continue
            codes.append(code)
            geoms.append(geom)
    return geoms, codes, STRtree(geoms)


def lad_for_point(point: Point, geoms: list, tree: STRtree) -> int | None:
    candidates = tree.query(point)
    for raw_index in candidates:
        index = int(raw_index)
        if geoms[index].covers(point):
            return index
    # The ONS BGC boundary is generalised, so a handful of Natural England
    # interior points fall just outside its coastal/boundary line. Treat a
    # nearest LAD within 100 m as the same administrative area.
    nearest, distances = tree.query_nearest(point, return_distance=True)
    if len(nearest) and float(distances[0]) <= 100.0:
        return int(nearest[0])
    return None


def england_revised_lads() -> tuple[set[int], list, list[str], STRtree, dict]:
    lad_geoms, lad_codes, tree = load_english_lads()
    revised_path = source_path(ENGLAND_REVISED_FILE)
    uri = zip_uri(revised_path, "Ancient_Woodland_Revised_England_Completed_Counties.gpkg")
    layer = "Ancient_Woodland_Inventory_Revised_England"
    revised_lads: set[int] = set()
    unassigned = 0
    statuses = Counter()
    with fiona.open(uri, layer=layer) as src:
        for feature in src:
            props = feature["properties"]
            statuses[str(props.get("status"))] += 1
            x = props.get("x_coord")
            y = props.get("y_coord")
            if x is None or y is None:
                unassigned += 1
                continue
            lad_index = lad_for_point(Point(float(x), float(y)), lad_geoms, tree)
            if lad_index is None:
                unassigned += 1
            else:
                revised_lads.add(lad_index)
    expected_statuses = {"ASNW", "ARW", "AWPP", "IAWPP"}
    if set(statuses) != expected_statuses:
        raise RuntimeError(f"Unexpected revised England ancient woodland statuses: {dict(statuses)}")
    if unassigned:
        raise RuntimeError(f"Could not assign {unassigned} revised England AWI points to an English LAD")
    info = {
        "revised_lad_count": len(revised_lads),
        "revised_lad_codes": sorted(lad_codes[index] for index in revised_lads),
        "revised_status_counts": dict(sorted(statuses.items())),
    }
    return revised_lads, lad_geoms, lad_codes, tree, info


def derive_ancient_woodland(grid, gb_mask: np.ndarray) -> tuple[np.ndarray, dict]:
    fragments: dict[int, list] = {}
    revised_lads, lad_geoms, _, _, england_info = england_revised_lads()
    revised_coverage_geoms = [lad_geoms[index] for index in sorted(revised_lads)]
    revised_coverage_tree = STRtree(revised_coverage_geoms)
    counts: dict[str, int] = {}
    raw_areas: dict[str, float] = {}

    revised_uri = zip_uri(
        source_path(ENGLAND_REVISED_FILE),
        "Ancient_Woodland_Revised_England_Completed_Counties.gpkg",
    )
    revised_count = 0
    revised_area = 0.0
    with fiona.open(revised_uri, layer="Ancient_Woodland_Inventory_Revised_England") as src:
        for feature in src:
            geom = checked_geometry(feature, label="England revised AWI")
            if geom is None:
                continue
            revised_count += 1
            revised_area += collect_geometry_fragments(fragments, geom, grid, gb_mask)
    counts["england_revised_included"] = revised_count
    raw_areas["england_revised_ha"] = revised_area / 10_000.0

    legacy_uri = zip_uri(source_path(ENGLAND_LEGACY_FILE), "Ancient_Woodland_England.gpkg")
    legacy_included = 0
    legacy_excluded_revised_area = 0
    legacy_partially_clipped = 0
    legacy_area = 0.0
    legacy_statuses = Counter()
    with fiona.open(legacy_uri, layer="Ancient_Woodland_England") as src:
        for feature in src:
            props = feature["properties"]
            legacy_statuses[str(props.get("status"))] += 1
            geom = checked_geometry(feature, label="England legacy AWI")
            if geom is None:
                continue

            candidate_indices = revised_coverage_tree.query(geom)
            cutouts = [
                revised_coverage_geoms[int(index)]
                for index in candidate_indices
                if revised_coverage_geoms[int(index)].intersects(geom)
            ]
            remaining = geom
            if cutouts:
                revised_cover = cutouts[0] if len(cutouts) == 1 else unary_union(cutouts)
                remaining = geom.difference(revised_cover)
                if remaining.is_empty or float(remaining.area) <= 0:
                    legacy_excluded_revised_area += 1
                    continue
                if float(remaining.area) < float(geom.area) - 0.01:
                    legacy_partially_clipped += 1

            legacy_included += 1
            legacy_area += collect_geometry_fragments(fragments, remaining, grid, gb_mask)
    expected_legacy = {"ASNW", "PAWS", "AWP"}
    if set(legacy_statuses) != expected_legacy:
        raise RuntimeError(f"Unexpected legacy England AWI statuses: {dict(legacy_statuses)}")
    counts["england_legacy_included"] = legacy_included
    counts["england_legacy_excluded_in_revised_lads"] = legacy_excluded_revised_area
    counts["england_legacy_partially_clipped_at_revised_lad_boundaries"] = legacy_partially_clipped
    raw_areas["england_legacy_ha"] = legacy_area / 10_000.0

    wales_path = source_path(WALES_FILE)
    wales_count = 0
    wales_area = 0.0
    wales_categories = Counter()
    included_wales = {
        "Ancient Semi Natural Woodland",
        "Restored Ancient Woodland Site",
        "Plantation on Ancient Woodland Site",
        "Ancient Woodland Site of Unknown Category",
    }
    deleted_wales = {
        "Plantation on Ancient Woodland Site Deleted",
        "Ancient Semi Natural Woodland Deleted",
    }
    with fiona.open(str(wales_path), layer="NRW_ANCIENT_WOODLAND_INVENTORY_2021") as src:
        if src.crs.to_epsg() != 27700:
            raise RuntimeError(f"Wales AWI source is not EPSG:27700: {src.crs}")
        for feature in src:
            props = feature["properties"]
            category = str(props.get("category_name"))
            wales_categories[category] += 1
            if category not in included_wales:
                continue
            geom = checked_geometry(feature, label="Wales AWI")
            if geom is None:
                continue
            wales_count += 1
            wales_area += collect_geometry_fragments(fragments, geom, grid, gb_mask)
    if set(wales_categories) != included_wales | deleted_wales:
        raise RuntimeError(f"Unexpected Wales AWI categories: {dict(wales_categories)}")
    counts["wales_included"] = wales_count
    counts["wales_deleted_excluded"] = sum(wales_categories[name] for name in deleted_wales)
    raw_areas["wales_ha"] = wales_area / 10_000.0

    scotland_uri = zip_uri(source_path(SCOTLAND_FILE), "AWI_SCOTLAND.gpkg")
    scotland_count = 0
    scotland_area = 0.0
    scotland_categories = Counter()
    with fiona.open(scotland_uri, layer="AWI_SCOTLAND") as src:
        if src.crs.to_epsg() != 27700:
            raise RuntimeError(f"Scotland AWI source is not EPSG:27700: {src.crs}")
        for feature in src:
            props = feature["properties"]
            code = str(props.get("ANTIQUITY_") or "")
            scotland_categories[f"{code}: {props.get('ANTIQUITY')}"] += 1
            if code not in SCOTLAND_ANCIENT_CODES:
                continue
            geom = checked_geometry(feature, label="Scotland AWI")
            if geom is None:
                continue
            scotland_count += 1
            scotland_area += collect_geometry_fragments(fragments, geom, grid, gb_mask)
    counts["scotland_1a_2a_included"] = scotland_count
    raw_areas["scotland_1a_2a_ha"] = scotland_area / 10_000.0

    area_grid, union_stats = union_fragment_areas(fragments, grid, gb_mask)
    values = np.full(area_grid.shape, np.nan, dtype=np.float32)
    values[gb_mask] = (area_grid[gb_mask] / 10_000.0).astype(np.float32)
    coverage = {
        **england_info,
        "feature_counts": counts,
        "raw_canonical_intersection_area_ha_before_union": raw_areas,
        "union_statistics": union_stats,
        "legacy_status_counts": dict(sorted(legacy_statuses.items())),
        "wales_category_counts": dict(sorted(wales_categories.items())),
        "scotland_category_counts": dict(sorted(scotland_categories.items())),
        "canonical_valid_cells": int(np.count_nonzero(gb_mask)),
        "method": (
            "Accepted inventory polygons are clipped exactly to canonical 1 km cells, unioned within each cell to remove "
            "source overlaps, then divided by the full 1,000,000 m² cell area. England uses the revised inventory in every "
            "2025 LAD containing revised AWI features and legacy AWI elsewhere; Wales includes all four current 2021 AWI "
            "categories; Scotland includes antiquity codes 1a and 2a only."
        ),
        "england_revision_note": (
            "Natural England publishes completed revised counties without a county field. Goldilocks infers revised coverage "
            "at 2025 Local Authority District level from the supplied revised AWI feature coordinates, then removes legacy geometry inside those LAD polygons. Legacy polygons crossing a revised/unrevised LAD boundary are "
            "clipped at the boundary rather than discarded wholesale. The ONS BGC boundary is generalised; revised feature "
            "coordinates within 100 m of an LAD boundary are assigned to the nearest English LAD."
        ),
    }
    return values, coverage

def source_record(discovery: dict, source_id: str) -> dict:
    for item in discovery.get("files", []):
        if item.get("id") == source_id:
            return {
                "file": item.get("file"),
                "url": item.get("url"),
                "bytes": item.get("bytes"),
                "sha256": item.get("sha256"),
            }
    raise RuntimeError(f"Woodland discovery metadata has no source record {source_id}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Derive 1 km current woodland and ancient woodland cover metrics from pinned GB vector sources."
    )
    parser.add_argument("--discovery", type=Path, default=DEFAULT_DISCOVERY)
    parser.add_argument("--grid-manifest", type=Path, default=default_grid_manifest())
    parser.add_argument("--gb-mask-manifest", type=Path, default=default_gb_mask_manifest())
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    discovery = json.loads(args.discovery.read_text(encoding="utf-8"))
    if discovery.get("dataset") != "Goldilocks woodland source set":
        raise RuntimeError(f"Unexpected woodland discovery metadata: {args.discovery}")

    grid_manifest, canonical_mask = load_base_validity_mask(args.grid_manifest)
    gb_manifest, gb_mask = load_base_validity_mask(args.gb_mask_manifest)
    if not grids_match(grid_manifest["grid"], gb_manifest["grid"]):
        raise RuntimeError("Terrain GB mask does not use the canonical Goldilocks grid")
    grid = grid_from_manifest(grid_manifest["grid"])
    gb_mask &= canonical_mask
    print(
        f"Canonical Goldilocks grid {grid.width}x{grid.height}; "
        f"{np.count_nonzero(gb_mask):,} GB cells from Terrain 50 coverage"
    )

    woodland_values, woodland_coverage = derive_current_woodland(grid, gb_mask)
    ancient_values, ancient_coverage = derive_ancient_woodland(grid, gb_mask)

    metrics = [
        MetricResult(
            id="woodland_cover",
            category_id="woodland",
            label="Current woodland cover",
            units="%",
            period="NFI GB 2024",
            definition=(
                "Percentage of the full canonical 1 km tile inside National Forest Inventory polygons representing current "
                "woodland land. Felled, ground-preparation, failed and windblown classes are excluded. This is woodland "
                "polygon area, not fractional tree-canopy density within each polygon."
            ),
            values=woodland_values,
            scale=0.1,
            offset=0.0,
            decimals=1,
            source_id="forestry-commission-nfi-gb-2024",
            source_variable="NFI CATEGORY=Woodland; filtered IFT_IOA",
            coverage=woodland_coverage,
            palette=WOODLAND_PALETTE,
            display_range=(0.0, 100.0),
        ),
        MetricResult(
            id="woodland_ancient_cover",
            category_id="woodland",
            label="Ancient woodland cover",
            units="%",
            period="GB national ancient woodland inventories",
            definition=(
                "Percentage of the full canonical 1 km tile covered by recognised ancient woodland inventory sites. England "
                "uses revised inventory coverage in preference to legacy inventory; Wales includes ASNW, RAWS, PAWS and "
                "AWSU; Scotland includes Ancient Woodland categories 1a and 2a only. This is inventory-site extent rather "
                "than current canopy cover, so it is not necessarily a subset of the current woodland metric."
            ),
            values=ancient_values,
            scale=0.1,
            offset=0.0,
            decimals=1,
            source_id="gb-ancient-woodland-inventories",
            source_variable="National ancient woodland inventory polygons",
            coverage=ancient_coverage,
            palette=WOODLAND_PALETTE,
            display_range=(0.0, 100.0),
        ),
    ]

    sources = {
        "forestry-commission-nfi-gb-2024": {
            "provider": "Forestry Commission",
            "dataset": "National Forest Inventory GB 2024 woodland map",
            "resolution": "Vector woodland polygons aggregated by exact area to 1 km",
            "homepage_url": "https://www.data.gov.uk/dataset/64379a2a-d877-415e-9e8d-8553bf09faea/national-forest-inventory-gb-2024",
            "licence_name": "Open Government Licence v3.0",
            "licence_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
            "attribution": "© Forestry Commission copyright. Contains Ordnance Survey data © Crown copyright and database right 2025.",
            "derived_product_notice": (
                "Goldilocks current woodland cover is a filtered area summary derived from NFI GB 2024; "
                "it is not a Forestry Commission product."
            ),
            "note": (
                "Goldilocks includes NFI interpreted forest types that represent current tree/shrub cover and excludes "
                "Felled, Ground prep, Failed and Windblow. The NFI woodland map generally maps woodland of at least 0.5 ha; "
                "Assumed woodland and Low density areas may be mapped from 0.1 ha. Northern Ireland is outside NFI GB coverage."
            ),
            "releases": [
                {
                    "label": "National Forest Inventory GB 2024",
                    "status": "published; source item checked 2026-09-05",
                    "url": "https://www.data.gov.uk/dataset/64379a2a-d877-415e-9e8d-8553bf09faea/national-forest-inventory-gb-2024",
                }
            ],
            "source_files": {"nfi": [source_record(discovery, "nfi-gb-2024")]},
        },
        "gb-ancient-woodland-inventories": {
            "provider": "Natural England / Natural Resources Wales / NatureScot",
            "dataset": "Goldilocks GB composite of national Ancient Woodland Inventories",
            "resolution": "Vector ancient-woodland polygons aggregated by exact area to 1 km",
            "licence_name": "Source-specific open licences",
            "attribution": (
                "England: © Natural England 2024. Contains OS data © Crown copyright and database rights 2024. "
                "OS AC0000851168. Wales: Contains Natural Resources Wales information © Natural Resources Wales and "
                "Database Right. All rights Reserved. Contains Ordnance Survey Data. Ordnance Survey Licence number "
                "AC0000849444. Crown Copyright and Database Right. Scotland: Copyright NatureScot Contains Ordnance "
                "Survey data © Crown copyright and database right 2026. England revision coverage helper: Source: Office "
                "for National Statistics licensed under the Open Government Licence v.3.0. Contains OS data © Crown "
                "copyright and database right 2025."
            ),
            "derived_product_notice": (
                "The GB ancient woodland cover raster is a Goldilocks harmonisation of three national inventories and "
                "is not an official product of any source agency."
            ),
            "note": (
                "National inventories use different historical evidence and classifications. England revised AWI replaces "
                "legacy AWI where revised coverage is available; Wales includes all four recognised AWI categories; "
                "Scotland includes only antiquity classes 1a and 2a. Northern Ireland is nodata."
            ),
            "releases": [
                {
                    "label": "Natural England Ancient Woodland - Revised - Completed Counties",
                    "status": "published",
                    "url": "https://www.data.gov.uk/dataset/12b72196-1f20-44a9-8fe0-0e78db46a67c/ancient-woodland-revised-england-completed-counties",
                },
                {
                    "label": "Natural England Ancient Woodland (England) legacy inventory",
                    "status": "published",
                    "url": "https://www.data.gov.uk/dataset/9461f463-c363-4309-ae77-fdcd7e9df7d3/ancient-woodland-england",
                },
                {
                    "label": "Natural Resources Wales Ancient Woodland Inventory 2021",
                    "status": "published 2026",
                    "url": "https://datamap.gov.wales/layers/inspire-nrw:NRW_ANCIENT_WOODLAND_INVENTORY_2021",
                },
                {
                    "label": "NatureScot Ancient Woodland Inventory (Scotland)",
                    "status": "published",
                    "url": "https://www.data.gov.uk/dataset/c2f57ed9-5601-4864-af5f-a6e73e977f54/ancient-woodland-inventory-scotland1",
                },
            ],
            "source_files": {
                "england_revised": [source_record(discovery, "ancient-england-revised")],
                "england_legacy": [source_record(discovery, "ancient-england-legacy")],
                "wales": [source_record(discovery, "ancient-wales-2021")],
                "scotland": [source_record(discovery, "ancient-scotland")],
                "england_revision_boundary_helper": [source_record(discovery, "ons-lad-2025")],
            },
        },
    }

    return_path = write_dataset_manifest(
        dataset_id="woodland",
        categories=[CATEGORY],
        metrics=metrics,
        grid=grid,
        output_dir=args.output_dir.resolve(),
        sources=sources,
        validity_mask=gb_mask,
        grid_valid_cell_count=int(np.count_nonzero(gb_mask)),
        source_valid_cell_count=int(np.count_nonzero(gb_mask)),
    )
    print(f"Woodland metrics written to {return_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
