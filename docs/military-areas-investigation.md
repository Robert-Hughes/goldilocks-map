# Military-area overlay investigation

Date: 2026-09-08

## Scope

Goldilocks exposes two independently selectable military polygon overlays derived from the same pinned Geofabrik OpenStreetMap Great Britain extract used by the Services pipeline.

- **Dangerous**: polygons tagged `military=danger_area` or `military=range`.
- **Unspecified**: other polygons tagged `landuse=military` that do not carry one of those dangerous military tags.

`Unspecified` is deliberately not labelled safe. It means only that the Goldilocks classification did not find an explicit OSM `danger_area`/`range` tag on that polygon. Military land can have changing access arrangements, public rights of way, permissive routes, closures or live firing schedules that this static layer does not model.

Goldilocks does not treat generic `access=no` or `access=private` polygons as a general restricted-land overlay. Those tags occur on many unrelated private facilities and would not provide the targeted walking-screening layer requested here.

## Processing

`process_military_areas.py` reads the pinned OSM PBF through GDAL's OSM driver, retaining polygon geometry for `landuse=military` plus explicit `military=danger_area`/`military=range` areas. Boundaries are simplified with topology preservation at a tolerance of 0.00005 degrees (roughly a few metres at UK latitudes) before being converted to a compact gzip-compressed JSON payload. OSM feature references and names are retained for traceability and popup links.

The extraction is cached under the OSM source-version cache because the initial scan of the national PBF is relatively expensive. The compressed payload is decoded lazily in the browser only when either military-area checkbox is enabled.

For the pinned 2026-09-06 Great Britain extract, the processed snapshot contains **276 Dangerous polygons** and **1,339 Unspecified polygons** (1,615 total). The compact payload is 894,197 bytes as JSON and 291,659 bytes gzip-compressed. Representative Dangerous features include Salisbury Plain Training Area, Warcop Training Area, Altcar Training Camp, Bramley Training Area and the Lulworth Ranges Sea Danger Area. Individual ranges inside a larger training estate can appear as separate OSM polygons as well as the wider estate boundary where OSM maps both.

## Browser presentation

The control is presented on one line where space permits:

`Military areas:  [ ] Dangerous  [ ] Unspecified`

Dangerous areas use a stronger warm dashed outline/fill; unspecified military land uses a lighter muted dashed style. Both have low fill opacity so that the active Goldilocks raster remains visible underneath. Clicking a polygon opens its name/classification and an exact OpenStreetMap feature link.

## Limitation

This is an OSM-derived screening aid, not an authoritative MOD access map and not a live safety service. It must not be interpreted as confirming whether a route is legally open or whether a firing range is active at a particular time. For an actual walk, current signage and the relevant landowner/MOD access or firing information remain authoritative.
