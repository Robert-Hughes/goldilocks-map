#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
import gzip
import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import h5py
import numpy as np
from pyproj import Transformer

ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = ROOT / "data" / "source" / "hadukgrid"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "derived" / "climate-metrics"
HISTORICAL_ROOT = SOURCE_ROOT / "historical"
PROVISIONAL_ROOT = SOURCE_ROOT / "provisional-2026"

GRID_CRS = "EPSG:27700"
BNG_PROJ4 = (
    "+proj=tmerc +lat_0=49 +lon_0=-2 +k=0.9996012717 +x_0=400000 +y_0=-100000 "
    "+ellps=airy +towgs84=446.448,-125.157,542.06,0.1502,0.247,0.8421,-20.4894 "
    "+units=m +no_defs"
)
NODATA_U16 = np.uint16(65535)
THRESHOLDS_C = (25.0, 28.0, 30.0)
FILENAME_RE = re.compile(
    r"^(?P<variable>tasmax|tasmin)_hadukgrid_uk_1km_day_"
    r"(?P<start>\d{8})-(?P<end>\d{8})\.nc$"
)


@dataclass(frozen=True)
class SourceMonth:
    variable: str
    year: int
    month: int
    path: Path
    status: str

    @property
    def days(self) -> int:
        return calendar.monthrange(self.year, self.month)[1]


@dataclass(frozen=True)
class GridSpec:
    width: int
    height: int
    cell_size_m: int
    x: np.ndarray
    y: np.ndarray
    west: float
    south: float
    east: float
    north: float
    bounds_wgs84: list[list[float]]


@dataclass
class MetricResult:
    id: str
    label: str
    units: str
    period: str
    definition: str
    values: np.ndarray
    scale: float
    offset: float
    decimals: int
    source_variable: str
    coverage: dict


def human_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.2f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def h5_text(value: object) -> str:
    if isinstance(value, (bytes, np.bytes_)):
        return bytes(value).decode("utf-8")
    array = np.asarray(value)
    if array.shape == ():
        item = array.item()
        if isinstance(item, (bytes, np.bytes_)):
            return bytes(item).decode("utf-8")
        return str(item)
    return str(value)


def parse_source_month(path: Path, status: str) -> SourceMonth | None:
    match = FILENAME_RE.match(path.name)
    if not match:
        return None
    start = match.group("start")
    end = match.group("end")
    year = int(start[:4])
    month = int(start[4:6])
    if start[6:8] != "01":
        raise RuntimeError(f"Expected monthly file to begin on day 01: {path}")
    expected_end = calendar.monthrange(year, month)[1]
    if int(end[:4]) != year or int(end[4:6]) != month or int(end[6:8]) != expected_end:
        raise RuntimeError(f"Filename does not span a complete calendar month: {path}")
    return SourceMonth(match.group("variable"), year, month, path, status)


def discover_source_months(
    variable: str,
    *,
    min_year: int,
    max_year: int,
    include_provisional: bool,
) -> list[SourceMonth]:
    months: list[SourceMonth] = []
    roots: list[tuple[Path, str]] = [(HISTORICAL_ROOT / variable, "CEDA v1.3.2.ceda")]
    if include_provisional:
        roots.append((PROVISIONAL_ROOT / variable, "Met Office provisional 2026"))
    for root, status in roots:
        if not root.is_dir():
            continue
        for path in root.glob("*.nc"):
            item = parse_source_month(path, status)
            if item and min_year <= item.year <= max_year:
                months.append(item)
    months.sort(key=lambda item: (item.year, item.month))
    seen: set[tuple[int, int]] = set()
    for item in months:
        key = (item.year, item.month)
        if key in seen:
            raise RuntimeError(f"Duplicate {variable} source month {item.year}-{item.month:02d}")
        seen.add(key)
    return months


