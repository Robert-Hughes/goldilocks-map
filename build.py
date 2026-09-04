#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import h5py
import numpy as np
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
THRESHOLD_C = 25.0
NO_DATA_VALUE = 255
BNG_PROJ4 = (
    "+proj=tmerc +lat_0=49 +lon_0=-2 +k=0.9996012717 +x_0=400000 +y_0=-100000 "
    "+ellps=airy +towgs84=446.448,-125.157,542.06,0.1502,0.247,0.8421,-20.4894 "
    "+units=m +no_defs"
)


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


def h5_text(value: object) -> str:
    """Decode a scalar HDF5/NetCDF text attribute to normal Python text."""
    if isinstance(value, (bytes, np.bytes_)):
        return bytes(value).decode("utf-8")
    array = np.asarray(value)
    if array.shape == ():
        item = array.item()
        if isinstance(item, (bytes, np.bytes_)):
            return bytes(item).decode("utf-8")
        return str(item)
    return str(value)


def coord_step(values: np.ndarray) -> float:
    diffs = np.diff(np.asarray(values, dtype=float))
    nonzero = np.abs(diffs[np.abs(diffs) > 0])
    if not nonzero.size:
        raise RuntimeError("Unable to determine grid spacing")
    step = float(np.median(nonzero))
    if not np.allclose(nonzero, step):
        raise RuntimeError("Expected regular spatial coordinates")
    return step


