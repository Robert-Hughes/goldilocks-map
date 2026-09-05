# Data licence and attribution

Goldilocks Map contains derived raster metrics from multiple UK public datasets. The current build uses Met Office HadUK-Grid climate observations, Defra UK-AIR Pollution Climate Mapping (PCM) background pollution grids, and the Ordnance Survey OS Terrain 50 digital terrain model.

## Licence

All three source datasets used by the current build are made available under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).

Suggested attribution:

> Contains Met Office HadUK-Grid data © Crown copyright, licensed under the Open Government Licence v3.0. Contains Defra UK-AIR Pollution Climate Mapping data; source: Department for Environment, Food and Rural Affairs (Defra) via uk-air.defra.gov.uk, licensed under the Open Government Licence v3.0. Contains OS data © Crown copyright and database right 2026. Goldilocks Map metrics are derived products and are not official Met Office, Defra or Ordnance Survey products.

## Met Office HadUK-Grid

The stable 2016–2025 observations come from the citable CEDA HadUK-Grid v1.3.2.ceda release:

Met Office; Hollis, D.; Carlisle, E.; Kendon, M.; Packman, S.; Doherty, A. (2026): *HadUK-Grid Gridded Climate Observations on a 1km grid over the UK, v1.3.2.ceda (1836-2025).* NERC EDS Centre for Environmental Data Analysis, 23 June 2026. [doi:10.5285/789b3065d74a4c948ab05d33556c86d0](https://doi.org/10.5285/789b3065d74a4c948ab05d33556c86d0).

The current climate build also includes published 2026 provisional HadUK-Grid temperature grids from the [Met Office HadUK-Grid provisional-data service](https://www.metoffice.gov.uk/hadobs/hadukgrid/). The Met Office states that provisional grids may be amended before inclusion in a later annual release.

Method reference: Hollis, D.; McCarthy, M. P.; Kendon, M.; Legg, T.; Simpson, I. (2019): *HadUK-Grid — A new UK dataset of gridded climate observations.* Geoscience Data Journal, 6, 151–159. [doi:10.1002/gdj3.78](https://doi.org/10.1002/gdj3.78).

## Defra UK-AIR Pollution Climate Mapping

The Pollution category uses the 2022, 2023 and 2024 1 km background grids published on the [Defra UK-AIR PCM data page](https://uk-air.defra.gov.uk/data/pcm-data). Goldilocks derives three-year arithmetic means for PM2.5, NO₂, PM10 and ozone DGT120 exceedance days.

PCM values are modelled background concentrations or exceedance metrics, not measurements made at individual 1 km cell centres. Defra updates the PCM modelling methodology over time, so the annual grids should not be treated as a perfectly homogeneous observational time series.

## Ordnance Survey OS Terrain 50

The Terrain category uses the **2026-07** OS Terrain 50 Great Britain ASCII DTM grid downloaded through the OS Downloads API. OS Terrain 50 is an annual OpenData product with 50 m pixel-centre heights and is designed for broad-scale terrain analysis.

Goldilocks derives `terrain_relief` as the maximum minus minimum Terrain 50 elevation inside each canonical 1 km cell. The derived public raster is therefore a Goldilocks product, not an Ordnance Survey product. The source product covers Great Britain rather than Northern Ireland. In coastal source tiles, OS Terrain 50 models tidal-water heights as part of the supplied surface, so coastal relief can include the vertical transition between land and those modelled tidal-water heights.

The required OS OpenData acknowledgement for this release is:

> Contains OS data © Crown copyright and database right 2026.

## What Goldilocks publishes

Raw HadUK-Grid NetCDF files, Defra PCM CSV files and the OS Terrain 50 source archive are build inputs under `data/source/` and are not committed by this project. The tracked public snapshot contains only Goldilocks-derived, quantised metric rasters and the metadata needed to interpret them. Metric definitions, averaging choices, quality-control masking, quantisation, LOD construction and presentation are Goldilocks Map processing choices rather than official source-agency products.