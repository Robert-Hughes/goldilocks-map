# Travel-time validation harness

These files preserve the reusable part of the 2026-09-06 travel-time investigation. They are **not** part of the normal Goldilocks build.

The production model is in `../../travel_time_routing.py` and `../../process_travel_metrics.py`. The detailed experiment history and conclusions are in `../../docs/travel-time-investigation.md`.

## Origin sets

- `validation-origins-2026-09-06.csv` is the exact geocoded input used for the 128-route comparison described in the investigation document. It is retained for numerical reproducibility. A few Nominatim results were subsequently found to be imperfect town-centre choices (notably Keswick, Durham and Windermere).
- `validation-origins.csv` is the reviewed origin list for future runs. Those obvious settlement-centre geocodes have been corrected while the same 64 place names and regional groups are retained.

Each origin is paired with York and Cambridge, producing 128 comparison journeys.

The origin coordinates/display names were obtained using Nominatim/OpenStreetMap where noted. © OpenStreetMap contributors; data available under the Open Database Licence (ODbL): https://www.openstreetmap.org/copyright. These validation points are not used by the production Goldilocks build.

## Preparing a local Google comparison worksheet

`collect-google-reference.py` now only generates a CSV containing ordinary Google Maps directions URLs and blank fields for a manual comparison. It does **not** fetch Google Maps, automate a browser or extract/store Google Maps content.

```sh
python experiments/travel-time/collect-google-reference.py \
  --output /tmp/goldilocks-google-reference.csv
```

Open any URLs needed for local QA manually. Keep populated Google comparison results local and uncommitted, and comply with the applicable Google Maps terms. Google route/time data is an external validation reference only and must not be fed into or redistributed with the Goldilocks travel model.

Google directions are time-sensitive. Record the capture date/time and note whether a route appears affected by live traffic or closures before treating a mismatch as model error; the historical 2026-09-06 comparison included live A59 closures and M6 slowdowns that a historic-typical Goldilocks raster is not intended to reproduce.

## Analysing comparison CSVs

If a model experiment emits a CSV containing at least:

```text
variant,origin,region,destination,minutes,distance_miles
```

and you have locally filled the comparison worksheet, compare them with:

```sh
python experiments/travel-time/analyse-validation.py \
  /tmp/model-routes.csv \
  /tmp/goldilocks-google-reference.csv
```

The report gives MAE, bias, tail errors, within-10/15-minute counts, route-distance error, destination splits, traffic-label splits and regional splits.

## Historical aggregate results

The important aggregate values from the original temporary experiment are recorded in `../../docs/travel-time-investigation.md`. Raw Google page captures and downloaded road/speed datasets are intentionally not committed.
