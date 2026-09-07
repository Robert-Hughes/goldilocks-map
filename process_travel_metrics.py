#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import time
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import fiona
import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import shape
from shapely.strtree import STRtree

from goldilocks_raster import MetricResult, grid_from_manifest, load_base_validity_mask, write_dataset_manifest
from travel_time_routing import (
    CODE_TO_FUNC,
    FLAG_ROUNDABOUT,
    FLAG_SLIP,
    FUNC_TO_CODE,
    RoadGraph,
    apply_nh_observed_costs,
    build_csr,
    dijkstra_all,
    load_nh_index,
    load_open_roads,
    mean_costs_where_observed,
    mph_to_seconds,
    nearest_node,
    nearest_nodes_for_points,
    norm_road,
)

ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = ROOT / "data" / "source" / "travel-time"
DEFAULT_DISCOVERY = SOURCE_ROOT / "discovery.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "derived" / "travel-metrics"
CATEGORY = {"id": "travel", "label": "Travel", "order": 6}
SOURCE_ID = "goldilocks-travel-time-model"
OGL_NAME = "Open Government Licence v3.0"
OGL_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
BNG = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)
TARGETS = {
    "york": {"label": "York", "lat": 53.9591, "lon": -1.0815},
    "cambridge": {"label": "Cambridge", "lat": 52.2053, "lon": 0.1218},
}
TRAVEL_PALETTE = ("#1a9850", "#91cf60", "#d9ef8b", "#fee08b", "#fc8d59", "#d73027")
# OS Open Roads covers Great Britain, not Northern Ireland. On the current
# canonical UK grid the nearest-node distance has a clean jump from <10 km for
# GB road-covered land to >20 km for NI. Treat cells beyond 20 km as outside
# the source network rather than spuriously snapping them across the Irish Sea.
MAX_CELL_SNAP_M = 20_000.0

OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
NS = {"office": OFFICE_NS, "table": TABLE_NS, "text": TEXT_NS}


def default_grid_manifest() -> Path:
    return ROOT / "data" / "derived" / "climate-metrics" / "manifest.json"


def load_discovery(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("format_version") != 1:
        raise RuntimeError(f"Unsupported travel discovery metadata: {path}")
    return document


def source_path(discovery: dict[str, Any], *keys: str) -> Path:
    item: Any = discovery
    for key in keys:
        item = item[key]
    if not isinstance(item, str):
        raise RuntimeError(f"Travel discovery path {keys!r} is not a string")
    path = SOURCE_ROOT / item
    if not path.exists():
        raise RuntimeError(f"Travel source is missing: {path}")
    return path


def read_ods_sheet(path: Path, sheet_name: str) -> list[list[Any]]:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("content.xml"))
    for table in root.findall(".//table:table", NS):
        if table.get(f"{{{TABLE_NS}}}name") != sheet_name:
            continue
        rows: list[list[Any]] = []
        for row_element in table.findall("table:table-row", NS):
            row_repeat = int(row_element.get(f"{{{TABLE_NS}}}number-rows-repeated", "1"))
            values: list[Any] = []
            for cell in list(row_element):
                if cell.tag not in (f"{{{TABLE_NS}}}table-cell", f"{{{TABLE_NS}}}covered-table-cell"):
                    continue
                repeat = int(cell.get(f"{{{TABLE_NS}}}number-columns-repeated", "1"))
                value_type = cell.get(f"{{{OFFICE_NS}}}value-type")
                value: Any = None
                if value_type in ("float", "currency", "percentage"):
                    raw = cell.get(f"{{{OFFICE_NS}}}value")
                    if raw is not None:
                        value = float(raw)
                elif value_type == "date":
                    value = cell.get(f"{{{OFFICE_NS}}}date-value")
                elif value_type == "boolean":
                    value = cell.get(f"{{{OFFICE_NS}}}boolean-value")
                if value is None:
                    paragraphs = []
                    for paragraph in cell.findall(".//text:p", NS):
                        paragraphs.append("".join(paragraph.itertext()))
                    text = "\n".join(part for part in paragraphs if part).strip()
                    value = text if text else None
                if repeat > 256 and value is None:
                    # ODS often uses a huge repeated-empty tail. It has no semantic
                    # value for our narrow statistical tables.
                    break
                values.extend([value] * repeat)
            while values and values[-1] is None:
                values.pop()
            for _ in range(min(row_repeat, 64)):
                rows.append(list(values))
        return rows
    raise RuntimeError(f"ODS sheet {sheet_name!r} was not found in {path}")


