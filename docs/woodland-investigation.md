# Woodland metric investigation

## Why the NFI filter was tightened

Goldilocks originally treated the following National Forest Inventory (NFI) `IFT_IOA` classes as current woodland: `Assumed woodland`, `Broadleaved`, `Conifer`, `Coppice`, `Coppice with standards`, `Low density`, `Mixed mainly broadleaved`, `Mixed mainly conifer`, `Shrub` and `Young trees`. It already excluded `Felled`, `Ground prep`, `Failed` and `Windblow`.

A user report identified cells north of the A66 around Stainmore/Cumbria that Goldilocks showed as 100% woodland despite satellite imagery showing open moorland. Inspection of the pinned NFI GB 2024 source found a single `Assumed woodland` polygon (FID 43560) of about 3,420.9 ha spanning roughly BNG E382322–397252, N512401–519928. It completely covers ten Goldilocks 1 km cells, which explains the 100% values: the rasterisation was faithfully measuring the source polygon, but the source class was being interpreted too literally as existing tree cover.

For the house-search use case, the desired signal is substantial existing woods that are useful for shade, shelter and walking rather than planned, immature, sparse or scrub cover. The production metric therefore now uses only these six established NFI classes:

- `Broadleaved`
- `Conifer`
- `Coppice`
- `Coppice with standards`
- `Mixed mainly broadleaved`
- `Mixed mainly conifer`

The following classes are deliberately excluded:

- `Assumed woodland`
- `Young trees`
- `Low density`
- `Shrub`
- `Felled`
- `Ground prep`
- `Failed`
- `Windblow`

The metric ID remains `woodland_cover` for compatibility, but its display label and documentation describe it as **Established woodland extent**. It is still polygon extent, not literal overhead canopy density.

## Independent regional check

The filter choices were checked against the 2023 **Tree canopy and height for the North Pennines and Yorkshire Dales** dataset from the UK Centre for Ecology & Hydrology catalogue. Its 10 m tree-cover raster overlaps the A66/Stainmore problem area and provides an independent remote-sensing-based canopy signal.

Source: https://catalogue.ceh.ac.uk/documents/9e3055a3-a56b-4210-9628-4acd096ed9b7

A comparison over 1,880 overlapping Goldilocks 1 km cells gave:

| NFI interpretation | Correlation with 10 m canopy | Mean absolute error | Cells NFI >=80% but canopy <10% |
| --- | ---: | ---: | ---: |
| Original Goldilocks filter | 0.200 | 5.39 percentage points | 38 |
| Remove `Assumed woodland` only | 0.821 | 1.19 pp | 0 |
| Strict established classes + `Young trees` | 0.823 | 1.19 pp | 0 |
| Strict established classes | **0.877** | **1.11 pp** | **0** |

The ten complete 1 km cells inside the problematic Stainmore `Assumed woodland` polygon had mean canopy values of approximately 0.00% to 0.11% in the 10 m dataset. This strongly supports excluding `Assumed woodland` and also modestly favours excluding `Young trees`, `Low density` and `Shrub` for this particular location-suitability use case.

This is a regional validation rather than a GB-wide accuracy assessment. It establishes that the reported failure is real and that the strict filter performs much better in the test region; it does not turn NFI polygon extent into a true canopy-density product.

## Better canopy datasets to investigate later

The current NFI metric is useful for established woodland extent, but a separate genuine **tree canopy cover** metric would better answer the visual/shade/shelter question. Candidates identified for future work are:

### Copernicus Tree Cover Density

Conceptually the best fit: a 10 m raster representing the vertical projection of tree crowns as a 0–100% density measure. This could aggregate naturally to the Goldilocks 1 km grid. Before adoption, verify the latest product's actual UK coverage/version, download mechanism and long-term reproducibility.

Product catalogue: https://land.copernicus.eu/en/products/high-resolution-layer-tree-cover-density

### ESA WorldCover

Global 10 m Sentinel-based land-cover classification with an explicit tree-cover class and permissive CC BY 4.0 terms. Straightforward to obtain for the whole UK, but it is a classified tree-covered-land fraction rather than fractional crown density, and the commonly available baseline is older than the current NFI.

Product: https://esa-worldcover.org/

### Environment Agency National LiDAR Programme

High-resolution first-return surface and terrain data across England can be combined to derive canopy height and potentially canopy occupancy/density. This would be high quality and recent but substantially heavier to process and England-only, so equivalent sources would be needed for Wales and Scotland for a consistent GB layer.

Dataset: https://www.data.gov.uk/dataset/f0db0249-f17b-4036-9e65-309148c97ce4/national-lidar-programme

### UKCEH Land Cover Map 2025

Very recent 10 m UK satellite land-cover classification. Potentially useful as a fresh tree/woodland land-cover signal, but licensing and the right to redistribute an embedded public derived raster need to be checked carefully before use in Goldilocks.

Catalogue: https://catalogue.ceh.ac.uk/documents/9a206086-d1e7-4cd6-918a-ee19a8708f65

### North Pennines and Yorkshire Dales 2023 canopy/height

The regional 10 m dataset used for the validation above is excellent for testing the NFI metric and could potentially support a higher-quality regional layer, but it cannot be the national production source because its coverage is limited to the North Pennines/Yorkshire Dales study area.

## Recommended future direction

Keep NFI as **Established woodland extent** using the strict six-class filter. Separately investigate Copernicus Tree Cover Density first for a national **Tree canopy cover** metric. If recent UK coverage or reproducibility is unsuitable, evaluate ESA WorldCover as the simpler national fallback and national/country-specific LiDAR workflows as the higher-quality but more complex route.
