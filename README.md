# Goldilocks Map

Goldilocks Map is an experimental single-file UK location-suitability explorer. This proof of concept currently renders one derived Met Office HadUK-Grid climate metric over an 80 km x 80 km area around York.

## Current metric

- **Metric:** number of days with daily maximum temperature (`tasmax`) strictly greater than 25°C
- **Period:** July 2026
- **Source:** Met Office HadUK-Grid provisional daily 1 km data
- **Input:** `tasmax_hadukgrid_uk_1km_day_20260701-20260731.nc`
- **Output:** `dist/goldilocks.html`

York is only the current extraction centre and initial viewport. The stored metric is an absolute climate quantity, not a York-relative score.

HadUK-Grid is a gridded/interpolated climate-observation dataset. A 1 km grid-cell value should not be interpreted as a thermometer measurement physically made at that exact location.

## Build

Prerequisites: Python 3.11+, Node.js/npm, and internet access for the initial data/dependency download.

On GhostBSD/FreeBSD, using the packaged scientific/geospatial stack avoids lengthy local compilation:

```sh
sudo pkg install py312-xarray py312-h5netcdf py312-pyproj
python3 -m venv --system-site-packages .venv
. .venv/bin/activate
python -m pip install -e . --no-deps
python build.py
```

On platforms where binary Python wheels are available, a normal virtualenv and `python -m pip install -e .` is sufficient.

`python build.py` will:

1. download/cache the July 2026 HadUK-Grid NetCDF file under `data/` if needed;
2. verify that it contains `tasmax`, expected spatial coordinates, 31 daily observations, and recognised temperature units;
3. transform the York centre from WGS84 to British National Grid (EPSG:27700);
4. select the nearest 80 x 80 HadUK 1 km cells;
5. derive `count(tasmax > 25°C)` by reading one daily spatial raster at a time rather than materialising the whole 31-day cube;
6. encode the result as a regular row-major raster with one-byte-compatible values (`0..31`, with `255` reserved for no-data);
7. repeatedly average nodata-aware 2 x 2 regions, rounding each mean to the nearest integer, to build a full LOD pyramid down to 1 x 1;
8. install the minimal npm frontend dependencies if needed (`esbuild`, TypeScript and `proj4`);
9. bundle/minify `src/app.ts` with esbuild;
10. inject the derived raster pyramid JSON and bundled JavaScript into the HTML template;
11. write the single-file application to `dist/goldilocks.html`.

Use `python build.py --refresh-data` to force a fresh NetCDF download.

## Browser architecture

The climate layer is a custom Leaflet canvas layer rather than one Leaflet polygon per HadUK cell. The embedded data contains a pyramid of row-major raster levels sharing the same BNG extent. LOD0 is the original 1 km grid; each subsequent level doubles the nominal cell size and stores rounded nodata-aware averages of the preceding 2 x 2 regions. In the browser the JSON arrays are immediately converted to `Uint8Array`s.

After a pan/zoom settles, the renderer chooses the finest LOD whose nominal cells are at least about four screen pixels across. The pixel-size test is evaluated at runtime at a fixed representative UK reference point (54.5°N, 2°W), so the choice depends only on zoom rather than the current viewport latitude. It uses Leaflet's floating-point projected pixel coordinates rather than rounded container coordinates; otherwise sub-pixel 1 km cells can collapse to a measured size of zero and incorrectly jump straight to the coarsest LOD. For visibility clipping, the viewport is first intersected with the raster's WGS84 envelope and only that nearby rectangle is transformed into British National Grid; this avoids low-zoom false negatives caused by projecting remote world-scale viewport corners into BNG. The resulting row/column range gets a one-cell safety margin and only that visible part of the selected LOD is iterated. Odd-sized outer cells are clipped to the original raster extent.

During pinch or animated zooms, the selected LOD is deliberately kept fixed. The already-rendered canvas is continuously translated/scaled using the same Leaflet zoom transform as the basemap, so it stays visually registered without rerunning the raster loop every gesture frame. When the zoom finishes, the renderer chooses the appropriate LOD for the final zoom and redraws once at the final resolution and visible-cell range.

Clicking always resolves against LOD0, so popups retain the exact 1 km metric rather than a coarse averaged value. The information panel remembers its collapsed/expanded state in `localStorage`; the responsive mobile/desktop default is only used until the user makes a choice.

## Basemap selection

The generated page needs internet access for Leaflet and map tiles.

- When served over `http:` or `https:`, it uses the standard OpenStreetMap tile server.
- When opened directly as a `file:` URL, it uses CARTO's OpenStreetMap-backed tiles because the standard OSM tile service rejects referrer-less `file://` requests.

Attribution is changed accordingly.

## Project layout

- `build.py` — download, validation, streaming preprocessing, raster encoding, TypeScript bundling and HTML generation
- `templates/goldilocks.html` — single-page HTML shell
- `src/app.ts` — Leaflet/custom-canvas frontend
- `data/` — cached raw input data (git-ignored)
- `dist/goldilocks.html` — generated artifact (git-ignored)

## Data provenance

The source data are © Crown copyright, Met Office HadUK-Grid. This prototype uses provisional July 2026 daily maximum-temperature data at 1 km resolution. The derived metric is the count of daily `tasmax` values strictly above 25°C during 1–31 July 2026.

HadUK-Grid reference: Hollis, D., McCarthy, M. P., Kendon, M., Legg, T. & Simpson, I. (2019), “HadUK-Grid — A new UK dataset of gridded climate observations”, *Geoscience Data Journal*, 6, 151–159, https://doi.org/10.1002/gdj3.78.
