# NHS services investigation

Date: 2026-09-08

## Scope agreed for Goldilocks

The first implementation treats **GPs** and **NHS dentists** as simple presence/absence service layers. Acceptance status was investigated, but is deliberately deferred because the open reference distributions used here do not contain it and the suitable NHS API requires separately approved/onboarded access. Goldilocks does not scrape NHS HTML pages and does not expose acceptance controls that cannot currently be populated.

For hospitals, the first implementation intentionally includes only broad general/community hospital classes. ERIC `General acute hospital` and `Mixed service hospital` sites are grouped as **General hospitals**; ERIC `Community hospital (with inpatient beds)` sites are **Community hospitals**. Specialist acute hospitals, mental-health sites, learning-disability sites, other inpatient sites, non-inpatient sites, support facilities and unoccupied sites are excluded.

## Primary-care organisation sources

### England and Wales: NHS England Organisation Data Service

Goldilocks uses the current Organisation Data Service Data Search and Export predefined reports rather than scraping nhs.uk pages. The GP source is `epraccur` (General Medical Practices), with active `RO76` GP Practice records retained. The dental source is `egdpprac` (General Dental Practices), with active records retained. Both reports provide organisation identifiers, names, addresses/postcodes and status; they do not themselves provide a reliable current accepting-new-patients field.

The reports are dynamic rather than release-pinned, so `download_service_sources.py` refreshes them when regenerating the service bundle and records their byte size and SHA-256 in the local discovery metadata.

### Scotland: Public Health Scotland

The July 2026 **GP Practice Contact Details and List Sizes** distribution supplies Scottish GP practices. The March 2026 **Dental Practices and Patient Registrations** distribution supplies Scottish NHS dental practices. These distributions provide practice identity/address information but no centrally usable current new-patient acceptance field for this project.

### Acceptance status

No HTML pages are scraped. The investigated machine-readable route containing acceptance fields is the NHS England **Directory of Healthcare Services (Service Search) API v3**, which is listed as in production and exposes GP/dentist organisation information suitable for joining to ODS identifiers.

NHS API use requires separate application onboarding/approval and connection terms. Goldilocks does not currently have that approved access, so acceptance status is **not implemented in the data model or UI**. GPs and NHS dentists are treated equally as presence/absence locations. If approved machine-readable access is obtained later, acceptance can be reconsidered as a separate enhancement rather than carrying an unusable three-state enum now.

The newer Directory of Services Search API was also reviewed. It is restricted-access and, at the time of this investigation, its published HealthcareService endpoint is not yet available in Production, so it is not used by this implementation.

## Coordinates

The NHS/PHS/ERIC source records are postcode-addressed rather than consistently supplied with WGS84 coordinates. The NHS `pcodeall` distribution was checked but does not provide geographic coordinates suitable for this purpose. Goldilocks therefore uses **OS Code-Point Open 2026-08** as the offline postcode-to-coordinate source, with its published GB CSV download size/MD5 pinned by the downloader. Easting/northing positions are converted from EPSG:27700 to WGS84 with `pyproj`.

A postcode centroid is an appropriate screening-map location, but it is not a claim that the marker identifies the exact building entrance. Records whose postcodes cannot be resolved in the pinned Code-Point release are omitted and counted in the service manifest.

## Hospitals

### England

The England hospital source is NHS England **Estates Returns Information Collection (ERIC) 2024/25 site data**, an official site-level CSV for NHS secondary-care estate. Exact observed site-type counts in the source were:

- General acute hospital: 219
- Mixed service hospital: 47
- Community hospital (with inpatient beds): 230

Goldilocks maps `General acute hospital` and `Mixed service hospital` to **General hospitals**, and `Community hospital (with inpatient beds)` to **Community hospitals**. The exact filtering correctly identifies the motivating examples as General hospitals: York Hospital (`RCB55`), Addenbrooke's Hospital (`RGT01`) and Lister Hospital (`RWH01`). Other ERIC records at the same hospital postcode belonging to mental-health trusts are excluded because their ERIC site type is not one of the accepted types.

### Scotland

Public Health Scotland's monthly **Current NHS Hospitals in Scotland** reference file provides the current open-hospital identity, name and postcode. Because that file intentionally includes all NHS hospitals, Goldilocks joins it by hospital code to the PHS **Scottish Health Service Costs 2024/25 Hospital Profile**. The accompanying **Costs hospital classification 2024-2025** workbook defines the functional group codes used in that profile.

Goldilocks maps Costs Book groups `A1` (major teaching hospitals), `A2` (large general hospitals) and `A3` (general/mixed specialist hospitals) to **General hospitals**, and `J26` to **Community hospitals**. Children's, long-stay, psychiatric, learning-disability, maternity, dental, miscellaneous and clinic groups are excluded. The 2024/25 profile contains 29 selected `A1`/`A2`/`A3` hospitals and 51 `J26` hospitals, and all 80 join to the September 2026 current-hospital list. Examples include Glasgow Royal Infirmary, Queen Elizabeth University Hospital, Aberdeen Royal Infirmary, Royal Infirmary of Edinburgh, Raigmore Hospital, Belford Hospital and Borders General Hospital in the General category, plus Hawick, St Andrews, Falkirk and numerous rural Community hospitals in the Community category.

Wales still lacks an equivalent implemented hospital classification in the current Goldilocks service bundle; no classification is inferred from hospital names or OSM tags.

## Food-shop classification

Goldilocks keeps OSM `shop=supermarket` and `shop=convenience` as separate service toggles. This preserves a useful, if imperfect, OSM distinction between larger/full-range grocery shops and smaller local/convenience shops instead of silently labelling all grocery provision as a supermarket. The distinction is mapper-supplied rather than a measured store-size threshold, so it should be treated as a screening aid rather than a guarantee of stock range or floor area. Both food-shop toggles default to off; like the other service controls, an explicit user choice is persisted in `localStorage`.

## Current generated bundle

With the 2026-09-08 source refresh, the processed bundle contains 80,342 POIs after de-duplication:

- Supermarkets: 10,057
- Convenience / village shops: 33,591
- Post offices: 8,952
- Pharmacies: 9,081
- GPs: 7,435
- NHS dentists: 10,665
- General hospitals: 290 (261 England + 29 Scotland)
- Community hospitals: 271 (220 England + 51 Scotland)

There are 61 source records omitted because their postcodes are not present/resolvable in the pinned GB Code-Point source: 4 GP, 47 dental, 3 English general-hospital and 7 English community-hospital records. All 80 selected Scottish hospitals resolve successfully. The resulting service payload is 6,618,519 bytes as compact JSON and 1,807,090 bytes gzip-compressed.

## Browser/data design

The service transport is version 3. POIs remain spatially bucketed in 0.25-degree WGS84 buckets, with each bucket split into category-specific arrays. This permits the browser to count a sparse selection without scanning unrelated service types. There is no fixed zoom threshold: the browser shows selected services whenever no more than 200 fall inside the viewport, and otherwise renders none with a prompt to zoom in or select fewer categories. Fully enclosed bucket/category arrays are counted from their lengths, only edge buckets require coordinate tests, and counting stops immediately after the 201st match. Each tuple records coordinate, display name and source identity/reference; its category is supplied by the containing array. All eight service types are ordinary independent toggles persisted in `localStorage`.

Acceptance status is deliberately absent from the current transport and browser model. If approved machine-readable NHS access becomes available later, it can be reconsidered as a future feature rather than imposing unused states on the present UI.