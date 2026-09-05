#!/usr/bin/env python3
from __future__ import annotations

import gzip
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

GRID_CRS = "EPSG:27700"
BNG_PROJ4 = (
    "+proj=tmerc +lat_0=49 +lon_0=-2 +k=0.9996012717 +x_0=400000 +y_0=-100000 "
    "+ellps=airy +towgs84=446.448,-125.157,542.06,0.1502,0.247,0.8421,-20.4894 "
    "+units=m +no_defs"
)
NODATA_U16 = np.uint16(65535)
DATASET_MANIFEST_VERSION = 3
BUNDLE_MANIFEST_VERSION = 3
DEFAULT_METRIC_PALETTE = (
    "#1548ac",
    "#1796b7",
    "#18c096",
    "#19c84c",
    "#38cf1a",
    "#8ed31a",
    "#d7c71b",
    "#d9721b",
    "#da1b1b",
)


@dataclass(frozen=True)
class GridSpec:
    width: int
    height: int
    cell_size_m: int
    west: float
    south: float
    east: float
    north: float
    bounds_wgs84: list[list[float]]
    x: np.ndarray | None = None
    y: np.ndarray | None = None


@dataclass
class MetricResult:
    id: str
    category_id: str
    label: str
    units: str
    period: str
    definition: str
    values: np.ndarray
    scale: float
    offset: float
    decimals: int
    source_id: str
    source_variable: str
    coverage: dict[str, Any]
    palette: tuple[str, ...] | None = None
    display_range: tuple[float, float] | None = None
    palette_reverse: bool = False


def human_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.2f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def grid_to_manifest(
    grid: GridSpec,
    *,
    valid_cell_count: int | None = None,
    source_valid_cell_count: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "crs": GRID_CRS,
        "proj4": BNG_PROJ4,
        "cell_size_m": int(grid.cell_size_m),
        "width": int(grid.width),
        "height": int(grid.height),
        "cell_count": int(grid.width * grid.height),
        "west": int(round(grid.west)),
        "south": int(round(grid.south)),
        "east": int(round(grid.east)),
        "north": int(round(grid.north)),
        "row_order": "south_to_north",
        "column_order": "west_to_east",
        "bounds_wgs84": grid.bounds_wgs84,
    }
    if valid_cell_count is not None:
        result["valid_cell_count"] = int(valid_cell_count)
    if source_valid_cell_count is not None:
        result["source_valid_cell_count"] = int(source_valid_cell_count)
    return result


def grid_from_manifest(grid: dict[str, Any]) -> GridSpec:
    required = ("width", "height", "cell_size_m", "west", "south", "east", "north", "bounds_wgs84")
    missing = [key for key in required if key not in grid]
    if missing:
        raise RuntimeError(f"Grid manifest is missing fields: {missing}")
    if grid.get("crs") != GRID_CRS:
        raise RuntimeError(f"Expected canonical grid CRS {GRID_CRS}, got {grid.get('crs')!r}")
    return GridSpec(
        width=int(grid["width"]),
        height=int(grid["height"]),
        cell_size_m=int(grid["cell_size_m"]),
        west=float(grid["west"]),
        south=float(grid["south"]),
        east=float(grid["east"]),
        north=float(grid["north"]),
        bounds_wgs84=[[float(v) for v in pair] for pair in grid["bounds_wgs84"]],
    )


def grids_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    keys = (
        "crs",
        "cell_size_m",
        "width",
        "height",
        "west",
        "south",
        "east",
        "north",
        "row_order",
        "column_order",
    )
    return all(left.get(key) == right.get(key) for key in keys)


def encode_metric(
    values: np.ndarray,
    scale: float,
    offset: float,
    *,
    validity_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, int, int]:
    values = np.asarray(values)
    if validity_mask is not None and values.shape != validity_mask.shape:
        raise RuntimeError("Metric grid shape does not match validity mask")
    valid = np.isfinite(values)
    if validity_mask is not None:
        valid &= validity_mask
    if not np.any(valid):
        raise RuntimeError("Metric has no valid cells")
    encoded = np.full(values.shape, NODATA_U16, dtype=np.uint16)
    scaled = (values[valid].astype(np.float64) - offset) / scale
    rounded = np.floor(scaled + 0.5)
    if rounded.size and (rounded.min() < 0 or rounded.max() >= int(NODATA_U16)):
        raise RuntimeError(
            f"Metric encoding range {rounded.min()}..{rounded.max()} does not fit uint16 with nodata={int(NODATA_U16)}"
        )
    encoded[valid] = rounded.astype(np.uint16)
    valid_values = encoded[valid]
    return encoded, int(valid_values.min()), int(valid_values.max())


