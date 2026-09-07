#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import h5py
import numpy as np
from pyproj import Transformer

from goldilocks_raster import GRID_CRS, GridSpec, MetricResult, write_dataset_manifest

ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = ROOT / "data" / "source" / "hadukgrid"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "derived" / "climate-metrics"
HISTORICAL_ROOT = SOURCE_ROOT / "historical"
PROVISIONAL_ROOT = SOURCE_ROOT / "provisional-2026"

THRESHOLDS_C = (25.0, 28.0, 30.0)
METRIC_CATEGORIES = (
    {"id": "heat", "label": "Heat", "order": 0},
    {"id": "heat_2026", "label": "Heat — 2026", "order": 1},
    {"id": "cold", "label": "Cold", "order": 2},
)

HADUKGRID_DATASET_CITATION = (
    "Met Office; Hollis, D.; Carlisle, E.; Kendon, M.; Packman, S.; Doherty, A. (2026): "
    "HadUK-Grid Gridded Climate Observations on a 1km grid over the UK, v1.3.2.ceda (1836-2025). "
    "NERC EDS Centre for Environmental Data Analysis, 23 June 2026."
)
HADUKGRID_DATASET_DOI_URL = "https://doi.org/10.5285/789b3065d74a4c948ab05d33556c86d0"
HADUKGRID_METHOD_CITATION = (
    "Hollis, D.; McCarthy, M. P.; Kendon, M.; Legg, T.; Simpson, I. (2019): "
    "HadUK-Grid - A new UK dataset of gridded climate observations. Geoscience Data Journal, 6, 151-159."
)
HADUKGRID_METHOD_DOI_URL = "https://doi.org/10.1002/gdj3.78"
HADUKGRID_LICENCE_NAME = "Open Government Licence v3.0"
HADUKGRID_LICENCE_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
HADUKGRID_PROVISIONAL_URL = "https://www.metoffice.gov.uk/hadobs/hadukgrid/"
DERIVED_PRODUCT_NOTICE = (
    "Goldilocks Map climate metrics are derived from HadUK-Grid and are not an official Met Office product."
)
FILENAME_RE = re.compile(
    r"^(?P<variable>tasmax|tasmin)_hadukgrid_uk_1km_day_"
    r"(?P<start>\d{8})-(?P<end>\d{8})\.nc$"
)

# Eight 1 km HadUK-Grid cells covering St Kilda are deliberately excluded from
# derived products. A full 2016-01..2026-08 Tmin/Tmax consistency audit found
# severe interpolation artefacts at every one of these cells; see README.md.
# Coordinates are exact British National Grid cell centres, not a geographic
# bounding box, so no neighbouring Hebridean cells are accidentally removed.
ST_KILDA_PRUNED_CELLS_BNG: tuple[tuple[int, int], ...] = (
    (9500, 898500),
    (8500, 899500),
    (9500, 899500),
    (10500, 899500),
    (8500, 900500),
    (9500, 900500),
    (6500, 901500),
    (15500, 905500),
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
        GridSpec(
            width=width,
            height=height,
            cell_size_m=int(x_step),
            west=west,
            south=south,
            east=east,
            north=north,
            bounds_wgs84=bounds_wgs84,
            x=x,
            y=y,
        ),
        land_mask,
    )