def build_lod_levels(values: np.ndarray, cell_size_m: int, nodata: int) -> list[dict]:
    """Build a nodata-aware 2x2 average pyramid from an encoded uint8 raster."""
    current = np.asarray(values, dtype=np.uint8)
    if current.ndim != 2:
        raise RuntimeError(f"LOD source must be two-dimensional, got shape {current.shape}")

    levels: list[dict] = []
    level_number = 0
    current_cell_size = int(cell_size_m)

    while True:
        height, width = current.shape
        levels.append({
            "level": level_number,
            "width": int(width),
            "height": int(height),
            "cell_size_m": current_cell_size,
            "values": current.ravel(order="C").astype(int).tolist(),
        })
        if width == 1 and height == 1:
            break

        parent_height = (height + 1) // 2
        parent_width = (width + 1) // 2

        # Pad odd edges with nodata, then reduce every 2x2 block in one vectorised
        # operation. Each coarser level is derived from the already-rounded level
        # immediately below it, matching the original recursive semantics.
        padded = np.full((parent_height * 2, parent_width * 2), nodata, dtype=np.uint8)
        padded[:height, :width] = current
        valid = padded != nodata
        blocks_valid = valid.reshape(parent_height, 2, parent_width, 2)
        counts = blocks_valid.sum(axis=(1, 3), dtype=np.uint8)
        sums = np.where(valid, padded, 0).reshape(parent_height, 2, parent_width, 2).sum(
            axis=(1, 3), dtype=np.uint16
        )

        parent = np.full((parent_height, parent_width), nodata, dtype=np.uint8)
        has_children = counts > 0
        # Integer form of floor(mean + 0.5), i.e. half-up rounding for our
        # non-negative day counts, avoiding floating point entirely.
        numerator = 2 * sums[has_children] + counts[has_children]
        denominator = 2 * counts[has_children]
        parent[has_children] = (numerator // denominator).astype(np.uint8)

        current = parent
        current_cell_size *= 2
        level_number += 1

    return levels


def make_metric_data(path: Path) -> dict:
    bng_to_wgs84 = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)

    with h5py.File(path, "r") as h5:
        required = ("tasmax", "time", "projection_x_coordinate", "projection_y_coordinate")
        missing = [name for name in required if name not in h5]
        if missing:
            raise RuntimeError(f"Expected HadUK-Grid datasets missing from NetCDF/HDF5 file: {missing}")

        source = h5["tasmax"]
        x_name = "projection_x_coordinate"
        y_name = "projection_y_coordinate"
        x_vals = np.asarray(h5[x_name][...], dtype=float)
        y_vals = np.asarray(h5[y_name][...], dtype=float)
        time_count = int(h5["time"].shape[0])
        width = int(x_vals.size)
        height = int(y_vals.size)
        expected_shape = (time_count, height, width)
        if source.shape != expected_shape:
            raise RuntimeError(f"Expected tasmax shape {expected_shape}, found {source.shape}")
        if time_count != 31:
            raise RuntimeError(f"Expected 31 daily observations for July 2026, found {time_count}")

        units = h5_text(source.attrs.get("units", "")).strip()
        units_key = units.lower()
        if units_key in {"k", "kelvin"}:
            kelvin_source = True
        elif units_key in {"degc", "degree_celsius", "degrees_celsius", "c", "°c", "celsius"}:
            kelvin_source = False
        else:
            raise RuntimeError(f"Unexpected tasmax units {units!r}; refusing to assume Celsius")

        x_step = coord_step(x_vals)
        y_step = coord_step(y_vals)
        if x_step != 1000.0 or y_step != 1000.0:
            raise RuntimeError(f"Expected a 1 km grid, found spacing {x_step:g}m x {y_step:g}m")
        if x_vals[1] < x_vals[0] or y_vals[1] < y_vals[0]:
            raise RuntimeError("Raster encoding expects west-to-east and south-to-north source coordinates")

        # The source is gzip-compressed in multi-day HDF5 chunks. Reading one day
        # at a time repeatedly decompresses the same chunk. Read one native time
        # chunk at a time directly into a reusable float32 buffer instead: this
        # preserves streaming memory use while ensuring each compressed chunk is
        # normally decompressed only once.
        metric_values = np.zeros((height, width), dtype=np.uint8)
        valid_days = np.zeros((height, width), dtype=np.uint8)
        io_started = time.perf_counter()
        time_block = int(source.chunks[0]) if source.chunks else time_count
        time_block = max(1, min(time_block, time_count))
        buffer = np.empty((time_block, height, width), dtype=np.float32)

        fill_values = []
        for attr_name in ("_FillValue", "missing_value"):
            if attr_name in source.attrs:
                fill_values.extend(np.asarray(source.attrs[attr_name]).reshape(-1).tolist())
        fill_values_f32 = [np.float32(value) for value in fill_values]
        scale_factor = np.float32(np.asarray(source.attrs.get("scale_factor", 1.0)).item())
        add_offset = np.float32(np.asarray(source.attrs.get("add_offset", 0.0)).item())

        for start in range(0, time_count, time_block):
            stop = min(time_count, start + time_block)
            values = buffer[: stop - start]
            source.read_direct(values, source_sel=np.s_[start:stop, :, :])
            valid = np.isfinite(values)
            for fill_value in fill_values_f32:
                valid &= values != fill_value
            if scale_factor != 1.0 or add_offset != 0.0:
                values *= scale_factor
                values += add_offset
            if kelvin_source:
                values -= np.float32(273.15)
            metric_values += np.sum(valid & (values > THRESHOLD_C), axis=0, dtype=np.uint8)
            valid_days += np.sum(valid, axis=0, dtype=np.uint8)
        io_elapsed = time.perf_counter() - io_started

        has_data = valid_days > 0
        if not np.any(has_data):
            raise RuntimeError("Full HadUK-Grid domain has no valid daily observations")
        encoded_values = metric_values.copy()
        encoded_values[~has_data] = NO_DATA_VALUE
        lod_started = time.perf_counter()
        lod_levels = build_lod_levels(encoded_values, int(x_step), NO_DATA_VALUE)
        lod_elapsed = time.perf_counter() - lod_started

        west = float(x_vals[0] - x_step / 2)
        east = float(x_vals[-1] + x_step / 2)
        south = float(y_vals[0] - y_step / 2)
        north = float(y_vals[-1] + y_step / 2)
        corner_bng = [(west, south), (east, south), (east, north), (west, north)]
        bounds_wgs84 = []
        for easting, northing in corner_bng:
            lon, lat = bng_to_wgs84.transform(easting, northing)
            bounds_wgs84.append([round(lat, 7), round(lon, 7)])

        projection = None
        grid_mapping_name = h5_text(source.attrs.get("grid_mapping", "")).strip()
        if grid_mapping_name and grid_mapping_name in h5:
            projection = {k: h5_text(v) for k, v in h5[grid_mapping_name].attrs.items()}

        metric_min = int(metric_values[has_data].min())
        metric_max = int(metric_values[has_data].max())
        valid_cell_count = int(np.count_nonzero(has_data))
        print(f"tasmax shape: {source.shape}; units: {units}; HDF5 chunks: {source.chunks}")
        print(f"Selected coordinate names: x={x_name}, y={y_name}; spacing={x_step:g}m x {y_step:g}m")
        print(f"Full source raster: {height} x {width} = {width * height} positions ({valid_cell_count} valid)")
        print(f"Metric range: {metric_min} to {metric_max} days")
        print(f"Source read + metric accumulation: {io_elapsed:.3f}s using {time_block}-day HDF5 blocks")
        lod_shapes = " -> ".join(f"{level['width']}x{level['height']}" for level in lod_levels)
        lod_value_count = sum(level["width"] * level["height"] for level in lod_levels)
        base_value_count = width * height
        lod_overhead = (lod_value_count - base_value_count) / base_value_count * 100.0
        print(f"LOD pyramid: {lod_shapes}")
        print(f"LOD pyramid build: {lod_elapsed:.3f}s")
        print(f"LOD value count: {lod_value_count:,} ({lod_overhead:.1f}% over base raster)")

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
                "proj4": BNG_PROJ4,
                "cell_size_m": int(x_step),
                "width": width,
                "height": height,
                "cell_count": width * height,
                "valid_cell_count": valid_cell_count,
                "west": int(round(west)),
                "south": int(round(south)),
                "east": int(round(east)),
                "north": int(round(north)),
                "row_order": "south_to_north",
                "column_order": "west_to_east",
                "bounds_wgs84": bounds_wgs84,
                "projection_metadata": projection,
            },
            "raster": {
                "encoding": "row-major uint8-compatible integers",
                "nodata": NO_DATA_VALUE,
                "levels": lod_levels,
                # Valid-day counts remain base-resolution metadata for exact click popups.
                "valid_days": valid_days.ravel(order="C").astype(int).tolist(),
            },
            "source": {
                "provider": "Met Office HadUK-Grid",
                "variable": "tasmax",
                "source_units": units,
                "file": path.name,
                "url": SOURCE_URL,
                "status": "provisional July 2026 data",
                "resolution": "1 km",
                "daily_observation_count": time_count,
                "note": "HadUK-Grid is a gridded/interpolated climate-observation dataset; a grid cell is not a physical thermometer measurement at that exact point.",
            },
            "summary": {"min": metric_min, "max": metric_max},
        }


def ensure_frontend_dependencies() -> Path:
    executable = ROOT / "node_modules" / ".bin" / "esbuild"
    proj4_package = ROOT / "node_modules" / "proj4" / "package.json"
    if executable.exists() and proj4_package.exists():
        return executable
    print("Installing frontend build dependencies with npm...")
    subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=ROOT, check=True)
    if not executable.exists() or not proj4_package.exists():
        raise RuntimeError("npm install completed but required frontend dependencies are still missing")
    return executable


def bundle_typescript() -> str:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    esbuild = ensure_frontend_dependencies()
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
