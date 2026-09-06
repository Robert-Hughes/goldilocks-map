#!/usr/bin/env python3
from __future__ import annotations

import heapq
import json
import math
import struct
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import shapely
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree


FALLBACK_MPH = {
    "Motorway": 60.0,
    "A Road": 44.0,
    "B Road": 35.0,
    "Minor Road": 30.0,
    "Local Road": 25.0,
    "Restricted Local Access Road": 16.0,
    "Secondary Access Road": 20.0,
    "Local Access Road": 16.0,
    "Guided Busway": 15.0,
}

FLAG_DUAL = 1
FLAG_ROUNDABOUT = 2
FLAG_SLIP = 4

FUNCTIONS = sorted(FALLBACK_MPH)
FUNC_TO_CODE = {name: i for i, name in enumerate(FUNCTIONS)}
CODE_TO_FUNC = {i: name for name, i in FUNC_TO_CODE.items()}
UNKNOWN_FUNC = 255


@dataclass
class RoadGraph:
    x: np.ndarray
    y: np.ndarray
    u: np.ndarray
    v: np.ndarray
    length_m: np.ndarray
    function_code: np.ndarray
    form_flags: np.ndarray
    trunk_mask: np.ndarray
    base_cost_s: np.ndarray
    road_code: np.ndarray
    code_to_road: list[str]
    trunk_edges: np.ndarray
    trunk_roads: list[str]


@dataclass
class CsrGraph:
    offsets: np.ndarray
    neighbours: np.ndarray
    edge_ids: np.ndarray


def dbf_layout(path: Path) -> tuple[int, int, int, dict[str, tuple[int, int]]]:
    with path.open("rb") as handle:
        head = handle.read(32)
        if len(head) != 32:
            raise RuntimeError(f"Invalid DBF header: {path}")
        count = struct.unpack_from("<I", head, 4)[0]
        header_len = struct.unpack_from("<H", head, 8)[0]
        record_len = struct.unpack_from("<H", head, 10)[0]
        fields: list[tuple[str, int, int]] = []
        offset = 1
        while handle.tell() < header_len - 1:
            descriptor = handle.read(32)
            if not descriptor or descriptor[0] == 0x0D:
                break
            name = descriptor[:11].split(b"\0", 1)[0].decode("ascii")
            length = descriptor[16]
            fields.append((name, offset, length))
            offset += length
    return count, header_len, record_len, {name: (off, length) for name, off, length in fields}


def iter_dbf(path: Path, wanted: Iterable[str]):
    wanted = list(wanted)
    count, header_len, record_len, fields = dbf_layout(path)
    missing = [name for name in wanted if name not in fields]
    if missing:
        raise RuntimeError(f"{path} is missing DBF fields {missing}")
    spec = [(name, *fields[name]) for name in wanted]
    with path.open("rb") as handle:
        handle.seek(header_len)
        for _ in range(count):
            record = handle.read(record_len)
            if len(record) != record_len:
                break
            if record[0] == 0x2A:
                continue
            values = []
            for _, offset, length in spec:
                values.append(record[offset : offset + length].decode("latin1").strip())
            yield values


def iter_point_shp(path: Path):
    with path.open("rb") as handle:
        handle.seek(100)
        while True:
            record_header = handle.read(8)
            if not record_header:
                break
            if len(record_header) != 8:
                raise RuntimeError(f"Truncated shapefile record header: {path}")
            _, words = struct.unpack(">II", record_header)
            content = handle.read(words * 2)
            if len(content) != words * 2:
                raise RuntimeError(f"Truncated shapefile record: {path}")
            shape_type = struct.unpack_from("<I", content, 0)[0]
            if shape_type == 0:
                yield None
            elif shape_type in (1, 11, 21):
                yield struct.unpack_from("<dd", content, 4)
            else:
                raise RuntimeError(f"Unexpected point shape type {shape_type} in {path}")


def norm_road(value: str | None) -> str:
    return "".join((value or "").upper().split())


def mph_to_seconds(length_m: int, mph: float) -> int:
    if not math.isfinite(mph) or mph <= 0:
        raise ValueError(f"Invalid road speed {mph!r}")
    return max(1, min(65534, int(round(length_m / (mph * 0.44704)))))


def form_flags(form: str) -> int:
    flags = 0
    if form in ("Dual Carriageway", "Collapsed Dual Carriageway"):
        flags |= FLAG_DUAL
    if form == "Roundabout":
        flags |= FLAG_ROUNDABOUT
    if form == "Slip Road":
        flags |= FLAG_SLIP
    return flags