def coord_step(values: np.ndarray) -> float:
    diffs = np.diff(np.asarray(values, dtype=float))
    nonzero = np.abs(diffs[np.abs(diffs) > 0])
    if not nonzero.size:
        raise RuntimeError("Unable to determine grid spacing")
    step = float(np.median(nonzero))
    if not np.allclose(nonzero, step):
        raise RuntimeError("Expected regular spatial coordinates")
    return step


def grid_from_file(path: Path, variable: str) -> tuple[GridSpec, np.ndarray]:
    transformer = Transformer.from_crs(GRID_CRS, "EPSG:4326", always_xy=True)
    with h5py.File(path, "r") as h5:
        required = (variable, "projection_x_coordinate", "projection_y_coordinate", "time")
        missing = [name for name in required if name not in h5]
        if missing:
            raise RuntimeError(f"{path.name}: missing datasets {missing}")
        source = h5[variable]
        x = np.asarray(h5["projection_x_coordinate"][...], dtype=np.float64)
        y = np.asarray(h5["projection_y_coordinate"][...], dtype=np.float64)
        width = int(x.size)
        height = int(y.size)
        if source.shape[1:] != (height, width):
            raise RuntimeError(f"{path.name}: unexpected {variable} shape {source.shape}")
        x_step = coord_step(x)
        y_step = coord_step(y)
        if x_step != 1000.0 or y_step != 1000.0:
            raise RuntimeError(f"Expected 1 km grid, got {x_step:g}m x {y_step:g}m")
        if x[1] < x[0] or y[1] < y[0]:
            raise RuntimeError("Expected west-to-east and south-to-north source coordinates")
        west = float(x[0] - x_step / 2)
        east = float(x[-1] + x_step / 2)
        south = float(y[0] - y_step / 2)
        north = float(y[-1] + y_step / 2)
        bounds_wgs84: list[list[float]] = []
        for easting, northing in ((west, south), (east, south), (east, north), (west, north)):
            lon, lat = transformer.transform(easting, northing)
            bounds_wgs84.append([round(lat, 7), round(lon, 7)])

        first = np.empty((height, width), dtype=np.float32)
        source.read_direct(first, source_sel=np.s_[0, :, :])
        valid = np.isfinite(first)
        for attr_name in ("_FillValue", "missing_value"):
            if attr_name in source.attrs:
                for fill in np.asarray(source.attrs[attr_name]).reshape(-1):
                    valid &= first != np.float32(fill)
        land_mask = valid

    return (
        GridSpec(width, height, int(x_step), x, y, west, south, east, north, bounds_wgs84),
        land_mask,
    )


def validate_file_grid(h5: h5py.File, item: SourceMonth, grid: GridSpec) -> h5py.Dataset:
    if item.variable not in h5:
        raise RuntimeError(f"{item.path.name}: missing {item.variable}")
    source = h5[item.variable]
    if source.ndim != 3 or source.shape[1:] != (grid.height, grid.width):
        raise RuntimeError(f"{item.path.name}: unexpected {item.variable} shape {source.shape}")
    if int(source.shape[0]) != item.days:
        raise RuntimeError(f"{item.path.name}: expected {item.days} daily values, found {source.shape[0]}")
    units = h5_text(source.attrs.get("units", "")).strip().lower()
    if units not in {"degc", "degree_celsius", "degrees_celsius", "c", "°c", "celsius", "k", "kelvin"}:
        raise RuntimeError(f"{item.path.name}: unexpected temperature units {units!r}")
    x = np.asarray(h5["projection_x_coordinate"][...], dtype=np.float64)
    y = np.asarray(h5["projection_y_coordinate"][...], dtype=np.float64)
    if not np.array_equal(x, grid.x) or not np.array_equal(y, grid.y):
        raise RuntimeError(f"{item.path.name}: spatial grid differs from first source file")
    return source


