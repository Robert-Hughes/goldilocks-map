# Goldilocks Map

Goldilocks Map is an experimental single-file UK location-suitability explorer built from multiple gridded public datasets plus embedded point-based service data. Raster metrics are grouped into first-class categories (currently **Heat**, **Heat — 2026**, **Cold**, **Pollution**, **Terrain**, **Woodland** and **Travel**), with a separate **Services** control for selected OpenStreetMap POIs.

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

**Heat — 2026**

- observed 2026-to-date days with `tasmax > 25°C`;
- longest observed 2026 run with `tasmax > 25°C`;
- 95th percentile of complete June–August 2026 daily `tasmax`;
- observed 2026-to-date tropical-night count, defined as daily `tasmin > 20°C`.

The 2026-only counts are deliberately not annualised: they cover January through the latest published provisional month and do not treat unpublished later months as zero. The summer percentile is emitted only when all of June, July and August are available.

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

- `woodland_cover`: percentage of each canonical 1 km tile covered by **established woodland extent** in the Forestry Commission National Forest Inventory GB 2024;
- `woodland_ancient_cover`: percentage covered by recognised ancient-woodland inventory sites across England, Wales and Scotland.

The established-woodland metric uses a deliberately strict NFI filter aimed at the house-search requirement for substantial existing woods: `Broadleaved`, `Conifer`, `Coppice`, `Coppice with standards`, `Mixed mainly broadleaved` and `Mixed mainly conifer` are included. `Assumed woodland`, `Young trees`, `Low density` and `Shrub` are excluded, as are `Felled`, `Ground prep`, `Failed` and `Windblow`. The percentage measures NFI woodland-polygon extent, not fractional canopy density within each polygon. This stricter interpretation was adopted after an `Assumed woodland` polygon around the A66/Stainmore area produced 100% values over open moorland; an independent 2023 10 m LiDAR canopy dataset for the North Pennines showed near-zero canopy in those cells and strongly favoured the strict filter. See [`docs/woodland-investigation.md`](docs/woodland-investigation.md). Ancient woodland uses revised Natural England inventory coverage in preference to the legacy inventory where available, all four current NRW 2021 categories in Wales, and NatureScot antiquity classes 1a/2a in Scotland. It measures recognised ancient-woodland site extent rather than current canopy, so it is not necessarily a subset of the established-woodland layer. Overlapping ancient-woodland polygons are geometrically unioned within each 1 km tile before area is measured. Both metrics use the full 100 ha canonical tile as the denominator, are quantised to 0.1 percentage points, and use a fixed 0–100% white-to-dark-green palette. Northern Ireland is nodata.

**Travel**

- `travel_york_weekend`: representative historic weekend driving time to York;
- `travel_york_peak`: representative weekday PM-peak driving time to York;
- `travel_cambridge_weekend`: representative historic weekend driving time to Cambridge;
- `travel_cambridge_peak`: representative weekday PM-peak driving time to Cambridge.

Travel is precomputed offline rather than routed in the browser. The topology is the generalised OS Open Roads 2026-04 GB network. Matched England Strategic Road Network links use National Highways historic link speeds: weekend is the mean traversal time from the `Normal Saturday` and `Normal Sunday` aggregations, while peak uses `PM Peak`. Non-trunk A roads use DfT's 2025 flow-weighted speed by local authority and road number where available; unmatched A roads use the latest DfT country urban/rural averages with OS Open Built Up Areas. The peak local-A model applies the latest country weekday-evening-peak/all-day ratio. B/minor/local roads retain transparent road-function/form fallbacks because more elaborate guessed context degraded the validation set.

Each canonical cell centre inherits the travel time of its nearest Open Roads graph node; no additional driveway/access-leg penalty is added. Cells more than 20 km from the GB-only source graph (principally Northern Ireland) and cells on disconnected road components that cannot reach York/Cambridge are nodata. These are comparative suitability estimates, not live traffic, turn-by-turn routes or guaranteed journey times. The investigation, 128-route external validation and rejected alternatives are documented in [`docs/travel-time-investigation.md`](docs/travel-time-investigation.md).


