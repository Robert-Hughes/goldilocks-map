# Goldilocks Map

Goldilocks Map is an experimental single-file UK location-suitability explorer. This first proof of concept renders one derived Met Office HadUK-Grid climate metric over a 10 km x 10 km area around York.

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
4. select the nearest 10 x 10 HadUK 1 km cell centres;
5. derive `count(tasmax > 25°C)` for each cell;
6. install the minimal npm build dependencies if `node_modules/.bin/esbuild` is absent;
7. bundle/minify `src/app.ts` with esbuild;
8. inject the derived JSON and bundled JavaScript into the HTML template;
9. write the single-file application to `dist/goldilocks.html`.

Use `python build.py --refresh-data` to force a fresh NetCDF download.

The generated HTML embeds the derived climate data and application JavaScript. It still uses the internet at viewing time for Leaflet and CARTO's OpenStreetMap-backed basemap tiles; OpenStreetMap and CARTO attribution are shown on the map.

## Project layout

- `build.py` — download, validation, preprocessing, TypeScript bundling and HTML generation
- `templates/goldilocks.html` — single-page HTML shell
- `src/app.ts` — Leaflet frontend
- `data/` — cached raw input data (git-ignored)
- `dist/goldilocks.html` — generated artifact (git-ignored)

## Data provenance

The source data are © Crown copyright, Met Office HadUK-Grid. This prototype uses provisional July 2026 daily maximum-temperature data at 1 km resolution. The derived metric is the count of daily `tasmax` values strictly above 25°C during 1–31 July 2026.

HadUK-Grid reference: Hollis, D., McCarthy, M. P., Kendon, M., Legg, T. & Simpson, I. (2019), “HadUK-Grid — A new UK dataset of gridded climate observations”, *Geoscience Data Journal*, 6, 151–159, https://doi.org/10.1002/gdj3.78.
