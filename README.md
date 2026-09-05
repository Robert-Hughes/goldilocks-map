# Goldilocks Map

Goldilocks Map is an experimental single-file UK location-suitability explorer built from multiple gridded public datasets. Metrics are grouped into first-class categories (currently **Heat**, **Cold**, **Pollution**, **Terrain** and **Woodland**) and can be switched without reloading the page.

## Metrics

The processed bundle contains:

**Heat**

- expected annual days with `tasmax > 25°C`;
- expected annual days with `tasmax > 28°C`;
- expected annual days with `tasmax > 30°C`;
- 95th percentile of June–August daily `tasmax`;
- 99th percentile of June–August daily `tasmax`;
- longest observed consecutive run with `tasmax > 25°C`;
- expected annual tropical-night count, defined as daily `tasmin > 20°C`.

**Cold**

- expected annual air-frost days, defined as daily `tasmin < 0°C`;
- 5th percentile of daily `tasmin` across complete December–February (DJF) winters.

**Pollution**

- 2022–2024 mean PM2.5 background annual concentration;
- 2022–2024 mean NO₂ background annual concentration;
- 2022–2024 mean PM10 background annual concentration;
- 2022–2024 mean annual ozone DGT120 exceedance days (days where the daily maximum running 8-hour mean exceeds 120 µg/m³).

Pollution metrics come from Defra UK-AIR Pollution Climate Mapping (PCM) 1 km annual grids. Goldilocks requires a value in all three selected years and takes their arithmetic mean. PCM values are modelled background concentrations/metrics rather than measurements at individual grid-cell centres; Defra also updates the PCM modelling methodology over time, so the annual products should not be treated as a perfectly homogeneous observational time series.

**Terrain**

- `terrain_relief`: vertical relief in metres inside each canonical 1 km tile, defined as maximum minus minimum OS Terrain 50 DTM elevation.

The current Terrain source is OS Terrain 50 release **2026-07**, covering Great Britain. Although OS describes the product as having 50 m post spacing, the supplied ASCII grid stores each height at the centre of a 50 m × 50 m raster pixel with no shared edge values between source tiles. Because those pixels align with the Goldilocks 1 km grid, each complete 1 km tile contains exactly 20 × 20 = 400 Terrain 50 height values. `terrain_relief` is calculated from those 400 values only; neighbouring 1 km tiles do not contribute. The source heights are supplied to 0.1 m, while Goldilocks quantises relief to the nearest metre.

OS Terrain 50 is a bare-earth digital terrain model intended for broad-scale terrain analysis. In coastal source tiles OS models tidal-water heights as part of the supplied surface, so a coastal Goldilocks tile can include the vertical transition between land and those modelled tidal-water heights. Northern Ireland is outside OS Terrain 50 coverage and therefore appears as nodata for Terrain.

**Woodland**

- `woodland_cover`: percentage of each canonical 1 km tile covered by current woodland in the Forestry Commission National Forest Inventory GB 2024;
- `woodland_ancient_cover`: percentage covered by recognised ancient-woodland inventory sites across England, Wales and Scotland.

Current woodland deliberately excludes NFI classes `Felled`, `Ground prep`, `Failed` and `Windblow`; the goal is current woodland land rather than the wider forestry land-use footprint. The percentage measures NFI woodland-polygon area, not fractional canopy density within each polygon. Ancient woodland uses revised Natural England inventory coverage in preference to the legacy inventory where available, all four current NRW 2021 categories in Wales, and NatureScot antiquity classes 1a/2a in Scotland. It measures recognised ancient-woodland site extent rather than current canopy, so it is not necessarily a subset of the current-woodland layer. Overlapping ancient-woodland polygons are geometrically unioned within each 1 km tile before area is measured. The NFI source generally maps woodland of at least 0.5 ha (with some Assumed woodland and Low density areas from 0.1 ha), so very small woods and individual tree features are not comprehensively represented. Both metrics use the full 100 ha canonical tile as the denominator, are quantised to 0.1 percentage points, and use a fixed 0–100% white-to-dark-green palette. Northern Ireland is nodata.

An air-frost day is assigned when the minimum air temperature falls below freezing during the observation period; it does not mean the whole day remains below freezing. A day whose maximum temperature remains below freezing is instead an ice day.