def apply_data_pruning(grid: GridSpec, land_mask: np.ndarray) -> list[dict]:
    """Apply documented source-data QC exclusions before deriving any metric."""
    transformer = Transformer.from_crs(GRID_CRS, "EPSG:4326", always_xy=True)
    pruned_cells: list[dict] = []
    for easting, northing in ST_KILDA_PRUNED_CELLS_BNG:
        x_matches = np.flatnonzero(grid.x == float(easting))
        y_matches = np.flatnonzero(grid.y == float(northing))
        if x_matches.size != 1 or y_matches.size != 1:
            raise RuntimeError(
                f"Configured St Kilda prune cell E{easting} N{northing} is not an exact source-grid centre"
            )
        column = int(x_matches[0])
        row = int(y_matches[0])
        if not land_mask[row, column]:
            raise RuntimeError(
                f"Configured St Kilda prune cell E{easting} N{northing} is not valid in the source land mask"
            )
        land_mask[row, column] = False
        lon, lat = transformer.transform(easting, northing)
        pruned_cells.append(
            {
                "row": row,
                "column": column,
                "easting": easting,
                "northing": northing,
                "latitude": round(float(lat), 7),
                "longitude": round(float(lon), 7),
            }
        )
    return pruned_cells


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
                id=f"heat_days_tmax_gt_{threshold_id}",
                category_id="heat",
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
                source_id="hadukgrid",
                source_variable="tasmax",
                coverage=coverage,
            )
        )

    longest_values = longest_run.astype(np.float32)
    results.append(
        MetricResult(
            id="heat_longest_run_tmax_gt_25",
            category_id="heat",
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
            source_id="hadukgrid",
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
                    id="heat_summer_tmax_p95",
                    category_id="heat",
                    label="Summer Tmax 95th percentile",
                    units="°C",
                    period=summer_period,
                    definition="95th percentile of all available June–August HadUK-Grid daily maximum temperatures in the selected 2016–2026 observation period.",
                    values=p95,
                    scale=0.1,
                    offset=-50.0,
                    decimals=1,
                    source_id="hadukgrid",
                    source_variable="tasmax",
                    coverage=summer_coverage,
                ),
                MetricResult(
                    id="heat_summer_tmax_p99",
                    category_id="heat",
                    label="Summer Tmax 99th percentile",
                    units="°C",
                    period=summer_period,
                    definition="99th percentile of all available June–August HadUK-Grid daily maximum temperatures in the selected 2016–2026 observation period.",
                    values=p99,
                    scale=0.1,
                    offset=-50.0,
                    decimals=1,
                    source_id="hadukgrid",
                    source_variable="tasmax",
                    coverage=summer_coverage,
                ),
            ]
        )
        del summer
        summer_path.unlink(missing_ok=True)

    return results



def process_tasmax_2026(
    months: list[SourceMonth],
    grid: GridSpec,
    land_mask: np.ndarray,
    temp_dir: Path,
) -> list[MetricResult]:
    months = [item for item in months if item.year == 2026]
    if not months:
        return []

    hot_days = np.zeros((grid.height, grid.width), dtype=np.uint16)
    current_run = np.zeros((grid.height, grid.width), dtype=np.uint16)
    longest_run = np.zeros((grid.height, grid.width), dtype=np.uint16)

    summer_months = [item for item in months if item.month in {6, 7, 8}]
    complete_summer = {6, 7, 8} <= {item.month for item in summer_months}
    summer_days = sum(item.days for item in summer_months) if complete_summer else 0
    land_indices = np.flatnonzero(land_mask.ravel(order="C"))
    summer_path = temp_dir / "summer-2026-tasmax.float32"
    summer = None
    if summer_days:
        summer = np.memmap(summer_path, mode="w+", dtype=np.float32, shape=(summer_days, land_indices.size))
    summer_row = 0

    previous: SourceMonth | None = None
    started = time.perf_counter()
    for file_index, item in enumerate(months, start=1):
        if previous is None or item.month != previous.month + 1:
            current_run.fill(0)
        previous = item
        monthly_valid = np.zeros((grid.height, grid.width), dtype=np.uint8)
        with h5py.File(item.path, "r") as h5:
            source = validate_file_grid(h5, item, grid)
            for values, valid in read_blocks(source):
                block_days = values.shape[0]
                monthly_valid += np.sum(valid, axis=0, dtype=np.uint8)
                hot_days += np.sum(valid & (values > 25.0), axis=0, dtype=np.uint16)
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
        elapsed = time.perf_counter() - started
        print(f"tasmax-2026 {file_index}/{len(months)} {item.year}-{item.month:02d} ({elapsed:.1f}s elapsed)", flush=True)

    period = coverage_label(months)
    coverage = {
        "source_months": len(months),
        "daily_observations": sum(item.days for item in months),
        "months_present": [f"{item.year}-{item.month:02d}" for item in months],
        "method": "Observed 2026 count through the latest published month; unpublished later months are not treated as zero and the result is not annualised.",
    }
    results = [
        MetricResult(
            id="heat_2026_days_tmax_gt_25",
            category_id="heat_2026",
            label="2026 days Tmax > 25°C",
            units="days",
            period=period,
            definition=(
                "Observed number of days in the available 2026 period with HadUK-Grid daily maximum temperature strictly above 25°C. "
                "This is a year-to-date count, not an annual estimate; unpublished later months are not treated as zero."
            ),
            values=hot_days.astype(np.float32),
            scale=1.0,
            offset=0.0,
            decimals=0,
            source_id="hadukgrid",
            source_variable="tasmax",
            coverage=coverage,
        ),
        MetricResult(
            id="heat_2026_longest_run_tmax_gt_25",
            category_id="heat_2026",
            label="2026 longest run Tmax > 25°C",
            units="days",
            period=period,
            definition=(
                "Longest observed consecutive run of days in the available 2026 period with HadUK-Grid daily maximum temperature strictly above 25°C. "
                "Runs reset at any gap in the published monthly source record."
            ),
            values=longest_run.astype(np.float32),
            scale=1.0,
            offset=0.0,
            decimals=0,
            source_id="hadukgrid",
            source_variable="tasmax",
            coverage=coverage,
        ),
    ]

    if summer is not None and summer_days:
        if summer_row != summer_days:
            raise RuntimeError(f"Summer 2026 Tmax buffer contains {summer_row} rows; expected {summer_days}")
        summer.flush()
        p95 = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
        flat95 = p95.ravel(order="C")
        chunk_cells = 12_000
        percentile_started = time.perf_counter()
        for start in range(0, land_indices.size, chunk_cells):
            stop = min(land_indices.size, start + chunk_cells)
            block = np.asarray(summer[:, start:stop])
            percentile = np.percentile(block, 95.0, axis=0, method="linear")
            flat95[land_indices[start:stop]] = percentile.astype(np.float32)
        percentile_elapsed = time.perf_counter() - percentile_started
        print(f"Summer 2026 Tmax 95th percentile over {summer_days} daily rasters: {percentile_elapsed:.1f}s")
        results.append(
            MetricResult(
                id="heat_2026_summer_tmax_p95",
                category_id="heat_2026",
                label="Summer 2026 Tmax 95th percentile",
                units="°C",
                period="Summer 2026",
                definition="95th percentile of all June–August 2026 HadUK-Grid daily maximum temperatures.",
                values=p95,
                scale=0.1,
                offset=-50.0,
                decimals=1,
                source_id="hadukgrid",
                source_variable="tasmax",
                coverage={
                    "source_months": 3,
                    "daily_observations": summer_days,
                    "months_present": ["2026-06", "2026-07", "2026-08"],
                    "method": "Complete meteorological summer (June–August) 2026 only.",
                },
            )
        )
        del summer
        summer_path.unlink(missing_ok=True)

    return results