## Services

Goldilocks embeds a separate point-based service layer derived offline from public OSM/NHS datasets. **Supermarkets**, **Convenience / village shops**, **Post offices** and **Pharmacies** come from the pinned OpenStreetMap Great Britain extract. Supermarkets and convenience shops remain separate OSM-derived categories rather than treating every grocery shop as a supermarket. **GPs** and **NHS dentists** come from current NHS England Organisation Data Service reports plus Public Health Scotland practice distributions. In England, **General hospitals** include ERIC `General acute hospital` and `Mixed service hospital` sites and **Community hospitals** include ERIC `Community hospital (with inpatient beds)` sites. In Scotland, the current PHS hospital-code list is joined to the 2024/25 Scottish Health Service Costs Hospital Profile: Costs Book groups `A1`, `A2` and `A3` are General hospitals and `J26` is Community hospitals. Other Scottish hospital groups are excluded.

GPs and NHS dentists are currently simple presence/absence layers: Goldilocks does not distinguish whether a practice is accepting new patients. Current acceptance data is not available from the open ODS/PHS distributions used here, and the investigated NHS Directory of Healthcare Services API requires separately approved/onboarded access. Rather than expose permanently unusable acceptance filters, that enhancement is deferred. Goldilocks does not scrape nhs.uk HTML pages.

NHS/PHS/ERIC records are geocoded offline from their postcodes with pinned OS Code-Point Open 2026-08. OSM mapped areas are reduced to representative points and same-name near-coincident duplicates are collapsed. The combined 80,342 POIs are bucketed into 0.25° WGS84 cells, grouped by service category, gzip-compressed and embedded in the single HTML. There is no fixed service zoom threshold: selected markers are shown whenever at most 200 fall inside the current viewport; if the selection would produce more than 200, no service markers are rendered and the panel asks the user to zoom in or select fewer services. Service filters persist in `localStorage`; all service categories default to off on a fresh browser profile. Marker popups show the service type and an official source link. See [`docs/nhs-services-investigation.md`](docs/nhs-services-investigation.md) for source/API decisions and current coverage limitations.

An air-frost day is assigned when the minimum air temperature falls below freezing during the observation period; it does not mean the whole day remains below freezing. A day whose maximum temperature remains below freezing is instead an ice day.

Stable historical observations come from CEDA HadUK-Grid v1.3.2.ceda for 2016–2025. Published provisional 2026 months are also included. For the multi-year annual-count metrics, each calendar month is averaged across the years available for that month and the twelve monthly means are summed. This allows published 2026 months to contribute without treating unpublished months as zero. The separate **Heat — 2026** category instead reports observed 2026-to-date counts plus a complete-summer percentile, so it exposes the exceptional current year without pretending the partial calendar year is a full annual climatology. The winter Tmin percentile uses complete DJF winters only; with the current archive these are winters 2016–17 through 2025–26.

HadUK-Grid is a gridded/interpolated climate-observation dataset. A 1 km grid-cell value should not be interpreted as a thermometer measurement physically made at that exact location.

The longest-run metric is intentionally a hard-threshold statistic and is therefore not spatially smooth even when the underlying `tasmax` field is smooth. A single day at 25.01°C versus 24.99°C can preserve or break a multi-week run and create a sharp boundary between neighbouring 1 km cells. An independent year-by-year audit of the current bundle matched the stored streak raster exactly; every cell whose longest run is at least 25 days is driven by the provisional 2026 summer.

## Data quality pruning

The offline metric processor deliberately masks eight 1 km HadUK-Grid cells covering the St Kilda archipelago before deriving any metric or building any LOD level. The exclusion is expressed as exact British National Grid cell centres, not as a geographic bounding box, so neighbouring Hebridean cells are not affected.

A full Jan 2016–Aug 2026 cross-variable audit compared each daily `tasmin` value with the `tasmax` value covering the same 24-hour observation period. St Kilda was the only severe tropical-night failure cluster: all UK cells with Tmin/Tmax ordering errors greater than 10°C were these eight cells, and every tropical-night candidate with an ordering error greater than 5°C occurred there. Across the eight St Kilda cells, 152 of 160 `tasmin > 20°C` candidate events had a Tmin-above-Tmax inconsistency, demonstrating that the apparent tropical-night maximum was an interpolation artefact rather than a credible climate signal.