Stable historical observations come from CEDA HadUK-Grid v1.3.2.ceda for 2016–2025. Published provisional 2026 months are also included. For annual-count metrics, each calendar month is averaged across the years available for that month and the twelve monthly means are summed. This allows published 2026 months to contribute without treating unpublished months as zero. The winter Tmin percentile uses complete DJF winters only; with the current archive these are winters 2016–17 through 2025–26.

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
sudo pkg install py312-numpy py312-h5py py312-pyproj py312-fiona py312-shapely
python3 -m venv --system-site-packages .venv
. .venv/bin/activate
python -m pip install -e . --no-deps
npm install
```

On platforms where binary Python wheels are available, a normal virtualenv and `python -m pip install -e .` is sufficient.

After the raw source download is complete, generate each dataset fragment, assemble the common bundle and build the HTML:

```sh
python process_climate_metrics.py
python process_pollution_metrics.py
python process_terrain_metrics.py
python process_woodland_metrics.py
python assemble_metrics.py
python build.py
```

`goldilocks_raster.py` provides the common canonical-grid, quantisation, nodata-aware LOD and gzip-blob machinery. Each dataset processor writes a self-contained dataset manifest with categories, metrics and source provenance. `assemble_metrics.py` validates that those fragments use the same canonical 1 km BNG raster geometry, merges their categories/sources and copies the metric blobs into `data/derived/goldilocks-metrics/`.

`process_climate_metrics.py` streams the monthly NetCDF/HDF5 files; summer percentile input is staged in a temporary memory-mapped file so the full multi-year daily cube is never held in RAM. `process_pollution_metrics.py` aligns the 2022–2024 Defra PCM CSV grids to the canonical Goldilocks/HadUK 1 km grid and derives the three-year arithmetic means. `process_terrain_metrics.py` reads the nested OS Terrain 50 ASCII tiles directly from the national zip, reduces each aligned 20 × 20 block of 50 m pixel-centre heights to 1 km relief (`max - min`), and leaves canonical cells outside Great Britain as nodata. `process_woodland_metrics.py` uses Fiona/Shapely to intersect pinned NFI/AWI polygons exactly with the canonical grid, including overlap-unioning for the stitched ancient-woodland layer.

`build.py` validates the assembled blobs and source references, bundles/minifies the TypeScript frontend (including `proj4` and `fflate`), base64-embeds the compressed blobs and metadata, and writes the self-contained application to `dist/goldilocks.html`.

For development while the source archive is still downloading, a partial preview can be generated explicitly, for example:

```sh
python process_climate_metrics.py --min-year 2016 --max-year 2016 --no-provisional --allow-partial --output-dir data/derived/climate-metrics-preview
python assemble_metrics.py --dataset-manifest data/derived/climate-metrics-preview/manifest.json --output-dir data/derived/goldilocks-metrics-preview
python build.py --metrics-manifest data/derived/goldilocks-metrics-preview/manifest.json
```

## GitHub Pages deployment

The public repository is intended to be named `goldilocks-map`, giving a default project-site URL of `https://<account>.github.io/goldilocks-map/`.

The raw CEDA/Met Office NetCDF archive, Defra PCM CSV inputs, OS Terrain 50 archive and woodland vector sources are never required by GitHub Actions and remain git-ignored. After regenerating local metrics, explicitly refresh the small publishable snapshot with:

```sh
python publish_metrics_snapshot.py
```

This copies only the assembled manifest and the metric blobs referenced by it into `published-data/goldilocks-metrics/`. Commit that snapshot along with the source changes. The Pages workflow then installs the JavaScript build dependencies, runs:

```sh
python3 build.py --metrics-manifest published-data/goldilocks-metrics/manifest.json
```

and publishes the generated `goldilocks.html` as the artifact-root `index.html`. No CEDA account token or other repository secret is required for deployment.

The workflow in `.github/workflows/pages.yml` follows GitHub's custom Pages build/deploy model. In repository **Settings → Pages**, select **GitHub Actions** as the publishing source. The deployment environment is `github-pages`.

## Historical climate source download

`download_climate_sources.py` prepares the raw daily temperature archive used by the current 2016–2026 climate build. It discovers rather than hardcodes CEDA's current daily release subdirectory, downloads both `tasmax` and `tasmin` for every month of 2016–2025 from HadUK-Grid v1.3.2.ceda, and also discovers the currently published 2026 provisional monthly files from the Met Office site.

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

## Pollution source download

`download_pollution_sources.py` discovers the required Defra UK-AIR PCM CSV links from the live PCM data page rather than hardcoding the datastore URLs. The default selection is PM2.5, NO₂, PM10 and ozone DGT120 for 2022, 2023 and 2024.

```sh
# Verify that all 12 annual grids are currently discoverable.
python download_pollution_sources.py --dry-run

# Download/resume the source CSVs.
python download_pollution_sources.py --workers 3
```

Downloads go under `data/source/defra-pcm/<year>/` and are git-ignored. A local `discovery.json` records the exact source URLs, byte sizes and SHA-256 hashes. `process_pollution_metrics.py` maps the OSGB cell centres directly onto the canonical Goldilocks 1 km grid; no reprojection or spatial interpolation is required. Cells absent from PCM remain nodata rather than being filled from neighbours.

