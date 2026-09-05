# Data licence and attribution

Goldilocks Map contains derived raster metrics from multiple UK public datasets. The current build uses Met Office HadUK-Grid climate observations and Defra UK-AIR Pollution Climate Mapping (PCM) background pollution grids.

## Licence

Both source datasets used by the current build are Crown copyright data made available under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).

Suggested attribution:

> Contains Met Office HadUK-Grid data and Defra UK-AIR Pollution Climate Mapping data © Crown copyright, licensed under the Open Government Licence v3.0. Goldilocks Map metrics are derived products and are not official Met Office or Defra products.

## Met Office HadUK-Grid

The stable 2016–2025 observations come from the citable CEDA HadUK-Grid v1.3.2.ceda release:

Met Office; Hollis, D.; Carlisle, E.; Kendon, M.; Packman, S.; Doherty, A. (2026): *HadUK-Grid Gridded Climate Observations on a 1km grid over the UK, v1.3.2.ceda (1836-2025).* NERC EDS Centre for Environmental Data Analysis, 23 June 2026. [doi:10.5285/789b3065d74a4c948ab05d33556c86d0](https://doi.org/10.5285/789b3065d74a4c948ab05d33556c86d0).

The current climate build also includes published 2026 provisional HadUK-Grid temperature grids from the [Met Office HadUK-Grid provisional-data service](https://www.metoffice.gov.uk/hadobs/hadukgrid/). The Met Office states that provisional grids may be amended before inclusion in a later annual release.

Method reference: Hollis, D.; McCarthy, M. P.; Kendon, M.; Legg, T.; Simpson, I. (2019): *HadUK-Grid — A new UK dataset of gridded climate observations.* Geoscience Data Journal, 6, 151–159. [doi:10.1002/gdj3.78](https://doi.org/10.1002/gdj3.78).

## Defra UK-AIR Pollution Climate Mapping

The Pollution category uses the 2022, 2023 and 2024 1 km background grids published on the [Defra UK-AIR PCM data page](https://uk-air.defra.gov.uk/data/pcm-data). Goldilocks derives three-year arithmetic means for PM2.5, NO₂, PM10 and ozone DGT120 exceedance days.

PCM values are modelled background concentrations or exceedance metrics, not measurements made at individual 1 km cell centres. Defra updates the PCM modelling methodology over time, so the annual grids should not be treated as a perfectly homogeneous observational time series.

## What Goldilocks publishes

Raw HadUK-Grid NetCDF files and Defra PCM CSV files are build inputs under `data/source/` and are not committed by this project. The tracked public snapshot contains only Goldilocks-derived, quantised metric rasters and the metadata needed to interpret them. Metric definitions, averaging choices, quality-control masking, quantisation, LOD construction and presentation are Goldilocks Map processing choices rather than official source-agency products.