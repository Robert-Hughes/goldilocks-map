#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import re
import zipfile
from pathlib import Path

import numpy as np

from goldilocks_raster import MetricResult, grid_from_manifest, load_base_validity_mask, write_dataset_manifest

ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = ROOT / "data" / "source" / "os-terrain-50"
DEFAULT_DISCOVERY = SOURCE_ROOT / "discovery.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "derived" / "terrain-metrics"
CATEGORY = {"id": "terrain", "label": "Terrain", "order": 3}
SOURCE_ID = "os-terrain-50"
OS_TERRAIN_PRODUCT_URL = "https://www.ordnancesurvey.co.uk/products/os-terrain-50"
OS_TERRAIN_DOCS_URL = "https://docs.os.uk/os-downloads/products/land-and-terrain-portfolio/os-terrain-50"
OGL_NAME = "Open Government Licence v3.0"
OGL_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
NESTED_TILE_RE = re.compile(r"(?:^|/)([a-z]{2}\d{2})_OST50GRID_\d{8}\.zip$", re.IGNORECASE)
HEADER_KEYS = {"ncols", "nrows", "xllcorner", "yllcorner", "cellsize", "nodata_value"}


def default_grid_manifest() -> Path:
    return ROOT / "data" / "derived" / "climate-metrics" / "manifest.json"


def load_discovery(path: Path) -> tuple[dict, Path]:
    discovery = json.loads(path.read_text(encoding="utf-8"))
    if discovery.get("dataset") != "OS Terrain 50" or discovery.get("format") != "ASCII Grid and GML (Grid)":
        raise RuntimeError(f"Unexpected Terrain 50 discovery metadata in {path}")
    relative = discovery.get("file")
    if not isinstance(relative, str):
        raise RuntimeError(f"Terrain 50 discovery metadata has no source archive path: {path}")
    source = SOURCE_ROOT / relative
    if not source.is_file():
        raise RuntimeError(f"Terrain 50 source archive is missing: {source}")
    return discovery, source


def parse_ascii_grid(data: bytes, *, label: str) -> tuple[dict[str, float], np.ndarray]:
    text = data.decode("ascii", "strict")
    lines = text.splitlines()
    header: dict[str, float] = {}
    first_data_line = 0
    for index, line in enumerate(lines):
        parts = line.split()
        if len(parts) == 2 and parts[0].lower() in HEADER_KEYS:
            header[parts[0].lower()] = float(parts[1])
            first_data_line = index + 1
            continue
        first_data_line = index
        break
    required = ("ncols", "nrows", "xllcorner", "yllcorner", "cellsize")
    missing = [key for key in required if key not in header]
    if missing:
        raise RuntimeError(f"{label}: ASCII grid header is missing {missing}")
    ncols = int(header["ncols"])
    nrows = int(header["nrows"])
    if ncols != 200 or nrows != 200 or header["cellsize"] != 50.0:
        raise RuntimeError(
            f"{label}: expected a 200x200 50 m Terrain 50 tile, got {ncols}x{nrows} at {header['cellsize']:g} m"
        )
    values = np.fromstring("\n".join(lines[first_data_line:]), sep=" ", dtype=np.float32)
    if values.size != nrows * ncols:
        raise RuntimeError(f"{label}: read {values.size} elevation values; expected {nrows * ncols}")
    values = values.reshape((nrows, ncols))
    nodata = header.get("nodata_value")
    if nodata is not None and np.any(values == np.float32(nodata)):
        values = values.copy()
        values[values == np.float32(nodata)] = np.nan
    return header, values


def tile_relief(values_north_to_south: np.ndarray, *, label: str) -> np.ndarray:
    if values_north_to_south.shape != (200, 200):
        raise RuntimeError(f"{label}: unexpected source tile shape {values_north_to_south.shape}")
    # OS ASCII rows run north-to-south. Flip to the Goldilocks convention so
    # result row 0 is the southernmost 1 km block. Each 1 km canonical cell
    # contains exactly 20x20 Terrain 50 pixel-centre heights.
    south_to_north = np.flipud(values_north_to_south)
    blocks = south_to_north.reshape(10, 20, 10, 20).transpose(0, 2, 1, 3)
    valid_count = np.sum(np.isfinite(blocks), axis=(2, 3))
    with np.errstate(all="ignore"):
        minimum = np.nanmin(blocks, axis=(2, 3))
        maximum = np.nanmax(blocks, axis=(2, 3))
    relief = (maximum - minimum).astype(np.float32)
    relief[valid_count == 0] = np.nan
    return relief


