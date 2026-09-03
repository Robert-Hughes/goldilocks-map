#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

import numpy as np
import xarray as xr
from pyproj import Transformer

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DIST_DIR = ROOT / "dist"
TEMPLATE_PATH = ROOT / "templates" / "goldilocks.html"
APP_TS = ROOT / "src" / "app.ts"
APP_JS = DIST_DIR / "app.js"
OUTPUT_HTML = DIST_DIR / "goldilocks.html"

SOURCE_URL = "https://www.metoffice.gov.uk/hadobs/hadukgrid/data/2026/tasmax_hadukgrid_uk_1km_day_20260701-20260731.nc"
SOURCE_PATH = DATA_DIR / Path(SOURCE_URL).name
YORK_LAT = 53.96
YORK_LON = -1.08
GRID_SIZE = 10
THRESHOLD_C = 25.0


def download_source(refresh: bool = False) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SOURCE_PATH.exists() and SOURCE_PATH.stat().st_size > 1_000_000 and not refresh:
        print(f"Using cached HadUK-Grid source: {SOURCE_PATH}")
        return SOURCE_PATH

    temp_path = SOURCE_PATH.with_suffix(SOURCE_PATH.suffix + ".part")
    if temp_path.exists():
        temp_path.unlink()
    print(f"Downloading {SOURCE_URL}")
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "GoldilocksMap/0.1"})
    with urllib.request.urlopen(request, timeout=90) as response, temp_path.open("wb") as out:
        shutil.copyfileobj(response, out)
    if temp_path.stat().st_size < 1_000_000:
        raise RuntimeError(f"Downloaded file is unexpectedly small: {temp_path.stat().st_size} bytes")
    temp_path.replace(SOURCE_PATH)
    print(f"Cached {SOURCE_PATH} ({SOURCE_PATH.stat().st_size:,} bytes)")
    return SOURCE_PATH


def find_coord_name(ds: xr.Dataset, candidates: tuple[str, ...], axis: str) -> str:
    for name in candidates:
        if name in ds.coords or name in ds.dims:
            return name
    for name, coord in ds.coords.items():
        attrs = " ".join(str(coord.attrs.get(k, "")) for k in ("standard_name", "long_name", "axis")).lower()
        if axis == "x" and ("projection_x_coordinate" in attrs or attrs.strip() == "x"):
            return name
        if axis == "y" and ("projection_y_coordinate" in attrs or attrs.strip() == "y"):
            return name
    raise RuntimeError(f"Could not identify {axis}-coordinate in dataset; coords={list(ds.coords)} dims={list(ds.dims)}")


def select_nearest(values: np.ndarray, target: float, count: int) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if values.ndim != 1 or values.size < count:
        raise RuntimeError(f"Need at least {count} one-dimensional coordinate values")
    indices = np.argsort(np.abs(values - target), kind="stable")[:count]
    return np.sort(indices)


def coord_step(values: np.ndarray) -> float:
    diffs = np.diff(np.sort(np.asarray(values, dtype=float)))
    diffs = np.abs(diffs[np.abs(diffs) > 0])
    if not diffs.size:
        raise RuntimeError("Unable to determine grid spacing")
    return float(np.median(diffs))


def open_dataset(path: Path) -> xr.Dataset:
    try:
        return xr.open_dataset(path, engine="h5netcdf")
    except Exception as first_error:
        try:
            return xr.open_dataset(path)
        except Exception as second_error:
            raise RuntimeError(
                f"Unable to open NetCDF with h5netcdf or xarray auto-detection. "
                f"h5netcdf error: {first_error}; auto error: {second_error}"
            ) from second_error


