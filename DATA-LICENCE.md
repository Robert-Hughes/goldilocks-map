# Data licence and attribution

Goldilocks Map contains derived raster metrics from multiple UK public datasets plus a compact point-of-interest database assembled from OpenStreetMap and official NHS/OS reference data. The current service build combines selected OSM POIs, NHS England Organisation Data Service GP/dental records, Public Health Scotland GP/dental records, NHS England ERIC hospital sites, and OS Code-Point Open postcode coordinates.

## Licence

HadUK-Grid, Defra PCM, OS OpenData products, Forestry Commission NFI, Natural England AWI, Natural Resources Wales AWI, DfT local-road statistics, National Highways travel-time data, ONS helper boundaries and the NHS/PHS reference datasets used here are open-data inputs whose source-specific terms and acknowledgements are recorded below. OpenStreetMap service POIs are subject to the Open Database License (ODbL) 1.0. Do not replace source-specific acknowledgements with a single generic attribution. NHS API data, if an authorised acceptance-status snapshot is added later, remains subject to the connection/API terms agreed during onboarding and must not be assumed to inherit the open-data licence of ODS/ERIC.

Goldilocks Map publishes derived raster summaries plus a compact processed service database needed by the browser; it does not publish the raw source archives. Goldilocks processing choices and classifications are not official products of the source agencies.

## Met Office HadUK-Grid

The stable 2016–2025 observations come from the citable CEDA HadUK-Grid v1.3.2.ceda release:

Met Office; Hollis, D.; Carlisle, E.; Kendon, M.; Packman, S.; Doherty, A. (2026): *HadUK-Grid Gridded Climate Observations on a 1km grid over the UK, v1.3.2.ceda (1836-2025).* NERC EDS Centre for Environmental Data Analysis, 23 June 2026. [doi:10.5285/789b3065d74a4c948ab05d33556c86d0](https://doi.org/10.5285/789b3065d74a4c948ab05d33556c86d0).

The current climate build also includes published 2026 provisional HadUK-Grid temperature grids from the [Met Office HadUK-Grid provisional-data service](https://www.metoffice.gov.uk/hadobs/hadukgrid/). The Met Office states that provisional grids may be amended before inclusion in a later annual release.

Method reference: Hollis, D.; McCarthy, M. P.; Kendon, M.; Legg, T.; Simpson, I. (2019): *HadUK-Grid — A new UK dataset of gridded climate observations.* Geoscience Data Journal, 6, 151–159. [doi:10.1002/gdj3.78](https://doi.org/10.1002/gdj3.78).

## Defra UK-AIR Pollution Climate Mapping

The Pollution category uses the 2022, 2023 and 2024 1 km background grids published on the [Defra UK-AIR PCM data page](https://uk-air.defra.gov.uk/data/pcm-data). Goldilocks derives three-year arithmetic means for PM2.5, NO₂, PM10 and ozone DGT120 exceedance days.

Defra requires the following attribution when reusing UK-AIR information:

> © Crown 2026 copyright Defra via uk-air.defra.gov.uk, licenced under the Open Government Licence (OGL).

PCM values are modelled background concentrations or exceedance metrics, not measurements made at individual 1 km cell centres. Defra updates the PCM modelling methodology over time, so the annual grids should not be treated as a perfectly homogeneous observational time series.

## Ordnance Survey OS Terrain 50

The Terrain category uses the **2026-07** OS Terrain 50 Great Britain ASCII DTM grid downloaded through the OS Downloads API. OS Terrain 50 is an annual OpenData product with 50 m pixel-centre heights and is designed for broad-scale terrain analysis.

Goldilocks derives `terrain_relief` as the maximum minus minimum Terrain 50 elevation inside each canonical 1 km cell. The derived public raster is therefore a Goldilocks product, not an Ordnance Survey product. The source product covers Great Britain rather than Northern Ireland. In coastal source tiles, OS Terrain 50 models tidal-water heights as part of the supplied surface, so coastal relief can include the vertical transition between land and those modelled tidal-water heights.

The required OS OpenData acknowledgement for this release is:

> Contains OS data © Crown copyright and database right 2026.

## Forestry Commission National Forest Inventory GB 2024

The `woodland_cover` metric is derived from the National Forest Inventory GB 2024 woodland map using a strict established-woodland filter: `Broadleaved`, `Conifer`, `Coppice`, `Coppice with standards`, `Mixed mainly broadleaved` and `Mixed mainly conifer` are included; `Assumed woodland`, `Young trees`, `Low density`, `Shrub`, `Felled`, `Ground prep`, `Failed` and `Windblow` are excluded. The metric is exact polygon area within each 1 km canonical cell divided by the full 100 ha cell area; it is established-woodland polygon extent, not fractional canopy density within NFI polygons.

The source item states that use is subject to the Open Government Licence and requires:

> © Forestry Commission copyright. Contains Ordnance Survey data © Crown copyright and database right 2025.

## Ancient Woodland Inventories

`woodland_ancient_cover` is a Goldilocks harmonisation of three national inventories. The national inventories are not methodologically identical, so the raster should be interpreted as recognised ancient-woodland inventory-site coverage rather than as one homogeneous GB survey or a measure of current canopy. It is therefore not necessarily a subset of `woodland_cover`.

For **England**, Goldilocks uses Natural England's revised completed-counties inventory in preference to the legacy inventory where revised coverage is available, and legacy AWI elsewhere. The source is Open Government Licence data and requires:

> © Natural England 2024. Contains OS data © Crown copyright and database rights 2024. OS AC0000851168.

Because the revised download does not expose a county field, Goldilocks uses ONS December 2025 Local Authority District boundaries as an explicit processing helper to identify revised-coverage areas. ONS digital boundaries are OGL data and require:

> Source: Office for National Statistics licensed under the Open Government Licence v.3.0. Contains OS data © Crown copyright and database right 2025.

For **Wales**, Goldilocks uses the Natural Resources Wales Ancient Woodland Inventory 2021 and includes the four current categories ASNW, RAWS, PAWS and AWSU; records explicitly marked `Deleted` are excluded. The source is OGL data and requires:

> Contains Natural Resources Wales information © Natural Resources Wales and Database Right. All rights Reserved. Contains Ordnance Survey Data. Ordnance Survey Licence number AC0000849444. Crown Copyright and Database Right.

For **Scotland**, Goldilocks uses NatureScot AWI antiquity classes `1a` and `2a` only, corresponding to the inventory's Ancient (of semi-natural origin) category. The dataset metadata describes the source as available under the OS Open Data licence and requires:

> Copyright NatureScot Contains Ordnance Survey data © Crown copyright and database right 2026.

Accepted ancient-woodland polygons are clipped to the canonical grid and geometrically unioned within each 1 km cell before their area is measured. This prevents overlaps within or between the stitched national inventories from being counted twice. Northern Ireland is outside the v1 Woodland coverage and is nodata.

## Travel-time model sources

The Travel category is a Goldilocks-derived model, not an official routing product. It publishes four 1 km rasters: representative weekend and weekday-PM-peak driving time to York and Cambridge. The complete methodology, validation history and limitations are preserved in [`docs/travel-time-investigation.md`](docs/travel-time-investigation.md).

The routing topology is **OS Open Roads 2026-04**, a Great Britain OpenData road link/node network. Goldilocks uses the ESRI Shapefile national download and retains only the graph and attributes needed to derive traversal costs. **OS Open Built Up Areas 2026-04** is used only to classify unmatched local A-road links into settlement/rural context. Both products are Ordnance Survey OpenData under the Open Government Licence. The applicable acknowledgement is:

> Contains OS data © Crown copyright and database right 2026.

For local A roads, Goldilocks uses the Department for Transport CGN average-speed tables for England, Scotland and Wales published with the August 2026 travel-time statistics update. A road's annual observed speed is joined by ONS Local Authority District code and road number where possible. Where no direct road-level observation exists, Goldilocks uses the latest country urban/rural rolling-year speed observations together with OS Open Built Up Areas. The weekday-PM-peak variant adjusts local-A costs by the DfT country weekday-evening-peak/all-day speed ratio. These DfT statistics are published under the Open Government Licence.

For the English Strategic Road Network, Goldilocks uses historic link observations from the **National Highways Travel Time Reporting Tool**, reporting period April 2024–March 2025. The weekend layer averages `Normal Saturday` and `Normal Sunday` link traversal costs; the peak layer uses `PM Peak`. The source observations are used only as offline cost inputs and are not redistributed as a road/link dataset. National Highways open data is used under the Open Government Licence.

ONS December 2025 Local Authority District boundaries are used solely as a processing helper to join DfT road-number observations to Open Roads links. The required ONS acknowledgement is:

> Source: Office for National Statistics licensed under the Open Government Licence v.3.0. Contains OS data © Crown copyright and database right 2025.

The travel-time rasters inherit important limitations from the model. OS Open Roads is a generalised network rather than a navigation-grade road graph; detailed turn/one-way/access restrictions and ferry connectivity are not comprehensively represented. Observed National Highways costs cover the English SRN, while non-SRN and Scottish/Welsh trunk roads rely more heavily on DfT observations or transparent road-class fallbacks. The metrics represent historic typical regimes rather than current incidents, closures or live traffic. Goldilocks assigns each covered 1 km cell the travel time of its nearest usable Open Roads graph node without an additional driveway/access-time penalty. Northern Ireland is nodata because OS Open Roads covers Great Britain.

## OpenStreetMap service POIs

The Services controls use a pinned **OpenStreetMap Great Britain** extract dated **2026-09-06**, distributed by [Geofabrik](https://download.geofabrik.de/). Goldilocks extracts only `shop=supermarket`, `amenity=post_office` and `amenity=pharmacy`. Node POIs are retained directly; qualifying mapped areas are reduced to a representative interior point. Same-name near-coincident representations are de-duplicated.

OpenStreetMap data is © OpenStreetMap contributors and available under the [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/). The required attribution is shown in the Leaflet attribution control whenever the embedded service database is available:

> © OpenStreetMap contributors

The OSM-derived portion of the processed service database remains subject to the ODbL. Source OSM element references are retained for traceability.

## NHS primary-care and hospital service data

GP and NHS-dental organisation presence is assembled from NHS England Organisation Data Service Data Search and Export reports (`epraccur` and `egdpprac`) together with Public Health Scotland's **GP Practice Contact Details and List Sizes** (July 2026) and **Dental Practices and Patient Registrations** (March 2026). These public reference datasets are reused under the Open Government Licence. Goldilocks retains only the practice identity/name/postcode needed for the service map.

The hospital layers use NHS England **Estates Returns Information Collection (ERIC) 2024/25** site data. Goldilocks includes only ERIC `General acute hospital`, `Mixed service hospital` and `Community hospital (with inpatient beds)` site types, combining the first two into the General hospital display category. ERIC is an England secondary-care estate collection; the current hospital layer therefore does not claim equivalent Scottish/Welsh coverage.

The current public bundle contains no NHS API-derived acceptance data. If acceptance enrichment is enabled later, use of the NHS Directory of Healthcare Services API is conditional on the approved onboarding/connection terms for that integration; those API terms must be reviewed before publishing or redistributing a refreshed acceptance snapshot.

## OS Code-Point Open

NHS/PHS/ERIC postcodes are converted to map coordinates using pinned **OS Code-Point Open 2026-08** GB CSV data. The source is OS OpenData under the Open Government Licence and requires:

> Contains OS data © Crown copyright and database right 2026.

Goldilocks publishes only the resulting service-point coordinates, not the raw Code-Point archive.

## What Goldilocks publishes

Raw HadUK-Grid NetCDF files, Defra PCM CSV files, OS source archives, NFI/AWI vector downloads, DfT workbooks, National Highways link observations, ONS helper boundaries, the Geofabrik OSM PBF and raw NHS/PHS/ERIC/Code-Point service inputs are build inputs under `data/source/` and are not committed. The tracked public snapshots contain Goldilocks-derived quantised metric rasters and the compact processed service database with source metadata. Metric definitions, averaging choices, quality-control masking, routing/traversal-time modelling, service filtering/de-duplication, postcode geocoding and presentation are Goldilocks Map processing choices rather than official source-agency products.