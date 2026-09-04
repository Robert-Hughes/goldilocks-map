# Goldilocks Map

Goldilocks Map is an experimental single-file UK location-suitability explorer. This proof of concept currently renders one derived Met Office HadUK-Grid climate metric across the full source grid.

## Current metric

- **Metric:** number of days with daily maximum temperature (`tasmax`) strictly greater than 25°C
- **Period:** July 2026
- **Source:** Met Office HadUK-Grid provisional daily 1 km data
- **Input:** `tasmax_hadukgrid_uk_1km_day_20260701-20260731.nc`
- **Output:** `dist/goldilocks.html`

The stored metric is an absolute climate quantity over the full HadUK-Grid source domain.

HadUK-Grid is a gridded/interpolated climate-observation dataset. A 1 km grid-cell value should not be interpreted as a thermometer measurement physically made at that exact location.

## Build

Prerequisites: Python 3.11+, Node.js/npm, and internet access for the initial data/dependency download.

On GhostBSD/FreeBSD, using the packaged scientific/geospatial stack avoids lengthy local compilation:

```sh
sudo pkg install py312-numpy py312-h5py py312-pyproj
python3 -m venv --system-site-packages .venv
. .venv/bin/activate
python -m pip install -e . --no-deps
python build.py
```

On platforms where binary Python wheels are available, a normal virtualenv and `python -m pip install -e .` is sufficient.

`python build.py` will:

1. download/cache the July 2026 HadUK-Grid NetCDF file under `data/` if needed;
2. verify the expected `tasmax`, time and British National Grid coordinate datasets, 31 daily observations, grid spacing and temperature units;
3. derive `count(tasmax > 25°C)` over the complete spatial grid by reading native multi-day HDF5 chunks directly into a reusable float32 buffer rather than materialising the whole 31-day cube;
4. encode the result as a regular row-major raster with one-byte-compatible values (`0..31`, with `255` reserved for no-data);
5. build the nodata-aware LOD pyramid with vectorised 2 x 2 reductions, using rounded integer averages at each level;
6. install the minimal npm frontend dependencies if needed (`esbuild`, TypeScript and `proj4`);
7. bundle/minify `src/app.ts` with esbuild;
8. inject the derived raster pyramid JSON and bundled JavaScript into the HTML template;
9. write the single-file application to `dist/goldilocks.html`.

Use `python build.py --refresh-data` to force a fresh NetCDF download.

## Historical climate source download

`download_climate_sources.py` prepares the raw daily temperature archive needed for the planned 2016–2026 multi-metric build. It discovers rather than hardcodes CEDA's current daily release subdirectory, downloads both `tasmax` and `tasmin` for every month of 2016–2025 from HadUK-Grid v1.3.2.ceda, and also discovers the currently published 2026 provisional monthly files from the Met Office site.

CEDA downloads require a registered-user archive access token. Put it in the git-ignored repo-local `.env` file as:

```text
CEDA_ACCESS_TOKEN=your-token-here
```

The downloader reads `.env` automatically when `CEDA_ACCESS_TOKEN` is not already present in the process environment. An explicitly exported environment variable takes precedence over `.env`. Never commit the real token.

Useful commands:

```sh
# Discover the exact file set and sizes; no token required.
python download_climate_sources.py --dry-run

# Verify the token against one historical file before a long run.
python download_climate_sources.py --source ceda --check-auth

# Download/resume all historical and currently available provisional files.
python download_climate_sources.py --workers 3
```

Downloads go under `data/source/hadukgrid/` and are git-ignored. The local cache deliberately uses a shallow layout: `historical/tasmax/`, `historical/tasmin/`, `provisional-2026/tasmax/`, and `provisional-2026/tasmin/`. The NetCDF filenames already contain their variable, resolution, frequency and date range, so the deeper CEDA/Met Office archive hierarchy is not reproduced locally.

Completed CEDA files are checked against the MD5 hashes published in CEDA's JSON listing. Interrupted files retain a `.part` suffix and are resumed with HTTP Range requests on the next run. The downloader retries transient errors, validates expected byte sizes, writes a local discovery manifest, and fails quickly on an expired/rejected CEDA token. Use `--verify` when rechecking existing cached CEDA files should include a full MD5 pass.

The stable historical source is kept separate from provisional 2026 data because the latter can be revised before the next annual CEDA release. The derived-metric build can therefore record which observations came from the citable annual release and which were provisional.

## Browser architecture

The climate layer is a custom Leaflet `GridLayer` whose tiles are 256 x 256 canvas elements generated locally from the embedded raster pyramid. LOD0 is the original 1 km grid; each subsequent level doubles the nominal cell size and stores rounded nodata-aware averages of the preceding 2 x 2 regions. In the browser the JSON arrays are immediately converted to `Uint8Array`s.

At page startup, the browser marks every base-grid intersection used by a valid cell at any LOD and projects each required point exactly once from British National Grid into zoom-0 Web-Mercator world pixels. Coarser LOD edges are subsets of the same base-grid intersections, so all levels share two dense `Float64Array` lookup tables. Sea-only corners remain unprojected. Tile rendering then converts a corner to local canvas coordinates with only `cached_world_pixel * 2^zoom - tile_origin`; the thousands of BNG -> WGS84 -> Leaflet projection operations previously performed while creating each tile are eliminated.

Canvas geometry is batched per tile. Valid cell polygons are accumulated into `Path2D` objects grouped dynamically by their actual encoded metric value, with no assumption about a fixed value count or range. Each distinct value therefore requires only one canvas `fill()` call per tile, and colour strings are cached by value. When gridlines are enabled, all valid-cell outlines are accumulated into one additional `Path2D` and drawn with a single `stroke()` call; when disabled, that grid path is not built at all.

For each Leaflet tile zoom, the renderer chooses the finest climate LOD whose nominal cells are at least about four screen pixels across. The reference pixel distance is also projected only once at a fixed representative UK location (54.5°N, 2°W), so LOD choice depends only on zoom and thereafter requires only power-of-two scaling. Each tile still intersects its geographic bounds with the raster's WGS84 envelope and performs a small fixed set of inverse WGS84 -> BNG transforms to identify the candidate row/column range; only that range is iterated.
Leaflet manages tile buffering, panning, clipping, recycling, and zoom transforms. `updateWhenIdle: false` lets newly exposed climate tiles be created during a drag, while `keepBuffer: 2` retains neighbouring tile rings. `updateWhenZooming: false` keeps the current tile set/LOD scaled during pinch or animated zoom and requests the new zoom's tiles only when the gesture settles.

Clicking always resolves against LOD0, so popups retain the exact 1 km metric rather than a coarse averaged value. The heading, layer controls, legend, description and provenance are consolidated into one collapsible left-side Leaflet panel. Its collapsed state and the "Show gridlines" preference are stored independently in `localStorage`; responsive defaults are used only when no saved preference exists.

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