def derive_relief(source_zip: Path, grid, canonical_mask: np.ndarray) -> tuple[np.ndarray, dict]:
    relief = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
    source_coverage = np.zeros((grid.height, grid.width), dtype=bool)
    tile_count = 0
    tile_dates: set[str] = set()
    source_pixel_count = 0
    source_nodata_count = 0

    with zipfile.ZipFile(source_zip) as outer:
        nested = [name for name in outer.namelist() if NESTED_TILE_RE.search(name)]
        if not nested:
            raise RuntimeError(f"Terrain 50 source archive contains no nested grid tiles: {source_zip}")
        for index, nested_name in enumerate(nested, start=1):
            match = NESTED_TILE_RE.search(nested_name)
            assert match is not None
            tile_name = match.group(1).upper()
            date_match = re.search(r"_OST50GRID_(\d{8})\.zip$", nested_name, re.IGNORECASE)
            if date_match:
                tile_dates.add(date_match.group(1))
            with zipfile.ZipFile(io.BytesIO(outer.read(nested_name))) as inner:
                asc_names = [name for name in inner.namelist() if name.lower().endswith(".asc")]
                if len(asc_names) != 1:
                    raise RuntimeError(f"{nested_name}: expected exactly one .asc file; found {len(asc_names)}")
                asc_name = asc_names[0]
                header, source_values = parse_ascii_grid(inner.read(asc_name), label=f"{nested_name}/{asc_name}")

            xll = header["xllcorner"]
            yll = header["yllcorner"]
            if xll % 1000 != 0 or yll % 1000 != 0:
                raise RuntimeError(f"{tile_name}: 10 km tile origin does not align to canonical 1 km boundaries")
            base_col_f = (xll - grid.west) / grid.cell_size_m
            base_row_f = (yll - grid.south) / grid.cell_size_m
            if base_col_f != round(base_col_f) or base_row_f != round(base_row_f):
                raise RuntimeError(f"{tile_name}: source tile does not align exactly to the canonical Goldilocks grid")
            base_col = int(round(base_col_f))
            base_row = int(round(base_row_f))
            if base_col < 0 or base_row < 0 or base_col + 10 > grid.width or base_row + 10 > grid.height:
                raise RuntimeError(f"{tile_name}: source tile falls outside the canonical Goldilocks grid")

            target = np.s_[base_row : base_row + 10, base_col : base_col + 10]
            if np.any(source_coverage[target]):
                raise RuntimeError(f"{tile_name}: source tile overlaps previously processed Terrain 50 coverage")
            one_km_relief = tile_relief(source_values, label=tile_name)
            relief[target] = one_km_relief
            source_coverage[target] = np.isfinite(one_km_relief)
            source_pixel_count += int(np.count_nonzero(np.isfinite(source_values)))
            source_nodata_count += int(np.count_nonzero(~np.isfinite(source_values)))
            tile_count += 1
            if index % 250 == 0 or index == len(nested):
                print(f"Terrain 50 tiles {index}/{len(nested)}", flush=True)

    valid = canonical_mask & np.isfinite(relief)
    missing_canonical = canonical_mask & ~np.isfinite(relief)
    print(
        f"Terrain 50: {tile_count:,} source tiles; {np.count_nonzero(valid):,} canonical land cells with relief; "
        f"{np.count_nonzero(missing_canonical):,} canonical land cells outside Terrain 50 coverage"
    )
    return relief, {
        "source_tiles": tile_count,
        "source_tile_dates": sorted(tile_dates),
        "source_pixel_count": source_pixel_count,
        "source_nodata_pixel_count": source_nodata_count,
        "pixels_per_complete_1km_cell": 400,
        "canonical_valid_cells": int(np.count_nonzero(valid)),
        "canonical_land_cells_without_source_coverage": int(np.count_nonzero(missing_canonical)),
        "method": (
            "For each canonical 1 km cell, relief is max elevation minus min elevation across the 20x20 OS Terrain 50 "
            "50 m pixel-centre heights inside that cell. No neighbouring 1 km cells are included."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Derive Goldilocks Terrain relief from the cached OS Terrain 50 50 m DTM grid.")
    parser.add_argument("--discovery", type=Path, default=DEFAULT_DISCOVERY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--grid-manifest", type=Path, default=default_grid_manifest())
    args = parser.parse_args()

    discovery, source_zip = load_discovery(args.discovery.resolve())
    base_manifest, canonical_mask = load_base_validity_mask(args.grid_manifest)
    grid = grid_from_manifest(base_manifest["grid"])
    if canonical_mask.shape != (grid.height, grid.width):
        raise RuntimeError("Canonical validity mask shape does not match canonical grid")
    print(
        f"Canonical Goldilocks grid {grid.width}x{grid.height}; "
        f"{np.count_nonzero(canonical_mask):,} valid land cells from {args.grid_manifest}"
    )

    relief, coverage = derive_relief(source_zip, grid, canonical_mask)
    version = str(discovery["version"])
    attribution_year = version.split("-", 1)[0]
    metric = MetricResult(
        id="terrain_relief",
        category_id="terrain",
        label="Relief",
        units="m",
        period=f"OS Terrain 50 {version}",
        definition=(
            "Vertical relief within each canonical 1 km Goldilocks cell: the maximum minus minimum OS Terrain 50 DTM "
            "elevation across the 50 m pixel-centre heights inside that cell. No neighbouring 1 km cells contribute. "
            "OS Terrain 50 is a bare-earth terrain model; in coastal source tiles OS also models tidal-water heights."
        ),
        values=relief,
        scale=1.0,
        offset=0.0,
        decimals=0,
        source_id=SOURCE_ID,
        source_variable="elevation",
        coverage=coverage,
    )
    source = {
        SOURCE_ID: {
            "provider": "Ordnance Survey",
            "dataset": "OS Terrain 50",
            "resolution": "50 m DTM grid; annual full GB supply",
            "homepage_url": OS_TERRAIN_PRODUCT_URL,
            "licence_name": OGL_NAME,
            "licence_url": OGL_URL,
            "attribution": f"Contains OS data © Crown copyright and database right {attribution_year}.",
            "derived_product_notice": (
                "Goldilocks Map Terrain relief is derived from OS Terrain 50 by calculating max-minus-min elevation "
                "within each canonical 1 km cell; it is not an Ordnance Survey product."
            ),
            "note": (
                "OS Terrain 50 is a lower-resolution digital terrain model of Great Britain with 50 m pixel-centre "
                "heights. Buildings and trees are removed from the modelled ground surface. Coastal grid tiles may "
                "contain OS-modelled tidal-water heights; Northern Ireland is outside the product coverage."
            ),
            "releases": [
                {
                    "label": f"OS Terrain 50 {version}",
                    "status": "published",
                    "url": OS_TERRAIN_PRODUCT_URL,
                }
            ],
            "source_files": {
                "grid": [
                    {
                        "release": version,
                        "file": discovery.get("file_name"),
                        "md5": discovery.get("md5"),
                        "bytes": discovery.get("bytes"),
                        "download_url": discovery.get("download_url"),
                    }
                ]
            },
            "documentation_url": OS_TERRAIN_DOCS_URL,
        }
    }
    write_dataset_manifest(
        dataset_id="terrain",
        categories=[CATEGORY],
        metrics=[metric],
        grid=grid,
        output_dir=args.output_dir.resolve(),
        sources=source,
        validity_mask=canonical_mask,
        grid_valid_cell_count=int(np.count_nonzero(canonical_mask)),
        source_valid_cell_count=int(np.count_nonzero(canonical_mask & np.isfinite(relief))),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