def header_index(headers: list[Any], prefix: str) -> int:
    wanted = prefix.casefold()
    for index, value in enumerate(headers):
        if str(value or "").strip().casefold().startswith(wanted):
            return index
    raise RuntimeError(f"Could not find ODS column beginning {prefix!r} in {headers!r}")


def finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def load_dft_speeds(discovery: dict[str, Any]) -> tuple[dict[tuple[str, str], float], dict[str, dict[str, float]], dict[str, Any]]:
    specs = {
        "E": ("england", "CGN0503e", "CGN0503a"),
        "S": ("scotland", "CGN0507c", "CGN0507a"),
        "W": ("wales", "CGN0509c", "CGN0509a"),
    }
    road_speeds: dict[tuple[str, str], float] = {}
    context: dict[str, dict[str, float]] = {}
    stats: dict[str, Any] = {}
    for country, (country_key, road_sheet, context_sheet) in specs.items():
        relative = discovery["dft_local_a"]["files"][country_key]["file"]
        path = SOURCE_ROOT / relative
        road_rows = read_ods_sheet(path, road_sheet)
        if len(road_rows) < 5:
            raise RuntimeError(f"Unexpectedly short DfT road sheet {road_sheet}")
        road_headers = road_rows[3]
        code_col = header_index(road_headers, "ONS Area Code")
        road_col = header_index(road_headers, "Road Name")
        value_col = max(index for index, header in enumerate(road_headers) if header is not None)
        road_count = 0
        for row in road_rows[4:]:
            if max(code_col, road_col, value_col) >= len(row):
                continue
            code = str(row[code_col] or "").strip()
            road = norm_road(str(row[road_col] or ""))
            speed = finite_float(row[value_col])
            if code and road and speed is not None and speed > 3:
                road_speeds[(code, road)] = speed
                road_count += 1

        context_rows = read_ods_sheet(path, context_sheet)
        headers = context_rows[3]
        all_col = header_index(headers, "All day (Rolling Year)")
        urban_col = header_index(headers, "Urban roads (Rolling Year)")
        rural_col = header_index(headers, "Rural roads (Rolling Year)")
        evening_col = header_index(headers, "Weekday evening peak (Rolling Year)")
        latest: tuple[str, float, float, float, float] | None = None
        for row in context_rows[4:]:
            if max(all_col, urban_col, rural_col, evening_col) >= len(row):
                continue
            values = [finite_float(row[col]) for col in (all_col, urban_col, rural_col, evening_col)]
            if any(value is None for value in values):
                continue
            label = str(row[0] or "")
            latest = (label, values[0], values[1], values[2], values[3])  # type: ignore[arg-type]
        if latest is None:
            raise RuntimeError(f"No complete rolling-year row in DfT sheet {context_sheet}")
        label, all_day, urban, rural, evening = latest
        context[country] = {
            "all_day_mph": all_day,
            "urban_mph": urban,
            "rural_mph": rural,
            "weekday_evening_peak_mph": evening,
            "peak_ratio": evening / all_day,
        }
        stats[country] = {"road_level_records": road_count, "context_row": label, **context[country]}
    return road_speeds, context, stats


def load_polygon_tree(path: Path, *, layer: str | None = None, code_field: str | None = None):
    geometries = []
    codes = []
    with fiona.open(path, layer=layer) as source:
        for feature in source:
            if feature["geometry"] is None:
                continue
            geometries.append(shape(feature["geometry"]))
            if code_field is not None:
                codes.append(str(feature["properties"][code_field]))
    return STRtree(geometries), codes, geometries


