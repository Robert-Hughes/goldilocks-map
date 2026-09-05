# Goldilocks Map

Goldilocks Map is an experimental single-file UK location-suitability explorer built from Met Office HadUK-Grid daily 1 km observations. The climate layer supports several derived heat metrics over the full HadUK-Grid domain and lets the user switch between them without reloading the page.

## Climate metrics

The processed bundle contains:

- expected annual days with `tasmax > 25°C`;
- expected annual days with `tasmax > 28°C`;
- expected annual days with `tasmax > 30°C`;
- 95th percentile of June–August daily `tasmax`;
- 99th percentile of June–August daily `tasmax`;
- longest observed consecutive run with `tasmax > 25°C`;
- expected annual tropical-night count, defined as daily `tasmin > 20°C`.

Stable historical observations come from CEDA HadUK-Grid v1.3.2.ceda for 2016–2025. Published provisional 2026 months are also included. For annual-count metrics, each calendar month is averaged across the years available for that month and the twelve monthly means are summed. This allows published 2026 months to contribute without treating unpublished months as zero.

HadUK-Grid is a gridded/interpolated climate-observation dataset. A 1 km grid-cell value should not be interpreted as a thermometer measurement physically made at that exact location.

The longest-run metric is intentionally a hard-threshold statistic and is therefore not spatially smooth even when the underlying `tasmax` field is smooth. A single day at 25.01°C versus 24.99°C can preserve or break a multi-week run and create a sharp boundary between neighbouring 1 km cells. An independent year-by-year audit of the current bundle matched the stored streak raster exactly; every cell whose longest run is at least 25 days is driven by the provisional 2026 summer.

## Data quality pruning

The offline metric processor deliberately masks eight 1 km HadUK-Grid cells covering the St Kilda archipelago before deriving any metric or building any LOD level. The exclusion is expressed as exact British National Grid cell centres, not as a geographic bounding box, so neighbouring Hebridean cells are not affected.

A full Jan 2016–Aug 2026 cross-variable audit compared each daily `tasmin` value with the `tasmax` value covering the same 24-hour observation period. St Kilda was the only severe tropical-night failure cluster: all UK cells with Tmin/Tmax ordering errors greater than 10°C were these eight cells, and every tropical-night candidate with an ordering error greater than 5°C occurred there. Across the eight St Kilda cells, 152 of 160 `tasmin > 20°C` candidate events had a Tmin-above-Tmax inconsistency, demonstrating that the apparent tropical-night maximum was an interpolation artefact rather than a credible climate signal.

The excluded BNG cell centres are `(9500,898500)`, `(8500,899500)`, `(9500,899500)`, `(10500,899500)`, `(8500,900500)`, `(9500,900500)`, `(6500,901500)`, and `(15500,905500)`. This removes 8 of 245,077 source-valid cells (about 0.0033%). The smaller inconsistencies observed around the Isles of Scilly are retained: they do not produce comparable >5°C tropical-night ordering errors and do not materially distort the metric.

The derived manifest records the pruning rule, audit rationale and exact cells so generated products remain traceable.

## Build

Prerequisites: Python 3.11+, Node.js/npm, and internet access for source/dependency downloads.

On GhostBSD/FreeBSD, using the packaged scientific/geospatial stack avoids lengthy local compilation:

```sh
sudo pkg install py312-numpy py312-h5py py312-pyproj
python3 -m venv --system-site-packages .venv
. .venv/bin/activate
python -m pip install -e . --no-deps
npm install
```

On platforms where binary Python wheels are available, a normal virtualenv and `python -m pip install -e .` is sufficient.

After the raw source download is complete, generate the derived metric bundle and HTML:

```sh
python process_climate_metrics.py
python build.py
```

`process_climate_metrics.py` streams the monthly NetCDF/HDF5 files, derives all metrics, quantises them to `uint16`, builds the complete nodata-aware LOD pyramid for each metric, and writes independently gzip-compressed metric blobs plus `data/derived/climate-metrics/manifest.json`. Summer percentile input is staged in a temporary memory-mapped file so the full multi-year daily cube is never held in RAM.

`build.py` validates the derived blobs, bundles/minifies the TypeScript frontend (including `proj4` and `fflate`), base64-embeds the compressed blobs and metadata, and writes the self-contained application to `dist/goldilocks.html`.