The excluded BNG cell centres are `(9500,898500)`, `(8500,899500)`, `(9500,899500)`, `(10500,899500)`, `(8500,900500)`, `(9500,900500)`, `(6500,901500)`, and `(15500,905500)`. This removes 8 of 245,077 source-valid cells (about 0.0033%). The smaller inconsistencies observed around the Isles of Scilly are retained: they do not produce comparable >5°C tropical-night ordering errors and do not materially distort the metric.

The derived manifest records the pruning rule, audit rationale and exact cells so generated products remain traceable.

## Build

Prerequisites: Python 3.11+, Node.js/npm, internet access for source/dependency downloads, and GDAL/`ogr2ogr` with the OSM driver when regenerating the service POI bundle.

On GhostBSD/FreeBSD, using the packaged scientific/geospatial stack avoids lengthy local compilation:

```sh
sudo pkg install py312-numpy py312-h5py py312-pyproj py312-fiona py312-shapely gdal
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
python process_travel_metrics.py
python assemble_metrics.py
python process_service_pois.py
python process_military_areas.py
python build.py
```

`goldilocks_raster.py` provides the common canonical-grid, quantisation, nodata-aware LOD and gzip-blob machinery. Each dataset processor writes a self-contained dataset manifest with categories, metrics and source provenance. `assemble_metrics.py` validates that those fragments use the same canonical 1 km BNG raster geometry, merges their categories/sources and copies the metric blobs into `data/derived/goldilocks-metrics/`.

`process_climate_metrics.py` streams the monthly NetCDF/HDF5 files; summer percentile input is staged in a temporary memory-mapped file so the full multi-year daily cube is never held in RAM. `process_pollution_metrics.py` aligns the 2022–2024 Defra PCM CSV grids to the canonical Goldilocks/HadUK 1 km grid and derives the three-year arithmetic means. `process_terrain_metrics.py` reads the nested OS Terrain 50 ASCII tiles directly from the national zip, reduces each aligned 20 × 20 block of 50 m pixel-centre heights to 1 km relief (`max - min`), and leaves canonical cells outside Great Britain as nodata. `process_woodland_metrics.py` uses Fiona/Shapely to intersect pinned NFI/AWI polygons exactly with the canonical grid, including overlap-unioning for the stitched ancient-woodland layer. `process_travel_metrics.py` builds the pinned OS Open Roads GB graph, joins National Highways and DfT observed speed data, performs four reverse shortest-path searches and samples the resulting node-time fields to the canonical 1 km grid; expensive source-to-graph classifications and cell snaps are cached under ignored `data/source/travel-time/cache/`.

`process_service_pois.py` uses GDAL's OSM driver to stream the pinned Great Britain `.osm.pbf`, extracts supermarket/convenience/post-office/pharmacy node and area features, de-duplicates coincident node/area representations, spatially buckets the result and writes a compressed service bundle under `data/derived/services/`. `process_military_areas.py` uses the same pinned OSM extract to select military polygons, separates explicit danger/range areas from other `landuse=military` polygons, simplifies their boundaries offline and writes a separate compressed overlay bundle under `data/derived/military-areas/`.

`build.py` validates the assembled raster, service and military-area blobs and source references, bundles/minifies the TypeScript frontend (including `proj4` and `fflate`), base64-embeds the compressed blobs and metadata, and writes the self-contained application to `dist/goldilocks.html`.

For development while the source archive is still downloading, a partial preview can be generated explicitly, for example:

```sh
python process_climate_metrics.py --min-year 2016 --max-year 2016 --no-provisional --allow-partial --output-dir data/derived/climate-metrics-preview
python assemble_metrics.py --dataset-manifest data/derived/climate-metrics-preview/manifest.json --output-dir data/derived/goldilocks-metrics-preview
python build.py --metrics-manifest data/derived/goldilocks-metrics-preview/manifest.json
```

