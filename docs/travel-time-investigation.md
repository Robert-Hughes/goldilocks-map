# Travel-time metric investigation

## Purpose and decision

This document preserves the investigation that led to the first Goldilocks driving-time metrics. The original experiments were carried out in a temporary workspace and deliberately explored several architectures and speed models before any production code was added to the map.

The practical question is not turn-by-turn navigation. It is a location-suitability question: for each canonical 1 km Goldilocks cell, approximately how long would a normal car journey to family in York or Cambridge take under representative traffic conditions?

The implemented v1 design is deliberately fixed to the two family destinations rather than allowing arbitrary destinations in the browser. That choice lets the expensive routing calculation happen offline, permits a richer historic speed model, and leaves the browser with four ordinary compressed raster metrics:

- `travel_york_weekend` — representative weekend drive to York;
- `travel_york_peak` — representative weekday PM-peak drive to York;
- `travel_cambridge_weekend` — representative weekend drive to Cambridge;
- `travel_cambridge_peak` — representative weekday PM-peak drive to Cambridge.

The model uses only freely accessible open-data inputs with straightforward reuse terms. It is intentionally a suitability-screening model, not a navigation engine and not a live-traffic service.

## Final model in one diagram

```text
OS Open Roads 2026-04
  generalised GB topology
          |
          +---------------- England SRN ----------------+
          |                                             |
          |                                National Highways TTRT
          |                                  historic link speeds
          |                                  weekend / PM peak
          |                                             |
          +--------------- local A roads ---------------+
          |                                             |
          |                           DfT observed speed by
          |                           (LAD, road number), where
          |                           published; otherwise DfT
          |                           country urban/rural averages
          |                           + OS Open Built Up Areas
          |                                             |
          +------------ B/minor/local roads ------------+
          |                                             |
          |                           simple transparent function/
          |                           form-of-way fallback speeds
          |                                             |
          +----------------------+----------------------+
                                 |
                         weighted road graph
                                 |
               reverse Dijkstra from York/Cambridge
                                 |
                  nearest graph node for each covered
                       canonical 1 km cell centre
                                 |
                      four Goldilocks raster metrics
```

For weekend SRN costs, Goldilocks averages the per-link traversal times implied by National Highways `Normal Saturday` and `Normal Sunday` observations. For weekday peak, it uses National Highways `PM Peak` observations. DfT's road-level local-A-road table is annual rather than daypart-specific; for the peak metric its observed speed is adjusted by the latest country-level rolling weekday-evening-peak/all-day speed ratio. Unmatched local A roads use the corresponding DfT urban/rural observations. Other road classes retain the simple fallback model because attempts to make those classes more elaborate reduced validation quality.

## Data sources and reproducibility

Exact source versions, file URLs and checksums are pinned in [`travel-time-sources.json`](../travel-time-sources.json). Raw downloads are deliberately git-ignored. `download_travel_sources.py` recreates the source cache and writes a local discovery manifest under `data/source/travel-time/`.

The production inputs are:

### OS Open Roads

- Provider: Ordnance Survey.
- Product: OS Open Roads.
- Pinned release: **2026-04**.
- Format used: GB ESRI Shapefile archive.
- Product page: <https://www.ordnancesurvey.co.uk/products/os-open-roads>
- Downloads API: <https://api.os.uk/downloads/v1/products/OpenRoads/downloads>
- Pinned file: `oproad_essh_gb.zip`.
- Pinned MD5: `8c6e66a255f79e3e31845307cefee298`.

Open Roads supplies the GB link/node topology, road number, function, form-of-way and trunk-road flag. It is intentionally a generalised road-network product. It is not a navigation-grade representation of every one-way restriction, prohibited turn, lane rule or ferry connection.

### OS Open Built Up Areas

- Provider: Ordnance Survey.
- Product: OS Open Built Up Areas.
- Pinned release: **2026-04**.
- Format used: GB GeoPackage.
- Product page: <https://www.ordnancesurvey.co.uk/products/os-open-built-up-areas>
- Downloads API: <https://api.os.uk/downloads/v1/products/BuiltUpAreas/downloads>
- Pinned file: `OS_Open_Built_Up_Areas_GeoPackage.zip`.
- Pinned MD5: `d3620702aa841625b3b557c676e295a5`.