def process_tasmin_2026(
    months: list[SourceMonth],
    grid: GridSpec,
    land_mask: np.ndarray,
) -> list[MetricResult]:
    months = [item for item in months if item.year == 2026]
    if not months:
        return []

    tropical_nights = np.zeros((grid.height, grid.width), dtype=np.uint16)
    started = time.perf_counter()
    for file_index, item in enumerate(months, start=1):
        monthly_valid = np.zeros((grid.height, grid.width), dtype=np.uint8)
        with h5py.File(item.path, "r") as h5:
            source = validate_file_grid(h5, item, grid)
            for values, valid in read_blocks(source):
                monthly_valid += np.sum(valid, axis=0, dtype=np.uint8)
                tropical_nights += np.sum(valid & (values > 20.0), axis=0, dtype=np.uint16)
        if not np.all(monthly_valid[land_mask] == item.days):
            bad = int(np.count_nonzero(monthly_valid[land_mask] != item.days))
            raise RuntimeError(f"{item.path.name}: {bad} land cells do not have all {item.days} daily observations")
        elapsed = time.perf_counter() - started
        print(f"tasmin-2026 {file_index}/{len(months)} {item.year}-{item.month:02d} ({elapsed:.1f}s elapsed)", flush=True)

    period = coverage_label(months)
    return [
        MetricResult(
            id="heat_2026_tropical_nights_tmin_gt_20",
            category_id="heat_2026",
            label="2026 tropical nights: Tmin > 20°C",
            units="days",
            period=period,
            definition=(
                "Observed number of days in the available 2026 period whose HadUK-Grid daily minimum temperature is strictly above 20°C. "
                "This is a year-to-date count, not an annual estimate; HadUK-Grid daily Tmin follows the Met Office observation-day convention rather than a literal sunset-to-sunrise minimum."
            ),
            values=tropical_nights.astype(np.float32),
            scale=1.0,
            offset=0.0,
            decimals=0,
            source_id="hadukgrid",
            source_variable="tasmin",
            coverage={
                "source_months": len(months),
                "daily_observations": sum(item.days for item in months),
                "months_present": [f"{item.year}-{item.month:02d}" for item in months],
                "method": "Observed 2026 count through the latest published month; unpublished later months are not treated as zero and the result is not annualised.",
            },
        )
    ]