## GitHub Pages deployment

The public repository is intended to be named `goldilocks-map`, giving a default project-site URL of `https://<account>.github.io/goldilocks-map/`.

The raw CEDA/Met Office NetCDF archive, Defra PCM CSV inputs, OS Terrain 50 archive, woodland vectors and the ~2 GB OSM PBF are never required by GitHub Actions and remain git-ignored. After regenerating local data, explicitly refresh the publishable snapshots with:

```sh
python publish_metrics_snapshot.py
python publish_services_snapshot.py
python publish_military_snapshot.py
```

This copies only the assembled raster manifest/blobs into `published-data/goldilocks-metrics/`, the processed compressed POI bundle into `published-data/goldilocks-services/`, and the simplified military polygon bundle into `published-data/goldilocks-military-areas/`. Commit those snapshots along with the source changes. The Pages workflow then installs the JavaScript build dependencies and runs:

```sh
python3 build.py --metrics-manifest published-data/goldilocks-metrics/manifest.json --services-manifest published-data/goldilocks-services/manifest.json --military-manifest published-data/goldilocks-military-areas/manifest.json
```

and publishes the generated `goldilocks.html` as the artifact-root `index.html`. No CEDA account token, raw OSM extract or other repository secret is required for deployment.

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

## Travel source download

`download_travel_sources.py` recreates the pinned open-data input set for the York/Cambridge driving-time model: OS Open Roads 2026-04, OS Open Built Up Areas 2026-04, DfT CGN local-A-road speed workbooks, ONS December 2025 LAD boundaries, and National Highways Travel Time Reporting Tool link observations for Annual, Normal Saturday, Normal Sunday and PM Peak. Source versions, URLs and fixed-file checksums are tracked in `travel-time-sources.json`; the generated discovery metadata records hashes/counts for the downloaded dynamic query results.

```sh
# Validate the pinned OS release metadata and show intended source requests.
python download_travel_sources.py --dry-run

# Download/verify/extract the source set.
python download_travel_sources.py

# Build the four Travel rasters.
python process_travel_metrics.py
```

Raw sources and reproducible intermediate caches remain under git-ignored `data/source/travel-time/`. The two OS archives are the large inputs (Open Roads is about 606 MB compressed; Open Built Up Areas about 44 MB). The generated Travel fragment is only about 1.1 MB of compressed metric blobs. See [`docs/travel-time-investigation.md`](docs/travel-time-investigation.md) for the model rationale, validation results, rejected alternatives and limitations.

## Service and military-area source download

`download_service_sources.py` prepares the shared offline source set. It retains the pinned Geofabrik OpenStreetMap Great Britain PBF dated **2026-09-06**, pins OS Code-Point Open **2026-08**, Public Health Scotland GP **July 2026** and dental **March 2026** distributions, pins the PHS **2024/25 Hospital Profile** and **Costs hospital classification** workbooks, pins ERIC **2024/25**, and refreshes the current NHS England ODS GP/dental reports plus the current PHS NHS-hospital code list. The large OSM PBF and all NHS/PHS/OS source files remain git-ignored under `data/source/services/`.

```sh
# Show the selected sources without downloading them.
python download_service_sources.py --dry-run

# Download/update all open-data sources; --verify additionally re-hashes the 2 GiB OSM PBF.
python download_service_sources.py --verify

# Build the combined OSM/NHS service database and OSM military polygon overlays.
python process_service_pois.py
python process_military_areas.py
```

No NHS HTML is scraped. `process_service_pois.py` requires `ogr2ogr` with GDAL's OSM driver and uses `pyproj` for Code-Point Open postcode coordinates. It caches the OSM node/area extraction so subsequent NHS refreshes do not rescan the 2 GiB PBF. `process_military_areas.py` also requires GDAL and caches its simplified military polygon extraction beside the service OSM caches. The derived service and military-area manifests/payloads are local build inputs; the corresponding publish scripts copy only those processed files into the tracked public snapshots.

## Browser architecture

