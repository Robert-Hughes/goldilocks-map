#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

from download_pollution_sources import PCM_PAGE_URL, SOURCE_ROOT, expected_filename
from goldilocks_raster import (
    GridSpec,
    MetricResult,
    grid_from_manifest,
    load_base_validity_mask,
    write_dataset_manifest,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = ROOT / "data" / "derived" / "pollution-metrics"
DEFAULT_YEARS = (2022, 2023, 2024)
CATEGORY = {"id": "pollution", "label": "Pollution", "order": 3}
OGL_NAME = "Open Government Licence v3.0"
OGL_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"

VARIABLES = {
    "pm25": {
        "metric_id": "pollution_pm25_annual_mean",
        "label": "PM2.5 annual mean",
        "units": "µg/m³",
        "source_variable": "PM2.5 annual mean",
        "definition": (
            "Three-year arithmetic mean of Defra Pollution Climate Mapping (PCM) modelled background "
            "annual mean PM2.5 concentration for 2022–2024."
        ),
    },
    "no2": {
        "metric_id": "pollution_no2_annual_mean",
        "label": "NO₂ annual mean",
        "units": "µg/m³",
        "source_variable": "NO2 annual mean",
        "definition": (
            "Three-year arithmetic mean of Defra Pollution Climate Mapping (PCM) modelled background "
            "annual mean nitrogen dioxide (NO₂) concentration for 2022–2024."
        ),
    },
    "pm10": {
        "metric_id": "pollution_pm10_annual_mean",
        "label": "PM10 annual mean",
        "units": "µg/m³",
        "source_variable": "PM10 annual mean",
        "definition": (
            "Three-year arithmetic mean of Defra Pollution Climate Mapping (PCM) modelled background "
            "annual mean PM10 concentration for 2022–2024."
        ),
    },
    "ozone_dgt120": {
        "metric_id": "pollution_ozone_dgt120_days",
        "label": "Ozone exceedance days",
        "units": "days/year",
        "source_variable": "Ozone DGT120 exceedance days",
        "definition": (
            "Three-year arithmetic mean of the annual number of days where Defra PCM's daily maximum "
            "running 8-hour mean ozone concentration exceeds 120 µg/m³, using 2022–2024 grids."
        ),
    },
}


def default_grid_manifest() -> Path:
    candidates = (
        ROOT / "data" / "derived" / "climate-metrics" / "manifest.json",
        ROOT / "published-data" / "climate-metrics" / "manifest.json",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def inspect_pcm_header(path: Path) -> tuple[int, list[str], list[list[str]]]:
    metadata: list[list[str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        for line_number, row in enumerate(reader):
            cells = [cell.strip() for cell in row]
            lowered = [cell.lower() for cell in cells]
            if len(lowered) >= 4 and lowered[:3] == ["gridcode", "x", "y"]:
                return line_number, cells, metadata
            metadata.append(cells)
            if line_number > 30:
                break
    raise RuntimeError(f"Could not locate PCM grid header in {path}")


def load_pcm_grid(path: Path, grid: GridSpec, canonical_mask: np.ndarray) -> tuple[np.ndarray, dict]:
    header_line, header, metadata = inspect_pcm_header(path)
    raw = np.genfromtxt(
        path,
        delimiter=",",
        skip_header=header_line + 1,
        usecols=(1, 2, 3),
        dtype=np.float64,
        missing_values="MISSING",
        filling_values=np.nan,
    )
    if raw.ndim != 2 or raw.shape[1] != 3:
        raise RuntimeError(f"Unexpected PCM table shape in {path}: {raw.shape}")
    x = raw[:, 0]
    y = raw[:, 1]
    source_values = raw[:, 2]
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise RuntimeError(f"Non-finite PCM coordinates in {path}")

    columns = np.floor((x - grid.west) / grid.cell_size_m).astype(np.int64)
    rows = np.floor((y - grid.south) / grid.cell_size_m).astype(np.int64)
    inside = (
        (columns >= 0)
        & (columns < grid.width)
        & (rows >= 0)
        & (rows < grid.height)
    )
    if not np.all(inside):
        raise RuntimeError(f"{path.name}: {np.count_nonzero(~inside)} PCM cells fall outside the Goldilocks grid")

    expected_x = grid.west + (columns.astype(np.float64) + 0.5) * grid.cell_size_m
    expected_y = grid.south + (rows.astype(np.float64) + 0.5) * grid.cell_size_m
    if not np.allclose(x, expected_x, atol=0.01) or not np.allclose(y, expected_y, atol=0.01):
        raise RuntimeError(f"{path.name}: PCM coordinates are not exact centres of the canonical 1 km grid")

    flat_indices = rows * grid.width + columns
    if np.unique(flat_indices).size != flat_indices.size:
        raise RuntimeError(f"{path.name}: duplicate PCM grid cells detected")

    values = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
    values.ravel(order="C")[flat_indices] = source_values.astype(np.float32)
    values[~canonical_mask] = np.nan
    source_valid = int(np.count_nonzero(np.isfinite(source_values)))
    canonical_valid = int(np.count_nonzero(np.isfinite(values)))
    info = {
        "header": header,
        "metadata": metadata,
        "source_rows": int(raw.shape[0]),
        "source_valid_values": source_valid,
        "canonical_valid_values": canonical_valid,
    }
    return values, info


def derive_metric(
    variable: str,
    years: tuple[int, ...],
    grid: GridSpec,
    canonical_mask: np.ndarray,
) -> tuple[MetricResult, list[dict]]:
    config = VARIABLES[variable]
    annual: list[np.ndarray] = []
    file_info: list[dict] = []
    for year in years:
        filename = expected_filename(variable, year)
        path = SOURCE_ROOT / str(year) / filename
        if not path.is_file():
            raise RuntimeError(
                f"Missing PCM source {path}. Run download_pollution_sources.py before processing pollution metrics."
            )
        values, info = load_pcm_grid(path, grid, canonical_mask)
        annual.append(values)
        file_info.append({"year": year, "file": filename, **info})
        print(
            f"{variable:12s} {year}: {info['source_rows']:,} source rows; "
            f"{info['canonical_valid_values']:,} cells on canonical land mask"
        )

    stack = np.stack(annual, axis=0)
    all_years_valid = canonical_mask & np.all(np.isfinite(stack), axis=0)
    mean = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
    mean[all_years_valid] = np.mean(stack[:, all_years_valid], axis=0, dtype=np.float64).astype(np.float32)
    first_year, last_year = years[0], years[-1]
    period = f"{first_year}–{last_year} mean"
    definition = config["definition"].replace("Three-year", f"{len(years)}-year").replace("2022–2024", f"{first_year}–{last_year}")
    coverage = {
        "years": list(years),
        "method": "Arithmetic mean across annual PCM grids; a cell is included only when all selected years have a value.",
        "valid_cell_count": int(np.count_nonzero(all_years_valid)),
        "source_files": [
            {
                "year": item["year"],
                "file": item["file"],
                "source_rows": item["source_rows"],
                "canonical_valid_values": item["canonical_valid_values"],
                "source_column": item["header"][3] if len(item["header"]) > 3 else None,
            }
            for item in file_info
        ],
    }
    metric = MetricResult(
        id=config["metric_id"],
        category_id="pollution",
        label=config["label"],
        units=config["units"],
        period=period,
        definition=definition,
        values=mean,
        scale=0.1,
        offset=0.0,
        decimals=1,
        source_id="defra-pcm",
        source_variable=config["source_variable"],
        coverage=coverage,
    )
    return metric, file_info


def parse_years(value: str) -> tuple[int, ...]:
    years = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not years:
        raise argparse.ArgumentTypeError("at least one year is required")
    if tuple(sorted(set(years))) != years:
        raise argparse.ArgumentTypeError("years must be unique and sorted")
    return years


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Derive Goldilocks Pollution metrics from cached Defra UK-AIR PCM 1 km grids."
    )
    parser.add_argument("--years", type=parse_years, default=DEFAULT_YEARS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--grid-manifest", type=Path, default=default_grid_manifest())
    args = parser.parse_args()

    base_manifest, canonical_mask = load_base_validity_mask(args.grid_manifest)
    grid = grid_from_manifest(base_manifest["grid"])
    if canonical_mask.shape != (grid.height, grid.width):
        raise RuntimeError("Canonical validity mask shape does not match canonical grid")
    print(
        f"Canonical Goldilocks grid {grid.width}x{grid.height}; "
        f"{np.count_nonzero(canonical_mask):,} valid land cells from {args.grid_manifest}"
    )

    metrics: list[MetricResult] = []
    source_files: dict[str, list[dict]] = {}
    for variable in VARIABLES:
        metric, info = derive_metric(variable, args.years, grid, canonical_mask)
        metrics.append(metric)
        source_files[variable] = [
            {
                "year": item["year"],
                "file": item["file"],
                "url": f"https://uk-air.defra.gov.uk/datastore/pcm/{item['file']}",
            }
            for item in info
        ]

    years_label = f"{args.years[0]}–{args.years[-1]}"
    sources = {
        "defra-pcm": {
            "provider": "Department for Environment, Food and Rural Affairs (Defra)",
            "dataset": "UK-AIR Pollution Climate Mapping (PCM) background pollution data",
            "resolution": "1 km annual grids",
            "homepage_url": PCM_PAGE_URL,
            "licence_name": OGL_NAME,
            "licence_url": OGL_URL,
            "attribution": (
                "© Crown 2026 copyright Defra via uk-air.defra.gov.uk, licenced under the Open Government Licence (OGL)."
            ),
            "derived_product_notice": (
                f"Goldilocks Map Pollution metrics are {years_label} arithmetic means derived from annual Defra PCM grids; "
                "they are not official Defra products."
            ),
            "note": (
                "PCM values are modelled background concentrations/metrics rather than measurements at each grid-cell centre. "
                "Defra updates PCM modelling methods over time, so annual grids should not be treated as a perfectly homogeneous observational time series."
            ),
            "releases": [
                {"label": f"PCM {year} annual grids", "status": "published", "url": PCM_PAGE_URL}
                for year in args.years
            ],
            "source_files": source_files,
        }
    }
    base_grid = base_manifest.get("grid", {})
    write_dataset_manifest(
        dataset_id="pollution",
        categories=[CATEGORY],
        metrics=metrics,
        grid=grid,
        output_dir=args.output_dir.resolve(),
        sources=sources,
        validity_mask=canonical_mask,
        grid_valid_cell_count=int(np.count_nonzero(canonical_mask)),
        source_valid_cell_count=int(base_grid.get("source_valid_cell_count", np.count_nonzero(canonical_mask))),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