def process_tasmin(
    months: list[SourceMonth],
    grid: GridSpec,
    land_mask: np.ndarray,
    temp_dir: Path,
) -> list[MetricResult]:
    if not months:
        return []

    month_denominators = coverage_by_month(months)
    annual_tropical = np.zeros((grid.height, grid.width), dtype=np.float32)
    annual_air_frost = np.zeros((grid.height, grid.width), dtype=np.float32)

    available_months = {(item.year, item.month) for item in months}
    complete_winter_end_years = [
        year
        for year in range(min(item.year for item in months) + 1, max(item.year for item in months) + 1)
        if {(year - 1, 12), (year, 1), (year, 2)} <= available_months
    ]
    winter_month_keys = {
        key
        for year in complete_winter_end_years
        for key in ((year - 1, 12), (year, 1), (year, 2))
    }
    winter_months = [item for item in months if (item.year, item.month) in winter_month_keys]
    winter_days = sum(item.days for item in winter_months)
    land_indices = np.flatnonzero(land_mask.ravel(order="C"))
    winter_path = temp_dir / "winter-tasmin.float32"
    winter = None
    if winter_days:
        winter = np.memmap(winter_path, mode="w+", dtype=np.float32, shape=(winter_days, land_indices.size))
    winter_row = 0

    started = time.perf_counter()
    for file_index, item in enumerate(months, start=1):
        monthly_tropical = np.zeros((grid.height, grid.width), dtype=np.uint8)
        monthly_air_frost = np.zeros((grid.height, grid.width), dtype=np.uint8)
        monthly_valid = np.zeros((grid.height, grid.width), dtype=np.uint8)
        with h5py.File(item.path, "r") as h5:
            source = validate_file_grid(h5, item, grid)
            for values, valid in read_blocks(source):
                block_days = values.shape[0]
                monthly_valid += np.sum(valid, axis=0, dtype=np.uint8)
                monthly_tropical += np.sum(valid & (values > 20.0), axis=0, dtype=np.uint8)
                monthly_air_frost += np.sum(valid & (values < 0.0), axis=0, dtype=np.uint8)
                if winter is not None and (item.year, item.month) in winter_month_keys:
                    flat = values.reshape(block_days, -1)
                    winter[winter_row : winter_row + block_days, :] = flat[:, land_indices]
                    winter_row += block_days
        if not np.all(monthly_valid[land_mask] == item.days):
            bad = int(np.count_nonzero(monthly_valid[land_mask] != item.days))
            raise RuntimeError(f"{item.path.name}: {bad} land cells do not have all {item.days} daily observations")
        denominator = int(month_denominators[str(item.month)])
        annual_tropical += monthly_tropical.astype(np.float32) / np.float32(denominator)
        annual_air_frost += monthly_air_frost.astype(np.float32) / np.float32(denominator)
        elapsed = time.perf_counter() - started
        print(f"tasmin {file_index}/{len(months)} {item.year}-{item.month:02d} ({elapsed:.1f}s elapsed)", flush=True)

    period = coverage_label(months)
    annual_coverage = {
        "source_months": len(months),
        "by_calendar_month": month_denominators,
        "first_month": f"{months[0].year}-{months[0].month:02d}",
        "last_month": f"{months[-1].year}-{months[-1].month:02d}",
        "method": "Calendar-month means are summed so incomplete 2026 coverage does not count unpublished months as zero.",
    }
    results = [
        MetricResult(
            id="heat_tropical_nights_tmin_gt_20",
            category_id="heat",
            label="Tropical nights: Tmin > 20°C",
            units="days/year",
            period=period,
            definition=(
                "Expected annual number of days whose HadUK-Grid daily minimum temperature is strictly above 20°C, calculated from calendar-month means across all available years. "
                "HadUK-Grid daily Tmin follows the Met Office observation-day convention rather than a literal sunset-to-sunrise minimum."
            ),
            values=annual_tropical,
            scale=0.1,
            offset=0.0,
            decimals=1,
            source_id="hadukgrid",
            source_variable="tasmin",
            coverage=annual_coverage,
        ),
        MetricResult(
            id="cold_air_frost_days",
            category_id="cold",
            label="Air-frost days",
            units="days/year",
            period=period,
            definition=(
                "Expected annual number of days with HadUK-Grid daily minimum air temperature strictly below 0°C. "
                "An air frost is defined from Tmin; a day whose maximum temperature remains below freezing is instead an ice day. "
                "Calculated as the sum of calendar-month means across all available years, so published 2026 months are included without treating unpublished months as zero."
            ),
            values=annual_air_frost,
            scale=0.1,
            offset=0.0,
            decimals=1,
            source_id="hadukgrid",
            source_variable="tasmin",
            coverage=annual_coverage,
            palette_reverse=True,
        ),
    ]

    if winter is not None and winter_row != winter_days:
        raise RuntimeError(f"Winter Tmin buffer contains {winter_row} rows; expected {winter_days}")
    if winter is not None and winter_days:
        winter.flush()
        p05 = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
        flat05 = p05.ravel(order="C")
        chunk_cells = 12_000
        percentile_started = time.perf_counter()
        for start_cell in range(0, land_indices.size, chunk_cells):
            stop_cell = min(land_indices.size, start_cell + chunk_cells)
            block = np.asarray(winter[:, start_cell:stop_cell])
            percentile = np.percentile(block, 5.0, axis=0, method="linear")
            selected_indices = land_indices[start_cell:stop_cell]
            flat05[selected_indices] = percentile.astype(np.float32)
        percentile_elapsed = time.perf_counter() - percentile_started
        print(f"Winter Tmin 5th percentile over {winter_days} daily rasters: {percentile_elapsed:.1f}s")

        first_end_year = complete_winter_end_years[0]
        last_end_year = complete_winter_end_years[-1]
        winter_period = (
            f"Winters {first_end_year - 1}–{str(first_end_year)[-2:]} to {last_end_year - 1}–{str(last_end_year)[-2:]}"
            if first_end_year != last_end_year
            else f"Winter {first_end_year - 1}–{str(first_end_year)[-2:]}"
        )
        winter_coverage = {
            "source_months": len(winter_months),
            "daily_observations": winter_days,
            "complete_winter_end_years": complete_winter_end_years,
            "months_present": [f"{item.year}-{item.month:02d}" for item in winter_months],
            "method": "Only complete December–February (DJF) winters are included; partial winters at either end of the source period are excluded.",
        }
        results.append(
            MetricResult(
                id="cold_winter_tmin_p05",
                category_id="cold",
                label="Winter Tmin 5th percentile",
                units="°C",
                period=winter_period,
                definition=(
                    "5th percentile of HadUK-Grid daily minimum temperature across complete December–February winters. "
                    "Only complete DJF winters are used, so the statistic is not biased by partial seasonal coverage."
                ),
                values=p05,
                scale=0.1,
                offset=-50.0,
                decimals=1,
                source_id="hadukgrid",
                source_variable="tasmin",
                coverage=winter_coverage,
            )
        )
        del winter
        winter_path.unlink(missing_ok=True)

    return results