def dataset_decode_parameters(source: h5py.Dataset) -> tuple[list[np.float32], np.float32, np.float32, bool]:
    fills: list[np.float32] = []
    for attr_name in ("_FillValue", "missing_value"):
        if attr_name in source.attrs:
            fills.extend(np.float32(value) for value in np.asarray(source.attrs[attr_name]).reshape(-1))
    scale = np.float32(np.asarray(source.attrs.get("scale_factor", 1.0)).item())
    offset = np.float32(np.asarray(source.attrs.get("add_offset", 0.0)).item())
    units = h5_text(source.attrs.get("units", "")).strip().lower()
    kelvin = units in {"k", "kelvin"}
    return fills, scale, offset, kelvin


def read_blocks(source: h5py.Dataset) -> Iterable[tuple[np.ndarray, np.ndarray]]:
    time_count, height, width = source.shape
    block = int(source.chunks[0]) if source.chunks else int(time_count)
    block = max(1, min(block, int(time_count)))
    buffer = np.empty((block, height, width), dtype=np.float32)
    fills, scale, offset, kelvin = dataset_decode_parameters(source)
    for start in range(0, int(time_count), block):
        stop = min(int(time_count), start + block)
        values = buffer[: stop - start]
        source.read_direct(values, source_sel=np.s_[start:stop, :, :])
        valid = np.isfinite(values)
        for fill in fills:
            valid &= values != fill
        if scale != 1.0 or offset != 0.0:
            values *= scale
            values += offset
        if kelvin:
            values -= np.float32(273.15)
        yield values, valid


def coverage_by_month(months: list[SourceMonth]) -> dict[str, int]:
    counts = {str(month): 0 for month in range(1, 13)}
    for item in months:
        counts[str(item.month)] += 1
    return counts


def coverage_label(months: list[SourceMonth]) -> str:
    if not months:
        return "No observations"
    first = months[0]
    last = months[-1]
    if first.year == last.year and first.month == 1 and last.month == 12:
        return str(first.year)
    if first.month == 1 and last.month == 12:
        return f"{first.year}–{last.year}"
    return f"{first.year}-{first.month:02d} to {last.year}-{last.month:02d}"


def ensure_historical_coverage(months: list[SourceMonth], variable: str, allow_partial: bool) -> None:
    expected = {(year, month) for year in range(2016, 2026) for month in range(1, 13)}
    present = {(item.year, item.month) for item in months if 2016 <= item.year <= 2025}
    missing = sorted(expected - present)
    if missing and not allow_partial:
        preview = ", ".join(f"{year}-{month:02d}" for year, month in missing[:8])
        suffix = "..." if len(missing) > 8 else ""
        raise RuntimeError(
            f"Missing {len(missing)} historical {variable} months ({preview}{suffix}). "
            "Wait for the downloader or pass --allow-partial for a preview build."
        )


def ensure_provisional_coverage(tasmax_months: list[SourceMonth], tasmin_months: list[SourceMonth]) -> None:
    tasmax_2026 = {item.month for item in tasmax_months if item.year == 2026}
    tasmin_2026 = {item.month for item in tasmin_months if item.year == 2026}
    if not tasmax_2026 or not tasmin_2026:
        raise RuntimeError("Provisional 2026 data were requested but tasmax/tasmin 2026 months are missing")
    if tasmax_2026 != tasmin_2026:
        raise RuntimeError(
            f"Provisional 2026 tasmax/tasmin coverage differs: tasmax={sorted(tasmax_2026)}, tasmin={sorted(tasmin_2026)}"
        )
    latest = max(tasmax_2026)
    expected = set(range(1, latest + 1))
    if tasmax_2026 != expected:
        raise RuntimeError(f"Provisional 2026 coverage is not contiguous from January: {sorted(tasmax_2026)}")