## Terrain source download

`download_terrain_sources.py` uses the public OS Downloads API to discover the current OS Terrain 50 GB grid download, but deliberately requires it to match the pinned release **2026-07**. If OS publishes a newer annual release, the downloader fails rather than silently changing the build input; the pin should only be updated after reviewing the new release.

```sh
# Check the current OS Downloads API metadata against the pinned release.
python download_terrain_sources.py --dry-run

# Download and MD5-verify the 2026-07 national grid archive.
python download_terrain_sources.py
```

The current source archive is `terr50_gagg_gb.zip` (about 162 MB compressed), stored under `data/source/os-terrain-50/2026-07/` and git-ignored. The archive contains 2,858 nested 10 km × 10 km grid-tile zip files. Each tile contains a 200 × 200 ASCII raster at 50 m spacing. `process_terrain_metrics.py` reads those nested archives directly without expanding the full national dataset on disk.

## Woodland source download

`download_woodland_sources.py` validates a pinned GB woodland source set: Forestry Commission NFI GB 2024, Natural England revised and legacy AWI, NRW AWI 2021, NatureScot AWI, and ONS December 2025 Local Authority District boundaries used only to identify where revised England inventory coverage takes precedence.

```sh
# Check pinned remote metadata without downloading the large source files.
python download_woodland_sources.py --dry-run

# Download/validate the pinned vector inputs.
python download_woodland_sources.py
```

The vector inputs are stored under `data/source/woodland/` and remain git-ignored. The NFI download is the largest input (about 675 MB compressed). `process_woodland_metrics.py` computes exact polygon/canonical-cell intersections in British National Grid coordinates. Ancient-woodland polygon fragments are unioned per 1 km cell before area is measured so overlapping source polygons cannot double-count cover.

## Browser architecture

The data layer is a custom Leaflet `GridLayer` whose tiles are 256 x 256 canvas elements generated locally from the selected metric's embedded raster pyramid. Every metric is transported as a gzip-compressed little-endian `uint16` blob; only the initially selected metric is decompressed at startup, and other metrics are decoded lazily the first time their radio button is selected. Decoded metrics remain cached as typed-array views over one contiguous buffer.

At page startup, the browser decodes the first metric as the canonical land-geometry reference, marks every base-grid intersection required by that geometry at any LOD, and projects each required point exactly once from British National Grid into zoom-0 Web-Mercator world pixels. All metrics share the same grid dimensions and LOD structure, but individual datasets may contain additional nodata cells. The canonical projection lookup is therefore independent of whichever metric is restored from localStorage and is safely reused when switching between datasets. Tile rendering then converts a corner to local canvas coordinates with only `cached_world_pixel * 2^zoom - tile_origin`.

Canvas geometry is batched per tile. Metric values are mapped to a 64-step visual palette interpolated from the colour stops declared in each metric's metadata and cell polygons sharing a palette bin are accumulated into one `Path2D`, placing a fixed upper bound on fill calls even for percentile metrics with hundreds of distinct encoded values. When gridlines are enabled, all valid-cell outlines are accumulated into one additional `Path2D` and drawn with a single `stroke()` call; when disabled, that grid path is not built at all.

For each Leaflet tile zoom, the renderer chooses the finest metric LOD whose nominal cells are at least about four screen pixels across. The reference pixel distance is projected only once at a fixed representative UK location (54.5°N, 2°W), so LOD choice depends only on zoom and thereafter requires only power-of-two scaling. Each tile still performs a small fixed set of inverse WGS84 -> BNG transforms to identify its candidate row/column range. Leaflet manages tile buffering, panning, clipping, recycling, and zoom transforms. Leaflet's default 200 ms tile fade animation is disabled because metric canvases render synchronously; replacement tiles therefore appear immediately instead of fading through the basemap during redraws and zoom changes.

Clicking always resolves against active-metric LOD0, so popups retain the exact quantised 1 km value rather than a coarse averaged value. The selected-cell outline is a separate lightweight Leaflet vector overlay, so changing the selection does not regenerate any metric canvas tiles. The collapsible left-side panel groups metric radio buttons by the categories declared in the processed manifest (currently `heat`, `cold`, `pollution`, `terrain` and `woodland`), followed by gridline visibility, data-layer opacity, a per-metric dual-ended display-range slider with integrated colour samples, description and provenance. Layer opacity defaults to 62% to preserve the original map appearance and is applied by Leaflet to the existing tile layer without repainting raster canvases. Narrowing the display range clips only the colour mapping; underlying raster and popup values remain unchanged. Display-range scrubbing repaints the existing cached metric canvases in place rather than invoking Leaflet `GridLayer.redraw()`, avoiding tile removal/recreation and the resulting basemap flicker. Display ranges, layer opacity, panel state, gridline visibility and selected metric are stored independently in `localStorage`, and each range can be reset to that metric's configured full display range (normally its observed minimum/maximum; woodland is fixed at 0–100%). Metric identifiers are category-prefixed (for example `heat_days_tmax_gt_25`, `cold_air_frost_days`, `terrain_relief` and `woodland_cover`) so the same grouping remains explicit in code and generated data; legacy unprefixed Heat metric selections are migrated automatically. Gridlines default to hidden when no preference has yet been saved. Leaflet's zoom control is positioned at bottom-right so the main information panel can occupy the top-left corner cleanly.