The aggregate `os_open_built_up_areas` layer is used only to distinguish settlement context for local A roads lacking a road-level DfT observation.

### DfT local A-road speed statistics

- Provider: Department for Transport.
- Dataset family: road congestion and travel-time statistics, CGN tables.
- Dataset page: <https://www.gov.uk/government/statistical-data-sets/average-speed-delay-and-reliability-of-travel-times-cgn>
- Files used at the time of implementation:
  - England `CGN0503`: <https://assets.publishing.service.gov.uk/media/6a5df5359e2827ee5f6d0892/cgn0503.ods>
  - Scotland `CGN0507`: <https://assets.publishing.service.gov.uk/media/6a72ecbd8a340ed57ba476fc/cgn0507.ods>
  - Wales `CGN0509`: <https://assets.publishing.service.gov.uk/media/6a72ecd1011ff6453dece522/cgn0509.ods>

The individual-road tables publish flow-weighted average vehicle speeds for locally managed A roads by local authority and road name. The 2026-08-06 update used in the experiment has calendar-year **2025** road-level values. The same workbooks also provide rolling national context through **March 2026**, including urban, rural and weekday time-period averages.

The production parser reads the ODS XML directly, avoiding a build dependency on pandas/odfpy.

### ONS Local Authority District boundaries

- Provider: Office for National Statistics.
- Dataset: Local Authority Districts, December 2025, UK BGC.
- ONS Open Geography Portal: <https://geoportal.statistics.gov.uk/>
- Feature service used by the reproducible downloader: <https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/Local_Authority_Districts_DEC_2025_Boundaries_UK_BGC/FeatureServer/0/query>

These boundaries are a processing helper. Each non-trunk A-road link midpoint is assigned a `LAD25CD`, which permits the Open Roads road number to be joined to the DfT `(ONS Area Code, Road Name)` observation.

### National Highways Travel Time Reporting Tool

- Provider: National Highways.
- Reporting product: Travel Time Reporting Tool (TTRT).
- Product page: <https://nationalhighways.co.uk/roads-and-travel/road-projects/travel-time-reporting-tool/>
- ArcGIS feature service used by the downloader: <https://services-eu1.arcgis.com/mZXeBXkkZpekxjXT/arcgis/rest/services/TTRT_V_2_5_6/FeatureServer/0/query>
- Pinned reporting period: **Apr 2024 - Mar 2025**.
- Production aggregations: `Normal Saturday`, `Normal Sunday`, `PM Peak`.
- `Annual` is also pinned/downloadable because it was important in the validation experiments and remains useful for reproduction.

The National Highways geometry is matched offline to Open Roads trunk links by road number and geometry. This supplies observed historic traversal speeds on a large part of England's Strategic Road Network, where errors in assumed free-flow speed would otherwise dominate long journeys.

## Licensing approach

The production design was intentionally restricted to sources that can be used as open data without API credentials in the generated application. The source-specific acknowledgements are recorded in `DATA-LICENCE.md` and in the generated metric manifest. Goldilocks publishes only its derived, quantised 1 km journey-time fields; raw road networks, source speed tables and source geometries remain under `data/source/` and are not committed or deployed.

The travel-time raster is a Goldilocks model and must not be presented as an Ordnance Survey, DfT, National Highways, ONS or Google product.

## Architecture alternatives considered

### 1. Fully client-side arbitrary destinations

The initial idea was to let the user add any saved location and calculate an isochrone or full travel-time field in the browser. Browser-WASM Valhalla wrappers such as `routewasm` make that technically possible, but a multi-hour GB isochrone can cause large routing-tile transfers. The more attractive browser-only experiment was therefore to build a Goldilocks-specific compact graph from OS Open Roads.

A full 2026-04 Open Roads load produced approximately:

- **3.35 million unique road nodes**;
- **3.94 million usable links**.

Open Roads is already highly generalised. Degree-2 contraction only reduced the graph by about five percent, so aggressive topology contraction was not the main storage win. Compact integer serialization was much more effective.