def process_tasmax(
    months: list[SourceMonth],
    grid: GridSpec,
    land_mask: np.ndarray,
    temp_dir: Path,
) -> list[MetricResult]:
    if not months:
        return []
    month_denominators = coverage_by_month(months)
    threshold_annual = {
        threshold: np.zeros((grid.height, grid.width), dtype=np.float32)
        for threshold in THRESHOLDS_C
    }
    current_run = np.zeros((grid.height, grid.width), dtype=np.uint16)
    longest_run = np.zeros((grid.height, grid.width), dtype=np.uint16)

    summer_months = [item for item in months if item.month in {6, 7, 8}]
    summer_days = sum(item.days for item in summer_months)
    land_indices = np.flatnonzero(land_mask.ravel(order="C"))
    temp_dir.mkdir(parents=True, exist_ok=True)
    summer_path = temp_dir / "summer-tasmax.float32"
    summer = None
    if summer_days:
        summer = np.memmap(summer_path, mode="w+", dtype=np.float32, shape=(summer_days, land_indices.size))
    summer_row = 0

    previous: SourceMonth | None = None
    started = time.perf_counter()
    for file_index, item in enumerate(months, start=1):
        if previous is None or item.year != previous.year or (item.month != previous.month + 1):
            current_run.fill(0)
        previous = item

        monthly_counts = {
            threshold: np.zeros((grid.height, grid.width), dtype=np.uint8)
            for threshold in THRESHOLDS_C
        }
        monthly_valid = np.zeros((grid.height, grid.width), dtype=np.uint8)
        with h5py.File(item.path, "r") as h5:
            source = validate_file_grid(h5, item, grid)
            for values, valid in read_blocks(source):
                block_days = values.shape[0]
                monthly_valid += np.sum(valid, axis=0, dtype=np.uint8)
                for threshold in THRESHOLDS_C:
                    monthly_counts[threshold] += np.sum(
                        valid & (values > threshold), axis=0, dtype=np.uint8
                    )

                for day_index in range(block_days):
                    hot = valid[day_index] & (values[day_index] > 25.0)
                    current_run[~hot] = 0
                    current_run[hot] += 1
                    np.maximum(longest_run, current_run, out=longest_run)

                if summer is not None and item.month in {6, 7, 8}:
                    flat = values.reshape(block_days, -1)
                    summer[summer_row : summer_row + block_days, :] = flat[:, land_indices]
                    summer_row += block_days

        if not np.all(monthly_valid[land_mask] == item.days):
            bad = int(np.count_nonzero(monthly_valid[land_mask] != item.days))
            raise RuntimeError(f"{item.path.name}: {bad} land cells do not have all {item.days} daily observations")

        denominator = int(month_denominators[str(item.month)])
        for threshold in THRESHOLDS_C:
            threshold_annual[threshold] += monthly_counts[threshold].astype(np.float32) / np.float32(denominator)

        elapsed = time.perf_counter() - started
        print(
            f"tasmax {file_index}/{len(months)} {item.year}-{item.month:02d} "
            f"({elapsed:.1f}s elapsed)",
            flush=True,
        )

    results: list[MetricResult] = []
    period = coverage_label(months)
    coverage = {
        "source_months": len(months),
        "by_calendar_month": month_denominators,
        "first_month": f"{months[0].year}-{months[0].month:02d}",
        "last_month": f"{months[-1].year}-{months[-1].month:02d}",
        "method": "Calendar-month means are summed so incomplete 2026 coverage does not count unpublished months as zero.",
    }
    for threshold in THRESHOLDS_C:
        threshold_id = int(threshold)
        results.append(
            MetricResult(
                id=f"days_tmax_gt_{threshold_id}",
                label=f"Days Tmax > {threshold_id}°C",
                units="days/year",
                period=period,
                definition=(
                    f"Expected annual number of days with HadUK-Grid daily maximum temperature strictly above {threshold_id}°C. "
                    "Calculated as the sum of calendar-month means across all available years, so published 2026 months are included without treating unpublished 2026 months as zero."
                ),
                values=threshold_annual[threshold],
                scale=0.1,
                offset=0.0,
                decimals=1,
                source_variable="tasmax",
                coverage=coverage,
            )
        )

    longest_values = longest_run.astype(np.float32)
    results.append(
        MetricResult(
            id="longest_run_tmax_gt_25",
            label="Longest run Tmax > 25°C",
            units="days",
            period=period,
            definition=(
                "Longest observed consecutive run of days with HadUK-Grid daily maximum temperature strictly above 25°C. "
                "Runs reset at calendar-year boundaries and at any gap in the available monthly source record; 2026 is included through the latest published month."
            ),
            values=longest_values,
            scale=1.0,
            offset=0.0,
            decimals=0,
            source_variable="tasmax",
            coverage=coverage,
        )
    )

    if summer is not None and summer_days:
        summer.flush()
        p95 = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
        p99 = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
        flat95 = p95.ravel(order="C")
        flat99 = p99.ravel(order="C")
        chunk_cells = 12_000
        percentile_started = time.perf_counter()
        for start in range(0, land_indices.size, chunk_cells):
            stop = min(land_indices.size, start + chunk_cells)
            block = np.asarray(summer[:, start:stop])
            percentiles = np.percentile(block, [95.0, 99.0], axis=0, method="linear")
            selected_indices = land_indices[start:stop]
            flat95[selected_indices] = percentiles[0].astype(np.float32)
            flat99[selected_indices] = percentiles[1].astype(np.float32)
        percentile_elapsed = time.perf_counter() - percentile_started
        print(f"Summer percentiles over {summer_days} daily rasters: {percentile_elapsed:.1f}s")
        summer_years = sorted({item.year for item in summer_months})
        summer_coverage = {
            "source_months": len(summer_months),
            "daily_observations": summer_days,
            "years": summer_years,
            "months_present": [f"{item.year}-{item.month:02d}" for item in summer_months],
        }
        summer_period = (
            f"Summers {summer_years[0]}–{summer_years[-1]}"
            if len(summer_years) > 1
            else f"Summer {summer_years[0]}"
        )
        results.extend(
            [
                MetricResult(
                    id="summer_tmax_p95",
                    label="Summer Tmax 95th percentile",
                    units="°C",
                    period=summer_period,
                    definition="95th percentile of all available June–August HadUK-Grid daily maximum temperatures in the selected 2016–2026 observation period.",
                    values=p95,
                    scale=0.1,
                    offset=-50.0,
                    decimals=1,
                    source_variable="tasmax",
                    coverage=summer_coverage,
                ),
                MetricResult(
                    id="summer_tmax_p99",
                    label="Summer Tmax 99th percentile",
                    units="°C",
                    period=summer_period,
                    definition="99th percentile of all available June–August HadUK-Grid daily maximum temperatures in the selected 2016–2026 observation period.",
                    values=p99,
                    scale=0.1,
                    offset=-50.0,
                    decimals=1,
                    source_variable="tasmax",
                    coverage=summer_coverage,
                ),
            ]
        )
        del summer
        summer_path.unlink(missing_ok=True)

    return results