## Basemap selection

The generated page needs internet access for Leaflet and map tiles when it is served over `http:` or `https:`. It uses the standard OpenStreetMap tile server and displays the required OpenStreetMap attribution.

Direct `file://` opening still renders the embedded Goldilocks data layer, but intentionally does not request a third-party basemap. Serve `dist/` with a small HTTP server for the normal local experience. This avoids referrer-less requests to the standard OpenStreetMap tile service and keeps Goldilocks dependent on only one basemap provider.

## Project layout

- `download_climate_sources.py` — authenticated/resumable CEDA download plus provisional 2026 discovery
- `goldilocks_raster.py` — common canonical-grid, quantisation, LOD, compression and dataset-manifest helpers
- `process_climate_metrics.py` — streaming HadUK-Grid climate metric derivation and climate dataset fragment
- `download_pollution_sources.py` — discovers/downloads Defra UK-AIR PCM annual 1 km CSV grids
- `process_pollution_metrics.py` — derives 2022–2024 Pollution means on the canonical grid
- `download_terrain_sources.py` — discovers, pins and MD5-verifies the OS Terrain 50 national ASCII-grid archive
- `process_terrain_metrics.py` — derives 1 km Terrain relief from the 50 m DTM grid
- `download_woodland_sources.py` — downloads and validates the pinned NFI, national AWI and ONS helper boundary inputs
- `process_woodland_metrics.py` — derives current and ancient woodland percentage cover on the canonical grid
- `assemble_metrics.py` — validates and combines dataset fragments into the common Goldilocks metric bundle
- `build.py` — validates/embeds the assembled metric bundle, bundles the frontend and embeds third-party software notices
- `publish_metrics_snapshot.py` — copies the validated assembled bundle into the tracked public snapshot used by Pages
- `.github/workflows/pages.yml` — builds the static artifact from the public snapshot and deploys it to GitHub Pages
- `templates/goldilocks.html` — single-page HTML shell
- `src/app.ts` — Leaflet/custom-canvas multi-metric frontend
- `data/source/` — cached raw source data (HadUK NetCDF + Defra PCM CSV + OS Terrain 50 archive + woodland vectors; git-ignored and never published)
- `data/derived/` — local generated metric bundles (git-ignored)
- `published-data/goldilocks-metrics/` — tracked, publishable snapshot of the derived metric bundle used by GitHub Pages
- `dist/goldilocks.html` — generated application artifact (git-ignored)
- `DATA-LICENCE.md` — source-data licences, attribution and provenance notes
- `THIRD-PARTY-NOTICES.txt` — licences for JavaScript incorporated into the generated HTML

## Data licence and provenance

The current source datasets include Met Office HadUK-Grid, Defra UK-AIR Pollution Climate Mapping, Ordnance Survey OS Terrain 50, Forestry Commission NFI and national ancient-woodland inventories. The source-specific reuse and attribution terms are documented in `DATA-LICENCE.md`; the main sources are available under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/). Goldilocks metrics are derived products and are not official products of the source agencies. The OS Terrain 50-derived metric carries the required acknowledgement: `Contains OS data © Crown copyright and database right 2026.`

Stable 2016–2025 observations are taken from the citable CEDA HadUK-Grid v1.3.2.ceda release:

Met Office; Hollis, D.; Carlisle, E.; Kendon, M.; Packman, S.; Doherty, A. (2026): *HadUK-Grid Gridded Climate Observations on a 1km grid over the UK, v1.3.2.ceda (1836-2025).* NERC EDS Centre for Environmental Data Analysis, 23 June 2026. [doi:10.5285/789b3065d74a4c948ab05d33556c86d0](https://doi.org/10.5285/789b3065d74a4c948ab05d33556c86d0).

Available 2026 climate months are provisional Met Office HadUK-Grid data and may be amended before a later annual CEDA release. Pollution metrics use Defra PCM 2022–2024 annual background grids. Terrain relief uses the pinned OS Terrain 50 2026-07 Great Britain grid. Woodland metrics use pinned Forestry Commission and national ancient-woodland vector datasets. See [`DATA-LICENCE.md`](DATA-LICENCE.md) for full licence, attribution and source-specific provenance notes.