def fallback_speed(function: str, form: str) -> float:
    mph = FALLBACK_MPH.get(function, 24.0)
    if function != "Motorway" and form in ("Dual Carriageway", "Collapsed Dual Carriageway"):
        mph += 5.0
    if form == "Roundabout":
        mph = min(mph, 20.0)
    if form == "Slip Road":
        mph = min(mph, 35.0)
    return mph


def load_open_roads(data_dir: Path) -> RoadGraph:
    node_dbfs = sorted(data_dir.glob("*_RoadNode.dbf"))
    link_dbfs = sorted(data_dir.glob("*_RoadLink.dbf"))
    if not node_dbfs or not link_dbfs:
        raise RuntimeError(f"OS Open Roads shapefiles were not found under {data_dir}")

    node_ids: dict[str, int] = {}
    xs: list[float] = []
    ys: list[float] = []
    duplicate_nodes = 0
    started = time.perf_counter()
    for index, dbf in enumerate(node_dbfs, start=1):
        for values, xy in zip(iter_dbf(dbf, ["identifier"]), iter_point_shp(dbf.with_suffix(".shp"))):
            if xy is None:
                continue
            identifier = values[0]
            if identifier in node_ids:
                duplicate_nodes += 1
                continue
            node_ids[identifier] = len(xs)
            xs.append(float(xy[0]))
            ys.append(float(xy[1]))
        if index % 10 == 0 or index == len(node_dbfs):
            print(f"Open Roads nodes {index}/{len(node_dbfs)}: {len(xs):,} unique", flush=True)
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    del xs, ys
    print(
        f"Loaded {len(x):,} Open Roads nodes ({duplicate_nodes:,} duplicate records ignored) "
        f"in {time.perf_counter() - started:.1f}s",
        flush=True,
    )

    total_links = sum(dbf_layout(path)[0] for path in link_dbfs)
    u = np.empty(total_links, dtype=np.uint32)
    v = np.empty(total_links, dtype=np.uint32)
    length = np.empty(total_links, dtype=np.uint32)
    function_code = np.empty(total_links, dtype=np.uint8)
    flags = np.empty(total_links, dtype=np.uint8)
    trunk_mask = np.empty(total_links, dtype=np.bool_)
    base_cost = np.empty(total_links, dtype=np.uint16)
    road_code = np.empty(total_links, dtype=np.uint16)

    road_to_code = {"": 0}
    code_to_road = [""]
    trunk_edges: list[int] = []
    trunk_roads: list[str] = []
    functions = Counter()
    missing_nodes = 0
    edge_count = 0
    started = time.perf_counter()
    wanted = ["startNode", "endNode", "length", "function", "formOfWay", "trunkRoad", "roadNumber"]
    for index, dbf in enumerate(link_dbfs, start=1):
        for sn, en, length_text, function, form, trunk, road in iter_dbf(dbf, wanted):
            start_node = node_ids.get(sn)
            end_node = node_ids.get(en)
            if start_node is None or end_node is None or start_node == end_node:
                missing_nodes += 1
                continue
            try:
                length_m = max(1, int(float(length_text)))
            except ValueError:
                continue
            road_number = norm_road(road)
            rc = road_to_code.get(road_number)
            if rc is None:
                rc = len(code_to_road)
                if rc >= 65535:
                    raise RuntimeError("Too many distinct Open Roads road numbers for uint16 encoding")
                road_to_code[road_number] = rc
                code_to_road.append(road_number)

            u[edge_count] = start_node
            v[edge_count] = end_node
            length[edge_count] = length_m
            function_code[edge_count] = FUNC_TO_CODE.get(function, UNKNOWN_FUNC)
            flags[edge_count] = form_flags(form)
            is_trunk = trunk.lower() == "true"
            trunk_mask[edge_count] = is_trunk
            base_cost[edge_count] = mph_to_seconds(length_m, fallback_speed(function, form))
            road_code[edge_count] = rc
            functions[function] += 1
            if is_trunk and road_number:
                trunk_edges.append(edge_count)
                trunk_roads.append(road_number)
            edge_count += 1
        if index % 10 == 0 or index == len(link_dbfs):
            print(
                f"Open Roads links {index}/{len(link_dbfs)}: {edge_count:,} usable; "
                f"{len(trunk_edges):,} marked trunk",
                flush=True,
            )
    del node_ids

    arrays = (u, v, length, function_code, flags, trunk_mask, base_cost, road_code)
    u, v, length, function_code, flags, trunk_mask, base_cost, road_code = [item[:edge_count] for item in arrays]
    print(
        f"Loaded {edge_count:,} Open Roads links ({missing_nodes:,} missing/self links ignored) "
        f"in {time.perf_counter() - started:.1f}s; functions={dict(functions)}",
        flush=True,
    )
    return RoadGraph(
        x=x,
        y=y,
        u=u,
        v=v,
        length_m=length,
        function_code=function_code,
        form_flags=flags,
        trunk_mask=trunk_mask,
        base_cost_s=base_cost,
        road_code=road_code,
        code_to_road=code_to_road,
        trunk_edges=np.asarray(trunk_edges, dtype=np.uint32),
        trunk_roads=trunk_roads,
    )


