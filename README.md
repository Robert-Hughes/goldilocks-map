# Goldilocks Map

Goldilocks Map is an experimental single-file UK location-suitability explorer. This proof of concept currently renders one derived Met Office HadUK-Grid climate metric over a 160 km x 160 km area around York.

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
4. select the nearest 160 x 160 HadUK 1 km cells;
5. derive `count(tasmax > 25°C)` by reading one daily spatial raster at a time rather than materialising the whole 31-day cube;
6. encode the result as a regular row-major raster with one-byte-compatible values (`0..31`, with `255` reserved for no-data);
7. repeatedly average nodata-aware 2 x 2 regions, rounding each mean to the nearest integer, to build a full LOD pyramid down to 1 x 1;
8. install the minimal npm frontend dependencies if needed (`esbuild`, TypeScript and `proj4`);
9. bundle/minify `src/app.ts` with esbuild;
10. inject the derived raster pyramid JSON and bundled JavaScript into the HTML template;
11. write the single-file application to `dist/goldilocks.html`.

Use `python build.py --refresh-data` to force a fresh NetCDF download.

## Browser architecture

The climate layer is a custom Leaflet `GridLayer` whose tiles are 256 x 256 canvas elements generated locally from the embedded raster pyramid. LOD0 is the original 1 km grid; each subsequent level doubles the nominal cell size and stores rounded nodata-aware averages of the preceding 2 x 2 regions. In the browser the JSON arrays are immediately converted to `Uint8Array`s.

For each Leaflet tile zoom, the renderer chooses the finest climate LOD whose nominal cells are at least about four screen pixels across. The pixel-size test is evaluated at runtime at a fixed representative UK reference point (54.5°N, 2°W), so the choice depends only on zoom rather than viewport latitude. It uses Leaflet's floating-point projected pixel coordinates rather than rounded container coordinates, avoiding sub-pixel values collapsing to zero. Each tile intersects its geographic bounds with the raster's WGS84 envelope before transforming that nearby area into British National Grid, then draws only the relevant rows and columns of the selected LOD.

Leaflet now manages tile buffering, panning, clipping, recycling, and zoom transforms. `updateWhenIdle: false` lets newly exposed climate tiles be created during a drag, while `keepBuffer: 2` retains neighbouring tile rings. `updateWhenZooming: false` keeps the current tile set/LOD scaled during pinch or animated zoom and requests the new zoom's tiles only when the gesture settles.

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