def process_tasmin(months: list[SourceMonth], grid: GridSpec, land_mask: np.ndarray) -> list[MetricResult]:
    if not months:
        return []
    month_denominators = coverage_by_month(months)
    annual = np.zeros((grid.height, grid.width), dtype=np.float32)
    started = time.perf_counter()
    for file_index, item in enumerate(months, start=1):
        monthly_count = np.zeros((grid.height, grid.width), dtype=np.uint8)
        monthly_valid = np.zeros((grid.height, grid.width), dtype=np.uint8)
        with h5py.File(item.path, "r") as h5:
            source = validate_file_grid(h5, item, grid)
            for values, valid in read_blocks(source):
                monthly_valid += np.sum(valid, axis=0, dtype=np.uint8)
                monthly_count += np.sum(valid & (values > 20.0), axis=0, dtype=np.uint8)
        if not np.all(monthly_valid[land_mask] == item.days):
            bad = int(np.count_nonzero(monthly_valid[land_mask] != item.days))
            raise RuntimeError(f"{item.path.name}: {bad} land cells do not have all {item.days} daily observations")
        denominator = int(month_denominators[str(item.month)])
        annual += monthly_count.astype(np.float32) / np.float32(denominator)
        elapsed = time.perf_counter() - started
        print(f"tasmin {file_index}/{len(months)} {item.year}-{item.month:02d} ({elapsed:.1f}s elapsed)", flush=True)

    period = coverage_label(months)
    coverage = {
        "source_months": len(months),
        "by_calendar_month": month_denominators,
        "first_month": f"{months[0].year}-{months[0].month:02d}",
        "last_month": f"{months[-1].year}-{months[-1].month:02d}",
        "method": "Calendar-month means are summed so incomplete 2026 coverage does not count unpublished months as zero.",
    }
    return [
        MetricResult(
            id="tropical_nights_tmin_gt_20",
            label="Tropical nights: Tmin > 20°C",
            units="days/year",
            period=period,
            definition=(
                "Expected annual number of days whose HadUK-Grid daily minimum temperature is strictly above 20°C, calculated from calendar-month means across all available years. "
                "HadUK-Grid daily Tmin follows the Met Office observation-day convention rather than a literal sunset-to-sunrise minimum."
            ),
            values=annual,
            scale=0.1,
            offset=0.0,
            decimals=1,
            source_variable="tasmin",
            coverage=coverage,
        )
    ]