def build_csr(graph: RoadGraph) -> CsrGraph:
    node_count = len(graph.x)
    degree = np.bincount(np.concatenate((graph.u, graph.v)), minlength=node_count).astype(np.uint32)
    offsets = np.empty(node_count + 1, dtype=np.uint32)
    offsets[0] = 0
    np.cumsum(degree, dtype=np.uint32, out=offsets[1:])
    neighbours = np.empty(len(graph.u) * 2, dtype=np.uint32)
    edge_ids = np.empty(len(graph.u) * 2, dtype=np.uint32)
    cursor = offsets[:-1].copy()
    started = time.perf_counter()
    for edge_id in range(len(graph.u)):
        a = int(graph.u[edge_id])
        b = int(graph.v[edge_id])
        pos = int(cursor[a])
        neighbours[pos] = b
        edge_ids[pos] = edge_id
        cursor[a] += 1
        pos = int(cursor[b])
        neighbours[pos] = a
        edge_ids[pos] = edge_id
        cursor[b] += 1
    print(f"Built CSR graph with {len(neighbours):,} directed arcs in {time.perf_counter()-started:.1f}s", flush=True)
    return CsrGraph(offsets=offsets, neighbours=neighbours, edge_ids=edge_ids)


def dijkstra_all(csr: CsrGraph, edge_cost_s: np.ndarray, target_node: int) -> tuple[np.ndarray, int, float]:
    node_count = len(csr.offsets) - 1
    inf = np.uint32(0xFFFFFFFF)
    dist = np.full(node_count, inf, dtype=np.uint32)
    dist[target_node] = 0
    heap: list[tuple[int, int]] = [(0, target_node)]
    settled = 0
    started = time.perf_counter()
    while heap:
        distance, node = heapq.heappop(heap)
        if distance != int(dist[node]):
            continue
        settled += 1
        start = int(csr.offsets[node])
        end = int(csr.offsets[node + 1])
        for pos in range(start, end):
            neighbour = int(csr.neighbours[pos])
            edge_id = int(csr.edge_ids[pos])
            candidate = distance + int(edge_cost_s[edge_id])
            if candidate < int(dist[neighbour]):
                dist[neighbour] = candidate
                heapq.heappush(heap, (candidate, neighbour))
    elapsed = time.perf_counter() - started
    return dist, settled, elapsed


def nearest_node(graph: RoadGraph, x: float, y: float) -> tuple[int, float]:
    squared = (graph.x - x) ** 2 + (graph.y - y) ** 2
    index = int(np.argmin(squared))
    return index, math.sqrt(float(squared[index]))


def nearest_nodes_for_points(
    graph: RoadGraph,
    xs: np.ndarray,
    ys: np.ndarray,
    *,
    batch_size: int = 100_000,
) -> tuple[np.ndarray, np.ndarray]:
    if xs.shape != ys.shape:
        raise ValueError("xs and ys must have the same shape")
    print(f"Building node spatial index for {len(graph.x):,} road nodes", flush=True)
    started = time.perf_counter()
    road_points = shapely.points(graph.x, graph.y)
    tree = STRtree(road_points)
    print(f"Node STRtree built in {time.perf_counter()-started:.1f}s", flush=True)

    nearest = np.empty(xs.size, dtype=np.uint32)
    distances = np.empty(xs.size, dtype=np.float32)
    started = time.perf_counter()
    for start in range(0, xs.size, batch_size):
        end = min(xs.size, start + batch_size)
        query = shapely.points(xs[start:end], ys[start:end])
        pairs, batch_dist = tree.query_nearest(query, all_matches=False, return_distance=True)
        if pairs.shape[1] != end - start:
            raise RuntimeError(
                f"Nearest-node query returned {pairs.shape[1]} matches for {end-start} query points"
            )
        order = np.argsort(pairs[0], kind="stable")
        query_indices = pairs[0][order]
        if not np.array_equal(query_indices, np.arange(end - start, dtype=query_indices.dtype)):
            raise RuntimeError("Nearest-node query did not return exactly one match for every query point")
        nearest[start:end] = pairs[1][order].astype(np.uint32)
        distances[start:end] = batch_dist[order].astype(np.float32)
        print(f"Snapped {end:,}/{xs.size:,} canonical cells to road nodes", flush=True)
    print(f"Canonical-cell snapping completed in {time.perf_counter()-started:.1f}s", flush=True)
    return nearest, distances