The browser-graph experiment reached approximately:

| Representation | Size |
| --- | ---: |
| Fixed CSR + BNG node coordinates | 98.3 MB raw / 36.6 MB gzip |
| Fixed CSR topology only | 72.9 MB raw / 26.5 MB gzip |
| Directed varint transport | 24.2 MB raw / 18.9 MB gzip |
| **Undirected varint transport** | **13.0 MB raw / 9.63 MB gzip** |

The final compact transport stored each undirected link once and reconstructed a normal bidirectional CSR graph in JavaScript. In a Node/browser-like benchmark:

- gzip decompression: about 0.13 s;
- graph decoding/CSR reconstruction: about 0.2 s additional;
- expanded typed-array routing graph: about **55 MiB**;
- graph plus Dijkstra workspace: about **85.5 MiB**;
- four-hour Dijkstra from Cambridge: about **1.0 s**;
- four-hour Dijkstra from York: about **1.3 s**.

This proved that arbitrary client-side locations are feasible. It was not selected for v1 because it would add roughly 10 MB of routing data, tens of megabytes of runtime memory and new browser complexity while still relying on a comparatively crude all-road speed model.

### 2. Fixed destinations, precomputed offline

For York and Cambridge the routing graph is needed only at build time. One reverse shortest-path search from a destination calculates travel time to every reachable road node; the result can then be sampled onto the canonical 1 km grid and encoded like any other Goldilocks metric.

This moves nearly all complexity out of the application:

- no routing network is shipped to the browser;
- no client-side graph memory or CPU is required;
- richer offline source matching is practical;
- a regenerated travel model results in only four small compressed rasters;
- normal Goldilocks colour-range, LOD, click-value and persistence code works unchanged.

The remainder of the investigation therefore concentrated on making edge traversal costs realistic enough for location screening.

## Experiment sequence

### Experiment A — simple Open Roads speeds

The first offline model assigned representative speeds by Open Roads `function`/`formOfWay`, then ran reverse Dijkstra. The base fallback table eventually retained by the production model is deliberately simple:

| Open Roads function | Fallback speed |
| --- | ---: |
| Motorway | 60 mph |
| A Road | 44 mph |
| B Road | 35 mph |
| Minor Road | 30 mph |
| Local Road | 25 mph |
| Restricted Local Access Road | 16 mph |
| Secondary Access Road | 20 mph |
| Local Access Road | 16 mph |

Non-motorway dual carriageways receive a small +5 mph adjustment; roundabouts are capped at 20 mph and slip roads at 35 mph. These values are fallbacks, not observations.

Against the first 20 Google Maps reference journeys, the simple model had a time MAE of about **15.6 minutes** and a positive bias of about **7.1 minutes**. Route-distance error was already modest enough to suggest that topology was not the dominant problem.

### Experiment B — National Highways observed SRN speeds

National Highways historic link speeds were spatially matched to Open Roads links marked as trunk roads. The matching approach used road number plus the Open Roads link midpoint, looking first within 80 m and accepting matched geometry within 120 m. When several near-coincident observed geometries were plausible, their speeds were averaged within a narrow nearest-distance tolerance.

For the current source pairing the match covers:

- **19,524 Open Roads trunk links**;
- about **6,146.6 km** of matched link length;
- median geometry match distance about **9.75 m**;
- 95th percentile match distance about **78.9 m**.

On the original 20-route comparison, replacing estimated SRN costs with National Highways annual observed costs reduced time MAE from approximately **15.6 to 9.7 minutes**, while route-distance error remained around four percent. Cambridge journeys, which spend a large fraction of their length on the English strategic network, improved particularly strongly.

This confirmed the central hypothesis of the investigation: realistic edge traversal time mattered more than replacing Dijkstra with a more sophisticated routing algorithm.

### Experiment C — built-up/rural context for local A roads

The next experiment overlaid OS Open Built Up Areas and used DfT's observed urban/rural local-A-road averages instead of one generic A-road speed. Each A-road link was sampled at its start, midpoint and end, and the implied traversal time was split according to the fraction of those samples in a built-up area.

On the original 20 routes:

| Model | Time MAE | Bias | Within 15 min | Route-distance error |
| --- | ---: | ---: | ---: | ---: |
| NH annual + simple local roads | 9.72 min | -4.68 min | 17/20 | 4.25% |
| **NH annual + built-up/rural A roads** | **6.48 min** | **+3.42 min** | **19/20** | **0.99%** |
| Context-sensitive A/B/minor/local | 8.66 min | +8.56 min | 16/20 | 1.39% |

York improved especially strongly: its MAE fell from **13.75 to 3.06 minutes**. Some apparent routing errors also disappeared because realistic A-road costs stopped Dijkstra selecting implausibly attractive minor alternatives.

Extending guessed urban/rural adjustments to B/minor/local roads made longer journeys worse. This negative result is why the production model deliberately avoids manufacturing detailed speed behaviour for road classes where we lack direct evidence.

### Experiment D — DfT road-by-local-authority observations

The 2025 DfT workbooks contain a more granular table than the national urban/rural summary: average speed by **local authority and road name**. Joining ONS LAD boundaries to Open Roads made it possible to apply those observations directly.

Coverage in the experiment was:

- 261,433 non-trunk Open Roads A-road links;
- 261,172 (99.9%) assigned to an LAD;
- **176,530 links (about 67.5%)** matched an exact DfT `(LAD code, road number)` observation.

Several formulations were tested: pure urban/rural, 25/50/75% blends between the road-level and contextual costs, direct road-level speed, and a relative-to-local-authority multiplier. On the first 20 references direct road-level speed was already competitive, but the sample had become too small and too repeatedly inspected to choose a coefficient responsibly. This motivated a new validation set.

### Experiment E — independent 128-journey validation set

A fresh set of **64 origins** was assembled, with deliberate concentration in northern England and the southern end of Scotland. Each origin was compared to both York and Cambridge, producing **128 routes**. The exact geocoded input used for the 2026-09-06 numbers is preserved as `experiments/travel-time/validation-origins-2026-09-06.csv`; `validation-origins.csv` is a reviewed version for future runs with three obvious town-centre geocodes corrected (Keswick, Durham and Windermere). Those corrections were made after the historical aggregate results below were calculated, so the original file remains the numerical reproduction input.

Regional groups included:

- Cumbria;
- Pennines / County Durham;
- North Yorkshire;
- North York Moors / Yorkshire coast;
- Northumberland / Tyne;
- Lancashire / western approaches;
- Scottish Borders;
- Dumfries and Galloway / southern Lanarkshire.

Google Maps references were collected directly from normal directions pages on **Sunday 6 September 2026**, approximately 13:20–13:27 BST. Google was used only as an external comparison reference. No Google-derived data is part of the production build or published bundle.

Against this untouched 128-route set, using National Highways **Annual** SRN speeds:

| Local-A model | MAE | Bias | 90th pct absolute error | Within 15 min | Route-distance error |
| --- | ---: | ---: | ---: | ---: | ---: |
| Country urban/rural only | 8.70 min | +3.45 min | 17.4 min | 111/128 | 2.80% |
| Road+LAD 25% blend | 8.38 | +3.55 | 17.8 | 112/128 | 2.34% |
| Road+LAD 50% blend | 7.97 | +3.39 | 17.8 | 112/128 | 2.23% |
| Road+LAD 75% blend | 7.41 | +2.79 | 16.7 | 114/128 | 2.15% |
| **Road+LAD direct** | **7.04** | **+1.68** | **14.5** | **120/128** | **1.80%** |
| Relative multiplier, clamped | 7.55 | +1.89 | 14.8 | 116/128 | 2.38% |

The direct formulation was both simpler and better. A clustered bootstrap by origin favoured direct road-level costs over the 75% blend by roughly 0.37 minutes of MAE, with the 95% interval only just excluding zero; the improvement over country urban/rural alone was much clearer, around 1.66 minutes.

The route-distance comparison was also important. Under the direct model:

- **119/128** route distances were within 5% of Google;
- **125/128** were within 10%;
- mean absolute route-distance discrepancy was about **1.8%**.