For development while the source archive is still downloading, a partial preview can be generated explicitly, for example:

```sh
python process_climate_metrics.py --min-year 2016 --max-year 2016 --no-provisional --allow-partial --output-dir data/derived/climate-metrics-preview
python build.py --metrics-manifest data/derived/climate-metrics-preview/manifest.json
```

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

The climate layer is a custom Leaflet `GridLayer` whose tiles are 256 x 256 canvas elements generated locally from the selected metric's embedded raster pyramid. Every metric is transported as a gzip-compressed little-endian `uint16` blob; only the initially selected metric is decompressed at startup, and other metrics are decoded lazily the first time their radio button is selected. Decoded metrics remain cached as typed-array views over one contiguous buffer.

At page startup, the browser marks every base-grid intersection used by a valid cell at any LOD and projects each required point exactly once from British National Grid into zoom-0 Web-Mercator world pixels. All metrics are required to share the same LOD dimensions and nodata geometry, so this projection lookup is reused when the selected climate measure changes. Tile rendering then converts a corner to local canvas coordinates with only `cached_world_pixel * 2^zoom - tile_origin`.

Canvas geometry is batched per tile. Metric values are mapped to a 64-step visual palette and cell polygons sharing a palette bin are accumulated into one `Path2D`, placing a fixed upper bound on fill calls even for percentile metrics with hundreds of distinct encoded values. When gridlines are enabled, all valid-cell outlines are accumulated into one additional `Path2D` and drawn with a single `stroke()` call; when disabled, that grid path is not built at all.

For each Leaflet tile zoom, the renderer chooses the finest climate LOD whose nominal cells are at least about four screen pixels across. The reference pixel distance is projected only once at a fixed representative UK location (54.5°N, 2°W), so LOD choice depends only on zoom and thereafter requires only power-of-two scaling. Each tile still performs a small fixed set of inverse WGS84 -> BNG transforms to identify its candidate row/column range. Leaflet manages tile buffering, panning, clipping, recycling, and zoom transforms. Leaflet's default 200 ms tile fade animation is disabled because climate canvases render synchronously; replacement tiles therefore appear immediately instead of fading through the basemap during redraws and zoom changes.

Clicking always resolves against active-metric LOD0, so popups retain the exact quantised 1 km value rather than a coarse averaged value. The selected-cell outline is a separate lightweight Leaflet vector overlay, so changing the selection does not regenerate any climate canvas tiles. The collapsible left-side panel contains the metric radio list, gridline control, legend, description and provenance. Panel state, gridline visibility and selected metric are stored independently in `localStorage`. Gridlines default to hidden when no preference has yet been saved.

## Basemap selection

The generated page needs internet access for Leaflet and map tiles.

- When served over `http:` or `https:`, it uses the standard OpenStreetMap tile server.
- When opened directly as a `file:` URL, it uses CARTO's OpenStreetMap-backed tiles because the standard OSM tile service rejects referrer-less `file://` requests.

Attribution is changed accordingly.

## Project layout

- `download_climate_sources.py` — authenticated/resumable CEDA download plus provisional 2026 discovery
- `process_climate_metrics.py` — streaming metric derivation, percentile staging, quantisation, LOD construction and compression
- `build.py` — validates/embeds the processed metric bundle and bundles the frontend
- `templates/goldilocks.html` — single-page HTML shell
- `src/app.ts` — Leaflet/custom-canvas multi-metric frontend
- `data/source/` — cached raw NetCDF source data (git-ignored)
- `data/derived/` — generated compressed metric bundles (git-ignored)
- `dist/goldilocks.html` — generated artifact (git-ignored)

## Data provenance

The source data are © Crown copyright, Met Office HadUK-Grid. Stable 2016–2025 observations are taken from CEDA HadUK-Grid v1.3.2.ceda; available 2026 months are provisional Met Office HadUK-Grid data and may be revised before a later annual release.

HadUK-Grid reference: Hollis, D., McCarthy, M. P., Kendon, M., Legg, T. & Simpson, I. (2019), “HadUK-Grid — A new UK dataset of gridded climate observations”, *Geoscience Data Journal*, 6, 151–159, https://doi.org/10.1002/gdj3.78.