def load_nh_index(path: Path) -> dict[str, tuple[STRtree, list[tuple[LineString, float, int]]]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    features = document.get("features")
    if not isinstance(features, list):
        raise RuntimeError(f"National Highways cache has no features list: {path}")
    groups: dict[str, list[tuple[LineString, float, int]]] = defaultdict(list)
    for feature in features:
        attributes = feature.get("attributes", {})
        road_number = norm_road(attributes.get("roadnumber"))
        speed = attributes.get("averagespeedmph")
        paths = feature.get("geometry", {}).get("paths", [])
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            continue
        if not road_number or not math.isfinite(speed) or speed <= 1:
            continue
        link_id = int(attributes.get("linkid") or 0)
        for path_coords in paths:
            if len(path_coords) >= 2:
                line = LineString([(float(x), float(y)) for x, y, *_ in path_coords])
                groups[road_number].append((line, speed, link_id))
    result: dict[str, tuple[STRtree, list[tuple[LineString, float, int]]]] = {}
    for road_number, values in groups.items():
        result[road_number] = (STRtree([item[0] for item in values]), values)
    return result


def apply_nh_observed_costs(
    base_cost_s: np.ndarray,
    graph: RoadGraph,
    indexes: dict[str, tuple[STRtree, list[tuple[LineString, float, int]]]],
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    cost = base_cost_s.copy()
    matched_mask = np.zeros(len(graph.u), dtype=np.bool_)
    matched_length_m = 0
    distances: list[float] = []
    candidate_counts = Counter()
    for index, edge0 in enumerate(graph.trunk_edges):
        edge = int(edge0)
        road_number = graph.trunk_roads[index]
        indexed = indexes.get(road_number)
        if indexed is None:
            continue
        tree, values = indexed
        midpoint = Point(
            (graph.x[int(graph.u[edge])] + graph.x[int(graph.v[edge])]) * 0.5,
            (graph.y[int(graph.u[edge])] + graph.y[int(graph.v[edge])]) * 0.5,
        )
        candidates = tree.query(midpoint, predicate="dwithin", distance=80.0)
        if len(candidates) == 0:
            nearest = tree.nearest(midpoint)
            if nearest is None:
                continue
            candidates = np.asarray([nearest], dtype=np.int64)
        scored: list[tuple[float, float]] = []
        for candidate0 in candidates:
            candidate = int(candidate0)
            distance = values[candidate][0].distance(midpoint)
            if distance <= 120.0:
                scored.append((distance, values[candidate][1]))
        if not scored:
            continue
        min_distance = min(item[0] for item in scored)
        speeds = [speed for distance, speed in scored if distance <= min_distance + 15.0]
        if not speeds:
            continue
        mph = float(np.mean(speeds))
        if mph < 5 or mph > 85:
            continue
        cost[edge] = mph_to_seconds(int(graph.length_m[edge]), mph)
        matched_mask[edge] = True
        matched_length_m += int(graph.length_m[edge])
        distances.append(min_distance)
        candidate_counts[len(speeds)] += 1
    stats = {
        "matched_edges": int(np.count_nonzero(matched_mask)),
        "trunk_edges": int(len(graph.trunk_edges)),
        "matched_length_km": matched_length_m / 1000.0,
        "median_match_distance_m": float(np.median(distances)) if distances else None,
        "p95_match_distance_m": float(np.percentile(distances, 95)) if distances else None,
        "candidate_speed_count": {str(key): int(value) for key, value in sorted(candidate_counts.items())},
    }
    return cost, matched_mask, stats


def mean_costs_where_observed(
    base_cost_s: np.ndarray,
    variants: list[tuple[np.ndarray, np.ndarray]],
) -> tuple[np.ndarray, np.ndarray]:
    if not variants:
        raise ValueError("At least one observed cost variant is required")
    result = base_cost_s.astype(np.uint32).copy()
    total = np.zeros(len(base_cost_s), dtype=np.uint32)
    count = np.zeros(len(base_cost_s), dtype=np.uint8)
    for costs, matched in variants:
        total[matched] += costs[matched].astype(np.uint32)
        count[matched] += 1
    observed = count > 0
    result[observed] = (total[observed] + count[observed].astype(np.uint32) // 2) // count[observed].astype(np.uint32)
    return result.astype(np.uint16), observed