The data layer is a custom Leaflet `GridLayer` whose tiles are 256 x 256 canvas elements generated locally from the selected metric's embedded raster pyramid. Every metric is transported as a gzip-compressed little-endian `uint16` blob; only the initially selected metric is decompressed at startup, and other metrics are decoded lazily the first time their radio button is selected. Decoded metrics remain cached as typed-array views over one contiguous buffer.

At page startup, the browser decodes the first metric as the canonical land-geometry reference, marks every base-grid intersection required by that geometry at any LOD, and projects each required point exactly once from British National Grid into zoom-0 Web-Mercator world pixels. All metrics share the same grid dimensions and LOD structure, but individual datasets may contain additional nodata cells. The canonical projection lookup is therefore independent of whichever metric is restored from localStorage and is safely reused when switching between datasets. Tile rendering then converts a corner to local canvas coordinates with only `cached_world_pixel * 2^zoom - tile_origin`.

Canvas geometry is batched per tile. Metric values are mapped to a 64-step visual palette interpolated from the colour stops declared in each metric's metadata and cell polygons sharing a palette bin are accumulated into one `Path2D`, placing a fixed upper bound on fill calls even for percentile metrics with hundreds of distinct encoded values. When gridlines are enabled, all valid-cell outlines are accumulated into one additional `Path2D` and drawn with a single `stroke()` call; when disabled, that grid path is not built at all.

For each Leaflet tile zoom, the renderer chooses the finest metric LOD whose nominal cells are at least about four screen pixels across. The reference pixel distance is projected only once at a fixed representative UK location (54.5°N, 2°W), so LOD choice depends only on zoom and thereafter requires only power-of-two scaling. Each tile still performs a small fixed set of inverse WGS84 -> BNG transforms to identify its candidate row/column range. Leaflet manages tile buffering, panning, clipping, recycling, and zoom transforms. Leaflet's default 200 ms tile fade animation is disabled because metric canvases render synchronously; replacement tiles therefore appear immediately instead of fading through the basemap during redraws and zoom changes.

Services are handled by a separate `ServicesController`, not by the raster renderer. The gzip service payload is decoded only when at least one service filter is enabled. Within each 0.25° spatial bucket the POIs are grouped by service category, so sparse selections do not scan unrelated service records. On each pan/zoom/filter change the controller counts only selected POIs in the current viewport, using category-array lengths for fully enclosed buckets and coordinate checks only on edge buckets; the count short-circuits as soon as it exceeds the configured 200-marker ceiling. At most 200 selected services are instantiated as Leaflet markers; above that threshold all service markers are suppressed until the user zooms in or selects fewer categories. Each service type has a distinct CSS/SVG `DivIcon`, and every service type is independently toggleable.

Military polygons are handled by a separate `MilitaryAreasController`. The OSM-derived payload is decoded lazily only if one of the military-area checkboxes is enabled. Explicit `military=danger_area` and `military=range` polygons form the **Dangerous** overlay; other `landuse=military` polygons form **Unspecified**, meaning that Goldilocks is not claiming an explicit OSM danger classification for them. Both overlays are independent, persisted toggles and retain exact OSM feature links in their popups.

Clicking always resolves against active-metric LOD0, so popups retain the exact quantised 1 km value rather than a coarse averaged value. The selected-cell outline is a separate lightweight Leaflet vector overlay, so changing the selection does not regenerate any metric canvas tiles. The collapsible left-side panel groups metric radio buttons by the categories declared in the processed manifest (currently `heat`, `heat_2026`, `cold`, `pollution`, `terrain`, `woodland` and `travel`), followed by service controls, the same-line **Military areas: Dangerous / Unspecified** toggles, a monochrome-basemap toggle, gridline visibility, saved-location-pin visibility, data-layer opacity, a per-metric dual-ended display-range slider with integrated colour samples, description and provenance. Layer opacity defaults to 62% to preserve the original map appearance and is applied by Leaflet to the existing tile layer without repainting raster canvases. Narrowing the display range clips only the colour mapping; underlying raster and popup values remain unchanged. Display-range scrubbing repaints the existing cached metric canvases in place rather than invoking Leaflet `GridLayer.redraw()`, avoiding tile removal/recreation and the resulting basemap flicker. Display ranges, layer opacity, panel state, military-area visibility, monochrome-basemap state, gridline visibility, location-pin visibility and selected metric are stored independently in `localStorage`, and each range can be reset to that metric's configured full display range (normally its observed minimum/maximum; woodland is fixed at 0–100%). Metric identifiers are category-prefixed (for example `heat_days_tmax_gt_25`, `cold_air_frost_days`, `terrain_relief`, `woodland_cover` and `travel_york_weekend`) so the same grouping remains explicit in code and generated data; legacy unprefixed Heat metric selections are migrated automatically. Gridlines, military overlays and monochrome basemap default to off and location pins default to visible when no preference has yet been saved. Leaflet's zoom control is positioned at bottom-right so the main information panel can occupy the top-left corner cleanly.