def build_manifest(
    metrics: list[MetricResult],
    land_mask: np.ndarray,
    grid: GridSpec,
    tasmax_months: list[SourceMonth],
    tasmin_months: list[SourceMonth],
    output_dir: Path,
    allow_partial: bool,
    pruned_cells: list[dict],
) -> Path:
    all_months = tasmax_months + tasmin_months
    historical_release = "CEDA HadUK-Grid v1.3.2.ceda" if any(
        item.status.startswith("CEDA") for item in all_months
    ) else None
    provisional_release = "Met Office provisional 2026" if any(
        item.status.startswith("Met Office provisional") for item in all_months
    ) else None
    source_valid_cell_count = int(np.count_nonzero(land_mask)) + len(pruned_cells)
    sources = {
        "hadukgrid": {
            "provider": "Met Office",
            "dataset": "HadUK-Grid gridded climate observations",
            "resolution": "1 km daily",
            "homepage_url": "https://www.metoffice.gov.uk/hadobs/hadukgrid/",
            "licence_name": HADUKGRID_LICENCE_NAME,
            "licence_url": HADUKGRID_LICENCE_URL,
            "citation": HADUKGRID_DATASET_CITATION,
            "citation_url": HADUKGRID_DATASET_DOI_URL,
            "method_citation": HADUKGRID_METHOD_CITATION,
            "method_url": HADUKGRID_METHOD_DOI_URL,
            "derived_product_notice": DERIVED_PRODUCT_NOTICE,
            "note": (
                "HadUK-Grid is a gridded/interpolated climate-observation dataset; "
                "a grid cell is not a physical thermometer measurement at that exact point."
            ),
            "releases": [
                *(
                    [{"label": historical_release, "status": "stable", "url": HADUKGRID_DATASET_DOI_URL}]
                    if historical_release
                    else []
                ),
                *(
                    [{"label": provisional_release, "status": "provisional", "url": HADUKGRID_PROVISIONAL_URL}]
                    if provisional_release
                    else []
                ),
            ],
            "source_files": {
                "tasmax": [
                    {"year": item.year, "month": item.month, "status": item.status, "file": item.path.name}
                    for item in tasmax_months
                ],
                "tasmin": [
                    {"year": item.year, "month": item.month, "status": item.status, "file": item.path.name}
                    for item in tasmin_months
                ],
            },
        }
    }
    data_pruning = [
        {
            "id": "st-kilda-cross-variable-temperature-qc",
            "source_id": "hadukgrid",
            "area": "St Kilda archipelago",
            "action": "Exclude these eight 1 km cells from all HadUK-Grid-derived metrics before LOD construction.",
            "reason": (
                "A full 2016-01 to 2026-08 audit comparing each daily Tmin with the Tmax covering the same "
                "24-hour observation period found severe interpolation inconsistencies at every St Kilda cell. "
                "All UK cells with ordering errors greater than 10°C were these eight cells, and all tropical-night "
                "candidate events with ordering errors greater than 5°C occurred in them."
            ),
            "audit_period": "2016-01 to 2026-08",
            "audit_summary": {
                "source_valid_cells": source_valid_cell_count,
                "pruned_cells": len(pruned_cells),
                "st_kilda_tropical_candidate_events": 160,
                "st_kilda_tropical_candidates_with_any_ordering_error": 152,
                "st_kilda_tropical_candidates_with_gt_5c_ordering_error": 135,
            },
            "cells": pruned_cells,
        }
    ]
    return write_dataset_manifest(
        dataset_id="climate",
        categories=list(METRIC_CATEGORIES),
        metrics=metrics,
        grid=grid,
        output_dir=output_dir,
        sources=sources,
        validity_mask=land_mask,
        grid_valid_cell_count=int(np.count_nonzero(land_mask)),
        source_valid_cell_count=source_valid_cell_count,
        data_pruning=data_pruning,
        preview_partial_sources=allow_partial,
    )


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
    source_valid_cell_count = int(np.count_nonzero(land_mask))
    pruned_cells = apply_data_pruning(grid, land_mask)
    print(
        f"Grid {grid.width}x{grid.height}, {np.count_nonzero(land_mask):,} retained valid cells "
        f"({source_valid_cell_count:,} source-valid; {len(pruned_cells)} St Kilda cells pruned); "
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
        metrics.extend(process_tasmax_2026(tasmax_months, grid, land_mask, temp_dir))
        metrics.extend(process_tasmin(tasmin_months, grid, land_mask, temp_dir))
        metrics.extend(process_tasmin_2026(tasmin_months, grid, land_mask))
        order = {
            "heat_days_tmax_gt_25": 0,
            "heat_days_tmax_gt_28": 1,
            "heat_days_tmax_gt_30": 2,
            "heat_summer_tmax_p95": 3,
            "heat_summer_tmax_p99": 4,
            "heat_longest_run_tmax_gt_25": 5,
            "heat_tropical_nights_tmin_gt_20": 6,
            "heat_2026_days_tmax_gt_25": 7,
            "heat_2026_longest_run_tmax_gt_25": 8,
            "heat_2026_summer_tmax_p95": 9,
            "heat_2026_tropical_nights_tmin_gt_20": 10,
            "cold_air_frost_days": 11,
            "cold_winter_tmin_p05": 12,
        }
        metrics.sort(key=lambda metric: order.get(metric.id, 999))
        build_manifest(
            metrics,
            land_mask,
            grid,
            tasmax_months,
            tasmin_months,
            output_dir,
            args.allow_partial,
            pruned_cells,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