This is strong evidence that the generalised Open Roads topology is adequate for the intended 1 km suitability metric. It does not prove equivalence to a navigation-grade graph, but it makes a much heavier OSM/Valhalla stack hard to justify for v1.

### Experiment F — traffic-regime sensitivity

The larger validation was captured on a Sunday, which made it possible to test whether traffic regime, rather than topology, explained the remaining long-journey bias. Keeping the winning direct local-A model fixed, three National Highways aggregations were compared:

| National Highways regime | All-route MAE | York MAE | Cambridge MAE | Within 15 min |
| --- | ---: | ---: | ---: | ---: |
| Annual | 7.04 min | 5.33 | 8.75 | 120/128 |
| Interpeak | 7.74 | 5.21 | 10.28 | 112/128 |
| **Normal Sunday** | **5.60** | 6.44 | **4.77** | **125/128** |

The Google pages were subsequently classified from their explanatory text. Many routes were affected by live slowdowns or closures. After refinement, approximately 51 routes explicitly described the usual traffic, about 75 contained current-traffic/closure behaviour, and only two were not classified. Examples included A59 road closures and a 16–17 minute M6 slowdown around Lancaster/Garstang.

For the 51 routes explicitly described by Google as usual traffic, the Normal Sunday model had an MAE of roughly **3.82 minutes**, versus about **7.63 minutes** for the Annual SRN model. A clustered bootstrap by origin estimated a Sunday-versus-Annual improvement of about **3.8 minutes**, with a 95% interval roughly 2.7–4.9 minutes.

The Scotland result was particularly revealing. Under Annual SRN speeds, southern-Scotland-to-Cambridge trips were systematically slow even when route distance almost exactly matched Google. Switching only the English SRN costs to Normal Sunday largely removed that error. This showed that a long journey from Scotland can be dominated by the traffic regime on the English strategic network, not by the Scottish graph segment.

This experiment is the main reason v1 exposes distinct weekend and peak metrics rather than one ambiguous “travel time”.

### Experiment G — local-authority-wide fallback sensitivity

The final experiment tested one remaining possible complication: if an A road lacks a road-specific DfT observation, should Goldilocks use the local-authority-wide DfT average before falling back to the country urban/rural model?

Only about 3,382 additional links were eligible for that extra fallback. On all 128 validation journeys the result was **numerically unchanged** from the simpler country-context fallback, for both the Annual and Normal Sunday SRN variants:

| Variant | MAE | Bias | Within 15 min | Route-distance error |
| --- | ---: | ---: | ---: | ---: |
| Direct road speed + country fallback + NH Annual | 7.04 min | +1.68 | 120/128 | 1.80% |
| Direct road speed + LA-wide fallback + NH Annual | **7.04 min** | **+1.68** | **120/128** | **1.80%** |
| Direct road speed + country fallback + NH Normal Sunday | 5.60 min | -3.69 | 125/128 | 2.38% |
| Direct road speed + LA-wide fallback + NH Normal Sunday | **5.60 min** | **-3.69** | **125/128** | **2.38%** |

The final production model therefore omits this ineffective hierarchy level. This is an example of deliberately preferring a simpler transparent method when extra complexity does not improve observed behaviour.

## Production speed model

### England Strategic Road Network

For Open Roads links marked as trunk and successfully matched to National Highways geometry:

- weekend: mean **link traversal time** from `Normal Saturday` and `Normal Sunday`;
- peak: `PM Peak` observed speed.

Averaging traversal seconds rather than speeds avoids a subtle harmonic/arithmetic-speed error when combining two day types.

Where a trunk link cannot be matched to the National Highways product, the Open Roads function/form fallback remains in force. National Highways covers England, so Scottish and Welsh trunk roads necessarily use fallbacks in v1.

### Local A roads

For each non-trunk A-road link:

1. assign its midpoint to the December 2025 ONS LAD;
2. normalize its Open Roads road number;
3. if DfT publishes an observation for that `(LAD code, road number)`, use the observed 2025 speed directly;
4. otherwise derive a traversal cost from the latest rolling country urban/rural A-road speeds and the OS Built Up Areas samples.