def make_metric_data(path: Path) -> dict:
    bng_to_wgs84 = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)
    wgs84_to_bng = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)
    york_e, york_n = wgs84_to_bng.transform(YORK_LON, YORK_LAT)

    with open_dataset(path) as ds:
        if "tasmax" not in ds.data_vars:
            raise RuntimeError(f"Expected variable 'tasmax' not found. Data variables: {list(ds.data_vars)}")
        tasmax = ds["tasmax"]
        x_name = find_coord_name(ds, ("projection_x_coordinate", "x", "easting"), "x")
        y_name = find_coord_name(ds, ("projection_y_coordinate", "y", "northing"), "y")
        if "time" not in tasmax.dims:
            raise RuntimeError(f"Expected a time dimension on tasmax, got {tasmax.dims}")
        if x_name not in tasmax.dims or y_name not in tasmax.dims:
            raise RuntimeError(f"tasmax dimensions {tasmax.dims} do not contain x={x_name!r} y={y_name!r}")

        x_all = np.asarray(ds[x_name].values, dtype=float)
        y_all = np.asarray(ds[y_name].values, dtype=float)
        x_idx = select_nearest(x_all, york_e, GRID_SIZE)
        y_idx = select_nearest(y_all, york_n, GRID_SIZE)
        x_vals = x_all[x_idx]
        y_vals = y_all[y_idx]
        x_step = coord_step(x_all)
        y_step = coord_step(y_all)

        subset = tasmax.isel({x_name: x_idx, y_name: y_idx}).transpose("time", y_name, x_name)
        values = np.asarray(subset.values, dtype=float)
        units = str(tasmax.attrs.get("units", "")).strip()
        if units.lower() in {"k", "kelvin"}:
            values = values - 273.15
            source_units = units
        elif units.lower() in {"degc", "degree_celsius", "degrees_celsius", "c", "°c", "celsius"}:
            source_units = units
        else:
            raise RuntimeError(f"Unexpected tasmax units {units!r}; refusing to assume Celsius")

        valid = np.isfinite(values)
        metric_values = np.sum(valid & (values > THRESHOLD_C), axis=0).astype(int)
        valid_days = np.sum(valid, axis=0).astype(int)
        if np.any(valid_days == 0):
            raise RuntimeError("At least one selected York-area cell has no valid daily observations")

        time_values = ds["time"].values
        time_count = int(np.size(time_values))
        if time_count != 31:
            raise RuntimeError(f"Expected 31 daily observations for July 2026, found {time_count}")

        cells = []
        for yi, northing in enumerate(y_vals):
            for xi, easting in enumerate(x_vals):
                west = float(easting - x_step / 2)
                east = float(easting + x_step / 2)
                south = float(northing - y_step / 2)
                north = float(northing + y_step / 2)
                corner_bng = [(west, south), (east, south), (east, north), (west, north)]
                corners = []
                for e, n in corner_bng:
                    lon, lat = bng_to_wgs84.transform(e, n)
                    corners.append([round(lat, 7), round(lon, 7)])
                center_lon, center_lat = bng_to_wgs84.transform(float(easting), float(northing))
                cells.append({
                    "id": f"BNG-{int(round(easting))}-{int(round(northing))}",
                    "value": int(metric_values[yi, xi]),
                    "valid_days": int(valid_days[yi, xi]),
                    "easting": int(round(float(easting))),
                    "northing": int(round(float(northing))),
                    "lat": round(center_lat, 7),
                    "lon": round(center_lon, 7),
                    "corners": corners,
                })

        projection = None
        grid_mapping_name = tasmax.attrs.get("grid_mapping")
        if grid_mapping_name and grid_mapping_name in ds.variables:
            projection = {k: str(v) for k, v in ds[grid_mapping_name].attrs.items()}

        metric_min = int(metric_values.min())
        metric_max = int(metric_values.max())
        print(f"tasmax dims: {tasmax.dims}; shape: {tasmax.shape}; units: {source_units}")
        print(f"Selected coordinate names: x={x_name}, y={y_name}; spacing={x_step:g}m x {y_step:g}m")
        print(f"York BNG coordinate: E={york_e:.1f}, N={york_n:.1f}")
        print(f"Extracted grid: {len(y_vals)} x {len(x_vals)} = {len(cells)} cells")
        print(f"Metric range: {metric_min} to {metric_max} days")

        return {
            "metric": {
                "id": "days_tmax_over_25",
                "label": "Days with Tmax > 25°C",
                "units": "days",
                "period": "2026-07",
                "threshold_c": THRESHOLD_C,
                "definition": "Count of July 2026 days whose HadUK-Grid daily maximum temperature is strictly greater than 25°C.",
            },
            "grid": {
                "crs": "EPSG:27700",
                "resolution_m": [x_step, y_step],
                "shape": [len(y_vals), len(x_vals)],
                "cell_count": len(cells),
                "x_centres": [int(round(v)) for v in x_vals.tolist()],
                "y_centres": [int(round(v)) for v in y_vals.tolist()],
                "selection_center_wgs84": {"lat": YORK_LAT, "lon": YORK_LON},
                "selection_center_bng": {"easting": round(york_e, 1), "northing": round(york_n, 1)},
                "projection_metadata": projection,
            },
            "source": {
                "provider": "Met Office HadUK-Grid",
                "variable": "tasmax",
                "source_units": source_units,
                "file": path.name,
                "url": SOURCE_URL,
                "status": "provisional July 2026 data",
                "resolution": "1 km",
                "daily_observation_count": time_count,
                "note": "HadUK-Grid is a gridded/interpolated climate-observation dataset; a grid cell is not a physical thermometer measurement at that exact point.",
            },
            "summary": {"min": metric_min, "max": metric_max},
            "cells": cells,
        }


def ensure_esbuild() -> Path:
    executable = ROOT / "node_modules" / ".bin" / "esbuild"
    if executable.exists():
        return executable
    print("Installing frontend build dependencies with npm...")
    subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=ROOT, check=True)
    if not executable.exists():
        raise RuntimeError("npm install completed but node_modules/.bin/esbuild was not created")
    return executable


def bundle_typescript() -> str:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    esbuild = ensure_esbuild()
    subprocess.run([
        str(esbuild), str(APP_TS), "--bundle", "--minify", "--format=iife", "--target=es2020", f"--outfile={APP_JS}"
    ], cwd=ROOT, check=True)
    return APP_JS.read_text(encoding="utf-8")


def render_html(data: dict, app_js: str) -> None:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    data_json = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("<", "\\u003c")
    if template.count("__GOLDILOCKS_DATA__") != 1 or template.count("__GOLDILOCKS_APP_JS__") != 1:
        raise RuntimeError("Template must contain each build placeholder exactly once")
    html = template.replace("__GOLDILOCKS_DATA__", data_json).replace("__GOLDILOCKS_APP_JS__", app_js)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    APP_JS.unlink(missing_ok=True)
    print(f"Wrote {OUTPUT_HTML} ({OUTPUT_HTML.stat().st_size:,} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Goldilocks Map as a single HTML file.")
    parser.add_argument("--refresh-data", action="store_true", help="redownload the HadUK-Grid source even when cached")
    args = parser.parse_args()
    source = download_source(refresh=args.refresh_data)
    data = make_metric_data(source)
    app_js = bundle_typescript()
    render_html(data, app_js)
    print("Build complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