def classify_built_up_for_a_edges(graph: RoadGraph, gpkg_path: Path, cache_path: Path) -> np.ndarray:
    a_code = FUNC_TO_CODE["A Road"]
    edge_indices = np.flatnonzero((graph.function_code == a_code) & (~graph.trunk_mask))
    if cache_path.is_file():
        cached = np.load(cache_path)
        if cached.shape == (len(graph.u),):
            print(f"Loaded cached built-up scores from {cache_path}")
            return cached
    print(f"Classifying {len(edge_indices):,} non-trunk A-road links against OS Open Built Up Areas", flush=True)
    tree, _, _ = load_polygon_tree(gpkg_path, layer="os_open_built_up_areas")
    scores = np.zeros(len(graph.u), dtype=np.uint8)
    batch_size = 100_000
    for start in range(0, len(edge_indices), batch_size):
        edges = edge_indices[start : start + batch_size]
        ux = graph.x[graph.u[edges]]
        uy = graph.y[graph.u[edges]]
        vx = graph.x[graph.v[edges]]
        vy = graph.y[graph.v[edges]]
        for sample_x, sample_y in ((ux, uy), (vx, vy), ((ux + vx) * 0.5, (uy + vy) * 0.5)):
            points = shapely.points(sample_x, sample_y)
            pairs = tree.query(points, predicate="within")
            if pairs.shape[1]:
                hits = np.unique(pairs[0])
                scores[edges[hits]] += 1
        print(f"  built-up classification {min(start+batch_size,len(edge_indices)):,}/{len(edge_indices):,}", flush=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache_path, scores)
    return scores


def assign_lad_to_a_edges(graph: RoadGraph, lad_path: Path, cache_path: Path) -> tuple[np.ndarray, list[str]]:
    a_code = FUNC_TO_CODE["A Road"]
    edge_indices = np.flatnonzero((graph.function_code == a_code) & (~graph.trunk_mask))
    tree, codes, _ = load_polygon_tree(lad_path, code_field="LAD25CD")
    if cache_path.is_file():
        cached = np.load(cache_path)
        if cached.shape == (len(graph.u),):
            print(f"Loaded cached LAD assignments from {cache_path}")
            return cached, codes
    result = np.full(len(graph.u), -1, dtype=np.int16)
    misses = 0
    batch_size = 100_000
    for start in range(0, len(edge_indices), batch_size):
        edges = edge_indices[start : start + batch_size]
        points = shapely.points(
            (graph.x[graph.u[edges]] + graph.x[graph.v[edges]]) * 0.5,
            (graph.y[graph.u[edges]] + graph.y[graph.v[edges]]) * 0.5,
        )
        pairs = tree.query(points, predicate="within")
        seen = np.zeros(len(edges), dtype=np.bool_)
        for point_index, polygon_index in zip(pairs[0], pairs[1]):
            point_index = int(point_index)
            if not seen[point_index]:
                result[int(edges[point_index])] = int(polygon_index)
                seen[point_index] = True
        misses += int(np.count_nonzero(~seen))
    print(f"Assigned LAD to {len(edge_indices)-misses:,}/{len(edge_indices):,} non-trunk A-road links")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache_path, result)
    return result, codes


def mixed_a_cost(length_m: int, urban_fraction: float, urban_mph: float, rural_mph: float, flags: int) -> int:
    if flags & FLAG_ROUNDABOUT:
        urban_mph = min(urban_mph, 20.0)
        rural_mph = min(rural_mph, 20.0)
    if flags & FLAG_SLIP:
        urban_mph = min(urban_mph, 35.0)
        rural_mph = min(rural_mph, 35.0)
    seconds = length_m / 0.44704 * (
        urban_fraction / urban_mph + (1.0 - urban_fraction) / rural_mph
    )
    return max(1, min(65534, int(round(seconds))))