def encode_metric(values: np.ndarray, land_mask: np.ndarray, scale: float, offset: float) -> tuple[np.ndarray, int, int]:
    if values.shape != land_mask.shape:
        raise RuntimeError("Metric grid shape does not match validity mask")
    encoded = np.full(values.shape, NODATA_U16, dtype=np.uint16)
    valid = land_mask & np.isfinite(values)
    scaled = (values[valid].astype(np.float64) - offset) / scale
    rounded = np.floor(scaled + 0.5)
    if rounded.size and (rounded.min() < 0 or rounded.max() >= int(NODATA_U16)):
        raise RuntimeError(
            f"Metric encoding range {rounded.min()}..{rounded.max()} does not fit uint16 with nodata={int(NODATA_U16)}"
        )
    encoded[valid] = rounded.astype(np.uint16)
    valid_values = encoded[valid]
    return encoded, int(valid_values.min()), int(valid_values.max())


def build_lod_levels(values: np.ndarray, cell_size_m: int) -> list[np.ndarray]:
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


def write_metric_blob(metric: MetricResult, land_mask: np.ndarray, grid: GridSpec, output_dir: Path) -> dict:
    encoded, encoded_min, encoded_max = encode_metric(metric.values, land_mask, metric.scale, metric.offset)
    levels = build_lod_levels(encoded, grid.cell_size_m)
    level_meta: list[dict] = []
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
    print(
        f"{metric.id}: {human_bytes(len(raw))} raw -> {human_bytes(len(compressed))} gzip "
        f"({len(compressed) / len(raw) * 100:.1f}%), range {decoded_min:g}..{decoded_max:g} {metric.units}"
    )
    return {
        "id": metric.id,
        "label": metric.label,
        "units": metric.units,
        "period": metric.period,
        "definition": metric.definition,
        "source_variable": metric.source_variable,
        "scale": metric.scale,
        "offset": metric.offset,
        "decimals": metric.decimals,
        "nodata": int(NODATA_U16),
        "encoded_min": encoded_min,
        "encoded_max": encoded_max,
        "summary": {"min": decoded_min, "max": decoded_max},
        "coverage": metric.coverage,
        "levels": level_meta,
        "blob_file": blob_name,
        "blob_encoding": "gzip+uint16le",
        "raw_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "sha256_raw": digest,
    }