Saved locations are a separate browser-only layer. Each location has a stable ID, name, WGS84 latitude/longitude and free-text notes, and the ordered list is stored under a versioned `localStorage` key. A collapsible top-right panel supports editing, up/down reordering, deletion and panning to a location. Export and import are separate expandable sections: export copies versioned JSON directly to the clipboard, while import accepts pasted JSON and replaces the complete list only after confirmation. Location names are rendered directly on the map as Leaflet label markers, and the top-left display controls can hide/show those markers without changing the saved list. Right-clicking the map (or long-pressing on touch devices through Leaflet's tap-hold handler) offers `Add location here`; right-clicking/long-pressing a saved marker offers `View / edit location`, which opens the corresponding panel editor. Location data is intentionally independent of metric decoding; there is currently no metric-at-location summary or comparison view. On narrow viewports (600 px or less), both top controls collapse to compact `+` buttons, expanding either panel automatically collapses the other, and the expanded panel's Leaflet corner is raised above the opposite control as a final overlap safeguard.

## Basemap selection

The generated page needs internet access for Leaflet and map tiles when it is served over `http:` or `https:`. It uses the standard OpenStreetMap tile server and displays the required OpenStreetMap attribution. The **Monochrome basemap** checkbox applies a browser-side CSS grayscale filter only to the OSM tile-layer container; Goldilocks raster overlays, service markers and saved-location labels retain their normal colours. Toggling it does not request different tiles and does not introduce another basemap provider.

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
- `process_woodland_metrics.py` — derives strict established-woodland extent and ancient woodland percentage cover on the canonical grid
- `docs/woodland-investigation.md` — A66/Stainmore mismatch investigation, regional LiDAR validation and future canopy-source candidates
- `travel-time-sources.json` — pinned versions, URLs and hashes for the Travel source set
- `download_travel_sources.py` — downloads/verifies/extracts the pinned open Travel inputs
- `travel_time_routing.py` — compact Open Roads graph parsing, matching, CSR and reverse-Dijkstra helpers
- `process_travel_metrics.py` — derives York/Cambridge weekend and weekday-PM-peak driving-time rasters
- `docs/travel-time-investigation.md` — experiment history, validation, final model and limitations
- `experiments/travel-time/` — reusable external-validation origin set and Google comparison harness
- `download_service_sources.py` — prepares the pinned/dynamic OSM, NHS/PHS, ERIC and Code-Point Open service sources
- `process_service_pois.py` — filters, postcode-geocodes, de-duplicates, buckets and compresses the combined service POIs
- `publish_services_snapshot.py` — copies the validated processed service bundle into the tracked public snapshot
- `docs/nhs-services-investigation.md` — NHS source/API decisions, ERIC hospital classification and current coverage limitations
- `process_military_areas.py` — extracts, classifies, simplifies and compresses OSM military-area polygons
- `publish_military_snapshot.py` — copies the validated military-area bundle into the tracked public snapshot
- `docs/military-areas-investigation.md` — OSM tag classification, processing choices and safety/access limitations
- `assemble_metrics.py` — validates and combines dataset fragments into the common Goldilocks metric bundle
- `build.py` — validates/embeds the raster, service and military-area bundles, bundles the frontend and embeds third-party software notices
- `publish_metrics_snapshot.py` — copies the validated assembled raster bundle into the tracked public snapshot used by Pages
- `.github/workflows/pages.yml` — builds the static artifact from the public snapshots and deploys it to GitHub Pages
- `templates/goldilocks.html` — single-page HTML shell and control/marker styling
- `src/app.ts` — browser startup/orchestration and base-map wiring
- `src/metrics.ts` — lazy metric decoding, display ranges and palette handling
- `src/raster.ts` — projected custom Leaflet raster layer, cell hit-testing and selection outline
- `src/services.ts` — lazy service-payload decoding, viewport buckets, toggles and Leaflet POI markers
- `src/military-areas.ts` — lazy military-polygon decoding, persisted toggles, styling and OSM-linked popups
- `src/info-panel.ts` — top-left metric/service/military/data controls and provenance panel
- `src/locations.ts` — saved-location persistence, labels, context actions, editing and JSON import/export
- `src/storage.ts` — small resilient helpers for persisted UI preferences
- `src/types.ts` — shared frontend data-model types
- `data/source/` — cached raw source data (HadUK NetCDF + Defra PCM CSV + OS Terrain 50 + woodland vectors + Travel inputs + OSM/NHS/PHS/ERIC/Code-Point inputs; git-ignored and never published)
- `data/derived/` — local generated metric, service and military-area bundles (git-ignored)
- `published-data/goldilocks-metrics/` — tracked, publishable snapshot of the derived raster bundle used by GitHub Pages
- `published-data/goldilocks-services/` — tracked, publishable snapshot of the compact processed service database used by GitHub Pages
- `published-data/goldilocks-military-areas/` — tracked, publishable snapshot of the simplified military polygon overlays used by GitHub Pages
- `dist/goldilocks.html` — generated application artifact (git-ignored)
- `DATA-LICENCE.md` — source-data licences, attribution and provenance notes
- `THIRD-PARTY-NOTICES.txt` — licences for JavaScript incorporated into the generated HTML

## Data licence and provenance

The current source datasets include Met Office HadUK-Grid, Defra UK-AIR Pollution Climate Mapping, Ordnance Survey OS Terrain 50/Open Roads/Open Built Up Areas/Code-Point Open, Forestry Commission NFI and national ancient-woodland inventories, DfT road-congestion statistics, National Highways historic travel-time observations, ONS administrative boundaries, OpenStreetMap service POIs, NHS England ODS/ERIC and Public Health Scotland practice data. Source-specific reuse and attribution terms are documented in `DATA-LICENCE.md`; NHS API data has separate onboarding/connection terms and is not present in the current public service snapshot.

Stable 2016–2025 observations are taken from the citable CEDA HadUK-Grid v1.3.2.ceda release:

Met Office; Hollis, D.; Carlisle, E.; Kendon, M.; Packman, S.; Doherty, A. (2026): *HadUK-Grid Gridded Climate Observations on a 1km grid over the UK, v1.3.2.ceda (1836-2025).* NERC EDS Centre for Environmental Data Analysis, 23 June 2026. [doi:10.5285/789b3065d74a4c948ab05d33556c86d0](https://doi.org/10.5285/789b3065d74a4c948ab05d33556c86d0).

Available 2026 climate months are provisional Met Office HadUK-Grid data and may be amended before a later annual CEDA release. Pollution metrics use Defra PCM 2022–2024 annual background grids. Terrain relief uses the pinned OS Terrain 50 2026-07 Great Britain grid. Woodland metrics use pinned Forestry Commission and national ancient-woodland vector datasets. Travel metrics use pinned OS Open Roads/Open Built Up Areas, DfT local-A-road statistics, National Highways historic SRN speeds and ONS LAD boundaries; see [`docs/travel-time-investigation.md`](docs/travel-time-investigation.md). Service POIs use OSM 2026-09-06 plus ODS/PHS primary-care data, ERIC 2024/25 hospitals and Code-Point Open 2026-08; see [`docs/nhs-services-investigation.md`](docs/nhs-services-investigation.md). See [`DATA-LICENCE.md`](DATA-LICENCE.md) for full licence, attribution and provenance notes.