def build_local_costs(
    graph: RoadGraph,
    urban_score: np.ndarray,
    lad_index: np.ndarray,
    lad_codes: list[str],
    road_speeds: dict[tuple[str, str], float],
    country_context: dict[str, dict[str, float]],
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    weekend = graph.base_cost_s.copy()
    peak = graph.base_cost_s.copy()
    counts = {"road_level_observed": 0, "country_context_fallback": 0, "unlocated_fallback": 0}
    a_code = FUNC_TO_CODE["A Road"]
    for edge0 in np.flatnonzero((graph.function_code == a_code) & (~graph.trunk_mask)):
        edge = int(edge0)
        lad_i = int(lad_index[edge])
        lad = lad_codes[lad_i] if lad_i >= 0 else ""
        country = lad[:1]
        context = country_context.get(country)
        road_number = graph.code_to_road[int(graph.road_code[edge])]
        observed = road_speeds.get((lad, road_number))
        length_m = int(graph.length_m[edge])
        if observed is not None and context is not None:
            weekend[edge] = mph_to_seconds(length_m, observed)
            peak[edge] = mph_to_seconds(length_m, observed * context["peak_ratio"])
            counts["road_level_observed"] += 1
            continue
        if context is not None:
            urban_fraction = float(urban_score[edge]) / 3.0
            weekend[edge] = mixed_a_cost(
                length_m,
                urban_fraction,
                context["urban_mph"],
                context["rural_mph"],
                int(graph.form_flags[edge]),
            )
            peak[edge] = mixed_a_cost(
                length_m,
                urban_fraction,
                context["urban_mph"] * context["peak_ratio"],
                context["rural_mph"] * context["peak_ratio"],
                int(graph.form_flags[edge]),
            )
            counts["country_context_fallback"] += 1
        else:
            counts["unlocated_fallback"] += 1
    return weekend, peak, counts


def nh_file(discovery: dict[str, Any], aggregation: str) -> Path:
    for item in discovery["national_highways"]["aggregations"]:
        if item["aggregation"] == aggregation:
            return SOURCE_ROOT / item["file"]
    raise RuntimeError(f"National Highways aggregation {aggregation!r} is missing from discovery metadata")


def build_scenario_costs(
    graph: RoadGraph,
    weekend_base: np.ndarray,
    peak_base: np.ndarray,
    discovery: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    stats: dict[str, Any] = {}
    weekend_variants = []
    for aggregation in ("Normal Saturday", "Normal Sunday"):
        indexes = load_nh_index(nh_file(discovery, aggregation))
        costs, matched, item_stats = apply_nh_observed_costs(weekend_base, graph, indexes)
        stats[aggregation] = item_stats
        weekend_variants.append((costs, matched))
    weekend, weekend_matched = mean_costs_where_observed(weekend_base, weekend_variants)
    stats["weekend_combined"] = {
        "matched_edges": int(np.count_nonzero(weekend_matched)),
        "method": "Mean link traversal time across Normal Saturday and Normal Sunday where available; one observed weekend day is used if only one matches.",
    }

    peak_indexes = load_nh_index(nh_file(discovery, "PM Peak"))
    peak, peak_matched, peak_stats = apply_nh_observed_costs(peak_base, graph, peak_indexes)
    stats["PM Peak"] = peak_stats
    return weekend, peak, stats


def load_or_build_cell_snaps(graph: RoadGraph, grid, canonical_mask: np.ndarray, cache_dir: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    valid_flat = np.flatnonzero(canonical_mask.ravel(order="C"))
    node_path = cache_dir / "canonical_valid_cell_nearest_node.u32.npy"
    distance_path = cache_dir / "canonical_valid_cell_snap_distance.f32.npy"
    if node_path.is_file() and distance_path.is_file():
        nodes = np.load(node_path)
        distances = np.load(distance_path)
        if nodes.shape == valid_flat.shape and distances.shape == valid_flat.shape:
            print(f"Loaded cached canonical road-node snaps for {len(nodes):,} cells")
            return valid_flat, nodes, distances

    rows = valid_flat // grid.width
    cols = valid_flat % grid.width
    xs = grid.west + (cols.astype(np.float64) + 0.5) * grid.cell_size_m
    ys = grid.south + (rows.astype(np.float64) + 0.5) * grid.cell_size_m
    nodes, distances = nearest_nodes_for_points(graph, xs, ys)
    cache_dir.mkdir(parents=True, exist_ok=True)
    np.save(node_path, nodes)
    np.save(distance_path, distances)
    return valid_flat, nodes, distances


def metric_grid_from_distances(
    dist_s: np.ndarray,
    valid_flat: np.ndarray,
    nearest_nodes: np.ndarray,
    snap_distances: np.ndarray,
    grid,
) -> np.ndarray:
    values = np.full(grid.width * grid.height, np.nan, dtype=np.float32)
    seconds = dist_s[nearest_nodes]
    reachable = (seconds != np.uint32(0xFFFFFFFF)) & (snap_distances <= MAX_CELL_SNAP_M)
    values[valid_flat[reachable]] = seconds[reachable].astype(np.float32) / 60.0
    return values.reshape((grid.height, grid.width))


def build_source_metadata(discovery: dict[str, Any], dft_stats: dict[str, Any], model_stats: dict[str, Any]) -> dict[str, Any]:
    open_roads = discovery["os_open_roads"]
    built_up = discovery["os_open_built_up_areas"]
    dft = discovery["dft_local_a"]
    nh = discovery["national_highways"]
    ons = discovery["ons_lad"]
    return {
        SOURCE_ID: {
            "provider": "Goldilocks Map model using Ordnance Survey, Department for Transport, National Highways and ONS",
            "dataset": "Derived GB driving-time model",
            "resolution": "Road-network routing sampled to the canonical Goldilocks 1 km grid",
            "homepage_url": open_roads["product_url"],
            "licence_name": OGL_NAME,
            "licence_url": OGL_URL,
            "attribution": (
                "Contains OS data © Crown copyright and database right 2026. "
                "Department for Transport and National Highways data licensed under the Open Government Licence v3.0. "
                "Source: Office for National Statistics licensed under the Open Government Licence v.3.0. "
                "Contains OS data © Crown copyright and database right 2025."
            ),
            "derived_product_notice": (
                "Goldilocks travel times are modelled estimates produced by this project. They are not routes or travel-time products "
                "published by Ordnance Survey, DfT, National Highways, ONS or Google."
            ),
            "note": (
                "The routing topology is the generalised OS Open Roads GB network and does not include every navigation restriction, "
                "turn restriction or ferry connection. England SRN traversal costs use observed National Highways historic speeds. "
                "Local A-road costs use DfT road-by-local-authority observed speeds where available; unmatched local A roads use DfT "
                "country urban/rural observations with OS Open Built Up Areas. Other road classes use transparent function/form fallbacks. "
                "Canonical cells are snapped to their nearest Open Roads graph node without an additional driveway/access-time penalty. "
                "Cells more than 20 km from the GB-only Open Roads graph (principally Northern Ireland) and cells whose road component "
                "cannot reach the destination are nodata."
            ),
            "releases": [
                {"label": f"OS Open Roads {open_roads['version']}", "status": "published", "url": open_roads["product_url"]},
                {"label": f"OS Open Built Up Areas {built_up['version']}", "status": "published", "url": built_up["product_url"]},
                {"label": "DfT CGN local A-road tables, 6 August 2026 update", "status": "published", "url": dft["dataset_page"]},
                {"label": f"National Highways TTRT {nh['reporting_period']}", "status": "published", "url": nh["homepage"]},
                {"label": f"ONS LAD {ons['release']}", "status": "published", "url": ons["homepage"]},
            ],
            "source_files": {
                "os_open_roads": open_roads,
                "os_open_built_up_areas": built_up,
                "dft_local_a": dft["files"],
                "ons_lad": ons,
                "national_highways": nh["aggregations"],
            },
            "model_stats": {"dft": dft_stats, **model_stats},
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Derive York/Cambridge weekend and weekday-PM-peak travel-time rasters.")
    parser.add_argument("--discovery", type=Path, default=DEFAULT_DISCOVERY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--grid-manifest", type=Path, default=default_grid_manifest())
    args = parser.parse_args()

    discovery = load_discovery(args.discovery.resolve())
    base_manifest, canonical_mask = load_base_validity_mask(args.grid_manifest)
    grid = grid_from_manifest(base_manifest["grid"])
    print(f"Canonical grid {grid.width}x{grid.height}; {np.count_nonzero(canonical_mask):,} valid land cells")

    open_roads_dir = SOURCE_ROOT / discovery["os_open_roads"]["product_id"] / discovery["os_open_roads"]["version"] / "extracted" / "data"
    bua_path = SOURCE_ROOT / discovery["os_open_built_up_areas"]["product_id"] / discovery["os_open_built_up_areas"]["version"] / "extracted" / "os_open_built_up_areas.gpkg"
    lad_path = SOURCE_ROOT / discovery["ons_lad"]["file"]
    if not open_roads_dir.is_dir() or not bua_path.is_file() or not lad_path.is_file():
        raise RuntimeError("Travel sources are incomplete; run download_travel_sources.py first")

    graph = load_open_roads(open_roads_dir)
    cache_dir = SOURCE_ROOT / "cache" / discovery["os_open_roads"]["version"]
    urban_score = classify_built_up_for_a_edges(graph, bua_path, cache_dir / "a_road_built_up_score.u8.npy")
    lad_index, lad_codes = assign_lad_to_a_edges(graph, lad_path, cache_dir / "a_road_lad_index.i16.npy")
    road_speeds, country_context, dft_stats = load_dft_speeds(discovery)
    weekend_base, peak_base, local_counts = build_local_costs(
        graph, urban_score, lad_index, lad_codes, road_speeds, country_context
    )
    weekend_cost, peak_cost, nh_stats = build_scenario_costs(graph, weekend_base, peak_base, discovery)

    csr = build_csr(graph)
    valid_flat, nearest_nodes, snap_distances = load_or_build_cell_snaps(
        graph, grid, canonical_mask, cache_dir
    )
    source_snap_mask = snap_distances <= MAX_CELL_SNAP_M
    source_snap_distances = snap_distances[source_snap_mask]
    snap_stats = {
        "canonical_valid_cells": int(len(valid_flat)),
        "max_source_snap_m": float(MAX_CELL_SNAP_M),
        "source_covered_cells": int(np.count_nonzero(source_snap_mask)),
        "excluded_cells": int(np.count_nonzero(~source_snap_mask)),
        "median_nearest_node_m": float(np.median(source_snap_distances)),
        "p95_nearest_node_m": float(np.percentile(source_snap_distances, 95)),
        "p99_nearest_node_m": float(np.percentile(source_snap_distances, 99)),
        "max_nearest_node_m": float(np.max(source_snap_distances)),
        "coverage_rule": (
            "A canonical cell is covered only when its centre is within 20 km of an OS Open Roads graph node. "
            "The threshold follows the clean observed gap between GB road-covered cells (<10 km in this build) and "
            "cells outside the GB-only source network (>20 km, principally Northern Ireland)."
        ),
        "access_penalty": "none; the nearest graph-node travel time is assigned to the 1 km cell centre",
    }

    scenario_costs = {"weekend": weekend_cost, "peak": peak_cost}
    grids: dict[tuple[str, str], np.ndarray] = {}
    target_stats: dict[str, Any] = {}
    for target_id, target in TARGETS.items():
        bx, by = BNG.transform(target["lon"], target["lat"])
        target_node, target_snap = nearest_node(graph, bx, by)
        target_stats[target_id] = {
            "label": target["label"], "lat": target["lat"], "lon": target["lon"],
            "bng": [bx, by], "node": target_node, "snap_m": target_snap,
        }
        for scenario, costs in scenario_costs.items():
            print(f"Dijkstra {target['label']} — {scenario}", flush=True)
            dist, settled, elapsed = dijkstra_all(csr, costs, target_node)
            print(f"  settled {settled:,} nodes in {elapsed:.1f}s", flush=True)
            target_stats[target_id][scenario] = {"settled_nodes": settled, "dijkstra_seconds": elapsed}
            grids[(target_id, scenario)] = metric_grid_from_distances(
                dist, valid_flat, nearest_nodes, snap_distances, grid
            )
            del dist

    period = (
        f"OS Open Roads {discovery['os_open_roads']['version']}; DfT local A-road data through March 2026; "
        f"National Highways {discovery['national_highways']['reporting_period']}"
    )
    common_coverage = {
        "routing_topology": "OS Open Roads GB generalised link/node network",
        "cell_snap": snap_stats,
        "local_a_cost_model": {
            "road_level": "DfT 2025 flow-weighted average speed by local authority and road number, used directly where available",
            "fallback": "latest DfT rolling country urban/rural A-road speeds with OS Open Built Up Areas",
            "weekday_peak_adjustment": "country rolling-year weekday evening-peak/all-day speed ratio",
            "counts": local_counts,
        },
        "national_highways": nh_stats,
        "targets": target_stats,
    }

    metrics: list[MetricResult] = []
    for target_id in ("york", "cambridge"):
        target_label = TARGETS[target_id]["label"]
        metrics.append(
            MetricResult(
                id=f"travel_{target_id}_weekend",
                category_id="travel",
                label=f"{target_label} — weekend",
                units="min",
                period=period,
                definition=(
                    f"Modelled driving time in minutes from the nearest OS Open Roads graph node to each canonical 1 km cell centre to {target_label}. "
                    "England Strategic Road Network links use the mean historic link traversal time from National Highways Normal Saturday and Normal Sunday observations. "
                    "Local A roads use DfT road-by-local-authority observed average speeds where available; other road costs use the documented Goldilocks fallbacks. "
                    "This is a typical historic weekend estimate, not live traffic and not turn-by-turn navigation."
                ),
                values=grids[(target_id, "weekend")],
                scale=1.0,
                offset=0.0,
                decimals=0,
                source_id=SOURCE_ID,
                source_variable=f"driving_time_to_{target_id}_weekend",
                coverage={**common_coverage, "scenario": "weekend: mean Normal Saturday/Sunday SRN traversal time"},
                palette=TRAVEL_PALETTE,
                display_range=(0.0, 360.0),
            )
        )
        metrics.append(
            MetricResult(
                id=f"travel_{target_id}_peak",
                category_id="travel",
                label=f"{target_label} — weekday PM peak",
                units="min",
                period=period,
                definition=(
                    f"Modelled weekday evening-peak driving time in minutes from the nearest OS Open Roads graph node to each canonical 1 km cell centre to {target_label}. "
                    "England Strategic Road Network links use National Highways PM Peak historic observations. Local A-road observed annual speeds are adjusted by the latest DfT country-level weekday-evening-peak/all-day speed ratio; "
                    "unmatched local A roads use correspondingly adjusted DfT urban/rural speeds. Other road classes retain the transparent Goldilocks function/form fallbacks. "
                    "This is a representative historic peak estimate, not live traffic and not turn-by-turn navigation."
                ),
                values=grids[(target_id, "peak")],
                scale=1.0,
                offset=0.0,
                decimals=0,
                source_id=SOURCE_ID,
                source_variable=f"driving_time_to_{target_id}_weekday_pm_peak",
                coverage={**common_coverage, "scenario": "weekday PM peak"},
                palette=TRAVEL_PALETTE,
                display_range=(0.0, 360.0),
            )
        )

    reachable_counts = [int(np.count_nonzero(np.isfinite(metric.values) & canonical_mask)) for metric in metrics]
    model_stats = {
        "open_roads_nodes": int(len(graph.x)),
        "open_roads_links": int(len(graph.u)),
        "trunk_links": int(len(graph.trunk_edges)),
        "local_a_cost_counts": local_counts,
        "cell_snap": snap_stats,
        "targets": target_stats,
        "national_highways": nh_stats,
        "reachable_cell_counts": reachable_counts,
    }
    sources = build_source_metadata(discovery, dft_stats, model_stats)
    write_dataset_manifest(
        dataset_id="travel",
        categories=[CATEGORY],
        metrics=metrics,
        grid=grid,
        output_dir=args.output_dir.resolve(),
        sources=sources,
        validity_mask=canonical_mask,
        grid_valid_cell_count=int(np.count_nonzero(canonical_mask)),
        source_valid_cell_count=max(reachable_counts),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