For peak local-A costs, the annual/rolling baseline is multiplied by the latest country-level weekday-evening-peak/all-day ratio. In the March 2026 rolling context used by the current build those ratios are approximately:

- England: 21.8 / 24.3 = **0.897**;
- Scotland: 24.7 / 26.5 = **0.932**;
- Wales: 24.9 / 26.7 = **0.933**.

This is intentionally conservative in complexity: the published road-level table does not contain per-road peak profiles, so the model does not pretend that it does.

### B, minor and local roads

These retain the simple function/form fallback table. The contextual all-class experiment showed that adding guessed urban/rural differentiation here could improve a few short York journeys but made longer Cambridge journeys materially too slow. The model therefore limits empirical sophistication to road classes for which useful observation data exists.

## Grid sampling and source coverage

Goldilocks does not calculate 245,000 independent routes. For each scenario and destination it performs one reverse Dijkstra over the road graph, producing a shortest travel time to every reachable node. Each canonical 1 km cell centre is then mapped to its nearest Open Roads graph node and inherits that node's travel time.

No extra driveway/access penalty is currently added between the cell centre and the snapped road node. The grid is already a coarse 1 km screening surface, and the validation points were likewise snapped to the road graph.

OS Open Roads covers **Great Britain rather than Northern Ireland**. A naive national nearest-node operation would incorrectly snap Northern Ireland across the Irish Sea. The production processor therefore has an explicit source-coverage guard: a canonical cell is valid for Travel only if its centre is within **20 km** of an Open Roads graph node. In the current grid there is a clean empirical gap: GB road-covered cells are within 10 km, while cells outside the GB source network begin beyond 20 km. The threshold gives **230,166** source-covered canonical cells, matching the GB coverage count independently obtained from OS Terrain 50. Cells outside the source network are nodata.

Some GB islands can also be disconnected from the mainland graph because Open Roads is not a ferry-routing dataset. A cell whose snapped component cannot reach York/Cambridge is left nodata rather than assigned an invented ferry traversal.

## Interpretation of the four v1 metrics

### Weekend

“Weekend” is a representative historic weekend rather than a live or exact-clock-time forecast. On matched English SRN links it uses the mean of Normal Saturday and Normal Sunday traversal times. Local A roads use the best available DfT road-level annual observation or country urban/rural fallback because equivalent per-road weekend data is not published in the chosen open source. Other roads use the basic fallback model.

The choice to combine Saturday and Sunday is a design decision for the house-search use case. The validation specifically established that the Normal Sunday regime closely matched a Sunday usual-traffic snapshot; it did **not** independently validate the exact Saturday/Sunday mean against a Saturday reference set.

### Weekday PM peak

“Weekday PM peak” is intended as a stress-case companion to weekend accessibility. Matched English SRN links use the TTRT `PM Peak` aggregation. Local A roads use the DfT evening-peak/all-day country ratio applied to their baseline speed. The model is therefore more directly peak-aware on the SRN than on ordinary local roads.

The PM-peak layer has not been benchmarked against an independent peak-time Google snapshot in this investigation. Its justification is primarily that it uses source-defined historic PM-peak observations where available and a transparent published DfT ratio elsewhere.

### Post-implementation weekend-raster check

After generating the actual published `uint16` rasters, the weekend layers were sampled at the exact 64-origin historical validation coordinates and compared with the same 128-route Sunday Google snapshot. This checks the complete production path, including Saturday/Sunday averaging, 1 km cell sampling, quantisation and source-coverage masking rather than only the graph-node experiment.

- all 128 routes: **5.49 min MAE**, **125/128 within 15 minutes**;
- the 51 routes Google explicitly labelled as usual traffic: **3.84 min MAE**, **51/51 within 15 minutes**.

The largest remaining errors were the same current-traffic/incident cases already identified in the earlier experiment (for example Garstang and Lancaster). The production weekend raster therefore preserves essentially the same validation quality as the Normal-Sunday graph experiment despite using the more defensible Saturday/Sunday average. This remains a Sunday reference set, not an independent Saturday validation.

## What the model does not claim

The travel layers should be interpreted as comparative suitability metrics, not precise promises. In particular:

- they do not include live incidents, temporary roadworks, closures, weather disruption or event traffic;
- they do not provide turn-by-turn directions;
- Open Roads omits some navigation restrictions and does not model ferries as a complete driving network;
- the model treats links as bidirectional because Open Roads does not provide all direction restrictions required by a navigation engine;
- the National Highways observed overlay covers England's SRN, not every GB trunk road;
- DfT road-level local-A observations are averages across a named road inside one local authority, not individual-link speed traces;
- weekend specificity is strongest on the English SRN; local A-road observations are annual/all-day baselines;
- peak specificity on local A roads is a country-level adjustment rather than a per-road peak profile;
- B/minor/local speeds are transparent assumptions;
- the 1 km grid inherits the nearest road-node time without an access-leg penalty;
- Northern Ireland is nodata because the chosen routing topology is GB-only.

These limitations are acceptable for the intended question — whether a candidate area is broadly one, two, three or four hours from family — but they would not be acceptable for a navigation application.

## Google comparison caveat

Google Maps was used only as an external plausibility check for route distances and journey times. The historical comparison was affected by live traffic and road closures on many routes, which is a mismatch with Goldilocks' historic-typical model. Raw Google page captures/results are not committed. The public helper under `experiments/travel-time/` deliberately generates directions URLs and an empty local worksheet only; it does not automate Google Maps or extract/store Google Maps content.

No Google Maps route, speed, geometry or journey-time data is an input to `process_travel_metrics.py`, `download_travel_sources.py`, the published metric blobs, or the Goldilocks application.

## Reproducing the production metrics

From the repository root, after the normal Python environment is installed:

```sh
python download_travel_sources.py
python process_travel_metrics.py
python assemble_metrics.py
python publish_metrics_snapshot.py
python build.py
```

`download_travel_sources.py --dry-run` checks the pinned OS source release metadata and prints the intended DfT/ONS downloads without fetching the large archives. The exact versions/checksums are in `travel-time-sources.json`; source archives and intermediate caches remain under ignored `data/source/travel-time/`.

The travel processor caches expensive source-to-graph classifications and canonical-cell graph-node snaps under the ignored source cache. These are reproducible accelerators, not published artifacts.

## Preserved validation harness

`experiments/travel-time/` contains the parts of the experiment that remain useful after the temporary workspace is deleted:

- `validation-origins-2026-09-06.csv` — the exact 64-origin input used for the historical 128-route results;
- `validation-origins.csv` — the reviewed version for future runs, retaining the same places/regions with obvious town-centre geocodes corrected;
- `collect-google-reference.py` — prepares a local CSV of ordinary Google Maps direction URLs and blank manual-QA fields without fetching or extracting Google Maps content;
- `analyse-validation.py` — small comparison/report helper for model-route and Google-reference CSVs;
- `README.md` — usage notes and the warning that Google is an external validation reference only.

The production routing/model code itself is no longer an experiment: it lives in `travel_time_routing.py` and `process_travel_metrics.py`.

## Why this design was chosen

The investigation supports a fairly narrow conclusion rather than a claim that this is the universally best routing architecture:

1. A compact arbitrary-destination browser graph is feasible, but costs about 10 MB compressed plus substantial browser RAM and added application complexity.
2. For two fixed destinations, offline reverse searches are simpler and allow better source matching while producing very small browser rasters.
3. The largest accuracy gains came from better traversal-time evidence, first on the English SRN and then on local A roads, not from changing shortest-path algorithms.
4. OS Open Roads route geometry is adequate at the 1 km screening scale: on the larger validation, most route distances were close to Google.
5. Direct DfT road-by-LAD observations generalized better than arbitrary blend coefficients.
6. Making B/minor/local roads more elaborate without direct evidence was counterproductive.
7. The traffic regime materially changes long-distance results, so separate weekend and peak layers are more honest than one undifferentiated travel-time metric.
8. Adding a local-authority-wide fallback after road-level matching produced no measurable validation benefit and was omitted.

The resulting v1 model is intentionally transparent: observed historic data is used where it has the greatest leverage; simple fallbacks remain where the open evidence is weaker; the offline result is reduced to four ordinary 1 km metrics that fit the existing Goldilocks architecture.