def build_manifest(
    metrics: list[MetricResult],
    land_mask: np.ndarray,
    grid: GridSpec,
    tasmax_months: list[SourceMonth],
    tasmin_months: list[SourceMonth],
    output_dir: Path,
    allow_partial: bool,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    metric_meta = [write_metric_blob(metric, land_mask, grid, output_dir) for metric in metrics]
    all_months = tasmax_months + tasmin_months
    historical_release = "CEDA HadUK-Grid v1.3.2.ceda" if any(item.status.startswith("CEDA") for item in all_months) else None
    provisional_release = "Met Office provisional 2026" if any(item.status.startswith("Met Office provisional") for item in all_months) else None
    manifest = {
        "format_version": 1,
        "generated_at_unix": int(time.time()),
        "preview_partial_sources": bool(allow_partial),
        "grid": {
            "crs": GRID_CRS,
            "proj4": BNG_PROJ4,
            "cell_size_m": grid.cell_size_m,
            "width": grid.width,
            "height": grid.height,
            "cell_count": grid.width * grid.height,
            "valid_cell_count": int(np.count_nonzero(land_mask)),
            "west": int(round(grid.west)),
            "south": int(round(grid.south)),
            "east": int(round(grid.east)),
            "north": int(round(grid.north)),
            "row_order": "south_to_north",
            "column_order": "west_to_east",
            "bounds_wgs84": grid.bounds_wgs84,
        },
        "metrics": metric_meta,
        "sources": {
            "provider": "Met Office HadUK-Grid",
            "resolution": "1 km daily",
            "historical_release": historical_release,
            "provisional_release": provisional_release,
            "tasmax_months": [
                {"year": item.year, "month": item.month, "status": item.status, "file": item.path.name}
                for item in tasmax_months
            ],
            "tasmin_months": [
                {"year": item.year, "month": item.month, "status": item.status, "file": item.path.name}
                for item in tasmin_months
            ],
            "note": "HadUK-Grid is a gridded/interpolated climate-observation dataset; a grid cell is not a physical thermometer measurement at that exact point.",
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {manifest_path} with {len(metric_meta)} metrics")
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Derive Goldilocks climate metrics from cached HadUK-Grid daily files.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--min-year", type=int, default=2016)
    parser.add_argument("--max-year", type=int, default=2026)
    parser.add_argument("--no-provisional", action="store_true", help="exclude provisional 2026 source files")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="build a preview from currently cached months instead of requiring all 2016-2025 historical months",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    include_provisional = not args.no_provisional
    tasmax_months = discover_source_months(
        "tasmax", min_year=args.min_year, max_year=args.max_year, include_provisional=include_provisional
    )
    tasmin_months = discover_source_months(
        "tasmin", min_year=args.min_year, max_year=args.max_year, include_provisional=include_provisional
    )
    if not tasmax_months and not tasmin_months:
        raise RuntimeError("No complete cached tasmax/tasmin monthly files found")
    ensure_historical_coverage(tasmax_months, "tasmax", args.allow_partial)
    ensure_historical_coverage(tasmin_months, "tasmin", args.allow_partial)
    if include_provisional and args.max_year >= 2026 and not args.allow_partial:
        ensure_provisional_coverage(tasmax_months, tasmin_months)

    first = tasmax_months[0] if tasmax_months else tasmin_months[0]
    grid, land_mask = grid_from_file(first.path, first.variable)
    print(
        f"Grid {grid.width}x{grid.height}, {np.count_nonzero(land_mask):,} valid cells; "
        f"tasmax months={len(tasmax_months)}, tasmin months={len(tasmin_months)}"
    )

    output_dir = args.output_dir.resolve()
    temp_dir = output_dir / "tmp"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        metrics: list[MetricResult] = []
        metrics.extend(process_tasmax(tasmax_months, grid, land_mask, temp_dir))
        metrics.extend(process_tasmin(tasmin_months, grid, land_mask))
        order = {
            "days_tmax_gt_25": 0,
            "days_tmax_gt_28": 1,
            "days_tmax_gt_30": 2,
            "summer_tmax_p95": 3,
            "summer_tmax_p99": 4,
            "longest_run_tmax_gt_25": 5,
            "tropical_nights_tmin_gt_20": 6,
        }
        metrics.sort(key=lambda metric: order.get(metric.id, 999))
        build_manifest(metrics, land_mask, grid, tasmax_months, tasmin_months, output_dir, args.allow_partial)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