def build_lod_levels(values: np.ndarray) -> list[np.ndarray]:
    current = np.asarray(values, dtype=np.uint16)
    levels: list[np.ndarray] = []
    while True:
        levels.append(current)
        height, width = current.shape
        if height == 1 and width == 1:
            break
        parent_height = (height + 1) // 2
        parent_width = (width + 1) // 2
        padded = np.full((parent_height * 2, parent_width * 2), NODATA_U16, dtype=np.uint16)
        padded[:height, :width] = current
        valid = padded != NODATA_U16
        blocks_valid = valid.reshape(parent_height, 2, parent_width, 2)
        counts = blocks_valid.sum(axis=(1, 3), dtype=np.uint8)
        sums = np.where(valid, padded, 0).reshape(parent_height, 2, parent_width, 2).sum(
            axis=(1, 3), dtype=np.uint32
        )
        parent = np.full((parent_height, parent_width), NODATA_U16, dtype=np.uint16)
        has_children = counts > 0
        numerator = 2 * sums[has_children] + counts[has_children].astype(np.uint32)
        denominator = 2 * counts[has_children].astype(np.uint32)
        parent[has_children] = (numerator // denominator).astype(np.uint16)
        current = parent
    return levels


def write_metric_blob(
    metric: MetricResult,
    grid: GridSpec,
    output_dir: Path,
    *,
    validity_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    if metric.values.shape != (grid.height, grid.width):
        raise RuntimeError(
            f"Metric {metric.id} has shape {metric.values.shape}; expected {(grid.height, grid.width)}"
        )
    encoded, encoded_min, encoded_max = encode_metric(
        metric.values,
        metric.scale,
        metric.offset,
        validity_mask=validity_mask,
    )
    levels = build_lod_levels(encoded)
    level_meta: list[dict[str, Any]] = []
    raw_parts: list[bytes] = []
    value_offset = 0
    cell_size = grid.cell_size_m
    for level_number, level in enumerate(levels):
        flat = np.asarray(level, dtype="<u2").ravel(order="C")
        raw_parts.append(flat.tobytes())
        level_meta.append(
            {
                "level": level_number,
                "width": int(level.shape[1]),
                "height": int(level.shape[0]),
                "cell_size_m": int(cell_size),
                "value_offset": value_offset,
                "value_count": int(flat.size),
            }
        )
        value_offset += int(flat.size)
        cell_size *= 2
    raw = b"".join(raw_parts)
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    blob_name = f"{metric.id}.u16.gz"
    blob_path = output_dir / blob_name
    blob_path.write_bytes(compressed)
    digest = hashlib.sha256(raw).hexdigest()
    decoded_min = encoded_min * metric.scale + metric.offset
    decoded_max = encoded_max * metric.scale + metric.offset
    valid_cell_count = int(np.count_nonzero(encoded != NODATA_U16))
    print(
        f"{metric.id}: {human_bytes(len(raw))} raw -> {human_bytes(len(compressed))} gzip "
        f"({len(compressed) / len(raw) * 100:.1f}%), {valid_cell_count:,} valid cells, "
        f"range {decoded_min:g}..{decoded_max:g} {metric.units}"
    )
    palette = list(metric.palette or DEFAULT_METRIC_PALETTE)
    if metric.palette_reverse:
        palette.reverse()
    if len(palette) < 2:
        raise RuntimeError(f"Metric {metric.id} palette must contain at least two colours")
    for colour in palette:
        try:
            valid_colour = isinstance(colour, str) and len(colour) == 7 and colour.startswith("#") and int(colour[1:], 16) >= 0
        except ValueError:
            valid_colour = False
        if not valid_colour:
            raise RuntimeError(f"Metric {metric.id} has unsupported palette colour {colour!r}; use #RRGGBB")

    metadata = {
        "id": metric.id,
        "category_id": metric.category_id,
        "label": metric.label,
        "units": metric.units,
        "period": metric.period,
        "definition": metric.definition,
        "source_id": metric.source_id,
        "source_variable": metric.source_variable,
        "scale": metric.scale,
        "offset": metric.offset,
        "decimals": metric.decimals,
        "nodata": int(NODATA_U16),
        "encoded_min": encoded_min,
        "encoded_max": encoded_max,
        "summary": {"min": decoded_min, "max": decoded_max},
        "coverage": metric.coverage,
        "valid_cell_count": valid_cell_count,
        "palette": palette,
        "levels": level_meta,
        "blob_file": blob_name,
        "blob_encoding": "gzip+uint16le",
        "raw_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "sha256_raw": digest,
    }
    if metric.display_range is not None:
        display_min, display_max = metric.display_range
        if not (np.isfinite(display_min) and np.isfinite(display_max) and display_min < display_max):
            raise RuntimeError(f"Metric {metric.id} has invalid display range {metric.display_range!r}")
        min_encoded = int(round((display_min - metric.offset) / metric.scale))
        max_encoded = int(round((display_max - metric.offset) / metric.scale))
        if min_encoded < 0 or max_encoded >= int(NODATA_U16) or min_encoded >= max_encoded:
            raise RuntimeError(f"Metric {metric.id} display range does not fit its uint16 encoding")
        metadata["display_range"] = {"min": float(display_min), "max": float(display_max)}
    return metadata


def write_dataset_manifest(
    *,
    dataset_id: str,
    categories: list[dict[str, Any]],
    metrics: list[MetricResult],
    grid: GridSpec,
    output_dir: Path,
    sources: dict[str, dict[str, Any]],
    validity_mask: np.ndarray | None = None,
    grid_valid_cell_count: int | None = None,
    source_valid_cell_count: int | None = None,
    data_pruning: list[dict[str, Any]] | None = None,
    preview_partial_sources: bool = False,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    if not metrics:
        raise RuntimeError(f"Dataset {dataset_id} has no metrics")
    category_ids = {item.get("id") for item in categories}
    if None in category_ids or len(category_ids) != len(categories):
        raise RuntimeError(f"Dataset {dataset_id} has invalid or duplicate categories")
    source_ids = set(sources)
    for metric in metrics:
        if metric.category_id not in category_ids:
            raise RuntimeError(f"Metric {metric.id} references unknown category {metric.category_id}")
        if metric.source_id not in source_ids:
            raise RuntimeError(f"Metric {metric.id} references unknown source {metric.source_id}")

    metric_meta = [
        write_metric_blob(metric, grid, output_dir, validity_mask=validity_mask)
        for metric in metrics
    ]
    manifest = {
        "format_version": DATASET_MANIFEST_VERSION,
        "kind": "goldilocks-dataset",
        "dataset_id": dataset_id,
        "generated_at_unix": int(time.time()),
        "preview_partial_sources": bool(preview_partial_sources),
        "grid": grid_to_manifest(
            grid,
            valid_cell_count=grid_valid_cell_count,
            source_valid_cell_count=source_valid_cell_count,
        ),
        "categories": categories,
        "metrics": metric_meta,
        "sources": sources,
        "data_pruning": data_pruning or [],
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    referenced_blobs = {metric["blob_file"] for metric in metric_meta}
    for stale_blob in output_dir.glob("*.u16.gz"):
        if stale_blob.name not in referenced_blobs:
            stale_blob.unlink()
    print(f"Wrote {manifest_path} with {len(metric_meta)} metrics")
    return manifest_path


def load_base_validity_mask(manifest_path: Path) -> tuple[dict[str, Any], np.ndarray]:
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metrics = manifest.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise RuntimeError(f"Manifest has no metrics: {manifest_path}")
    metric = metrics[0]
    levels = metric.get("levels")
    if not isinstance(levels, list) or not levels:
        raise RuntimeError(f"First metric has no LOD metadata: {manifest_path}")
    level0 = levels[0]
    blob_path = manifest_path.parent / metric["blob_file"]
    raw = gzip.decompress(blob_path.read_bytes())
    values = np.frombuffer(raw, dtype="<u2")
    start = int(level0["value_offset"])
    stop = start + int(level0["value_count"])
    lod0 = values[start:stop].reshape((int(level0["height"]), int(level0["width"])))
    nodata = int(metric.get("nodata", int(NODATA_U16)))
    return manifest, np.asarray(lod0 != nodata, dtype=bool)
