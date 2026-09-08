#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
SOURCE_DIR = ROOT / "data" / "source" / "services"
DISCOVERY_PATH = SOURCE_DIR / "discovery.json"
CHUNK_SIZE = 4 * 1024 * 1024
USER_AGENT = "Goldilocks-Map/0.1 (+https://github.com/Robert-Hughes/goldilocks-map)"

OSM = {
    "id": "osm", "provider": "OpenStreetMap contributors", "distributor": "Geofabrik GmbH",
    "dataset": "OpenStreetMap Great Britain extract", "extract_date": "2026-09-06",
    "file": "great-britain-260906.osm.pbf", "url": "https://download.geofabrik.de/europe/great-britain-260906.osm.pbf",
    "bytes": 2_169_620_686, "md5": "a63b85616bfb5e4998fcf4ba5c2dcae3",
    "homepage_url": "https://www.openstreetmap.org/", "licence_name": "Open Database License (ODbL) 1.0",
    "licence_url": "https://opendatacommons.org/licenses/odbl/1-0/", "attribution": "© OpenStreetMap contributors",
}
CODEPOINT = {
    "id": "os-code-point-open", "provider": "Ordnance Survey", "dataset": "Code-Point Open", "release": "2026-08",
    "file": "codepo_gb.zip", "url": "https://api.os.uk/downloads/v1/products/CodePointOpen/downloads?area=GB&format=CSV&redirect",
    "bytes": 14_461_176, "md5": "42ecd9a7db141608dc6ab63f2dfb0bc3",
    "homepage_url": "https://www.ordnancesurvey.co.uk/products/code-point-open", "licence_name": "Open Government Licence v3.0",
    "licence_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
    "attribution": "Contains OS data © Crown copyright and database right 2026.",
}
ODS_GP = {
    "id": "nhs-ods-gp", "provider": "NHS England Organisation Data Service", "dataset": "General Medical Practices (epraccur)",
    "file": "epraccur.csv", "url": "https://www.odsdatasearchandexport.nhs.uk/api/getReport?report=epraccur",
    "homepage_url": "https://digital.nhs.uk/services/organisation-data-service/data-search-and-export/csv-downloads/gp-and-gp-practice-related-data",
    "licence_name": "Open Government Licence v3.0", "licence_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
    "attribution": "NHS England Organisation Data Service",
}
ODS_DENTAL = {
    "id": "nhs-ods-dental", "provider": "NHS England Organisation Data Service", "dataset": "General Dental Practices (egdpprac)",
    "file": "egdpprac.csv", "url": "https://www.odsdatasearchandexport.nhs.uk/api/getReport?report=egdpprac",
    "homepage_url": "https://digital.nhs.uk/services/organisation-data-service/data-search-and-export/csv-downloads/miscellaneous",
    "licence_name": "Open Government Licence v3.0", "licence_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
    "attribution": "NHS England Organisation Data Service",
}
PHS_GP = {
    "id": "phs-gp", "provider": "Public Health Scotland", "dataset": "GP Practice Contact Details and List Sizes", "release": "July 2026",
    "file": "scotland-gp-practices-2026-07.csv",
    "url": "https://www.opendata.nhs.scot/dataset/f23655c3-6e23-4103-a511-a80d998adb90/resource/0032ce94-f7f6-44dd-879c-7d3074d8e2e8/download/practice_contact_details_20260701_opendata.csv",
    "sha256": "7d6494a1d2b9653a7bca8f67094cd854d3f3fe0e1281624966eaf28c2f423a11",
    "homepage_url": "https://www.opendata.nhs.scot/dataset/gp-practice-contact-details-and-list-sizes",
    "licence_name": "Open Government Licence v3.0", "licence_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/", "attribution": "Public Health Scotland",
}
PHS_DENTAL = {
    "id": "phs-dental", "provider": "Public Health Scotland", "dataset": "Dental Practices and Patient Registrations", "release": "March 2026",
    "file": "scotland-dental-practices-2026-03.csv",
    "url": "https://www.opendata.nhs.scot/dataset/2f218ba7-6695-4b22-867d-41383ae36de7/resource/6c0d19e7-ba5b-46c2-8135-3200b59ad482/download/nhs-dental-practices-and-nhs-dental-registrations-as-at-31-mar-2026.csv",
    "sha256": "c700aa951a24d756cc9f8bef182e21bf310decf6344d2873439197539e52059c",
    "homepage_url": "https://www.opendata.nhs.scot/dataset/dental-practices-and-patient-registrations",
    "licence_name": "Open Government Licence v3.0", "licence_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/", "attribution": "Public Health Scotland",
}
PHS_HOSPITAL_CODES = {
    "id": "phs-hospital-codes", "provider": "Public Health Scotland", "dataset": "Current NHS Hospitals in Scotland", "release": "September 2026",
    "file": "scotland-hospitals-2026-09.csv",
    "url": "https://www.opendata.nhs.scot/dataset/cbd1802e-0e04-4282-88eb-d7bdcfb120f0/resource/c698f450-eeed-41a0-88f7-c1e40a568acc/download/hospitals.csv",
    "homepage_url": "https://www.opendata.nhs.scot/dataset/hospital-codes",
    "licence_name": "Open Government Licence v3.0", "licence_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/", "attribution": "Public Health Scotland",
}
PHS_HOSPITAL_PROFILE = {
    "id": "phs-hospital-profile", "provider": "Public Health Scotland", "dataset": "Hospital profile 2024-2025", "release": "2024/25",
    "file": "scotland-hospital-profile-2024-25.xlsx", "url": "https://publichealthscotland.scot/media/40009/hospital_profile_2025.xlsx",
    "bytes": 46_240, "sha256": "a5e66af55d8068aa4458bac55952e1842cdaddf3c600240f3d468135db5cdfae",
    "homepage_url": "https://publichealthscotland.scot/publications/scottish-health-service-costs-reference-files-2024-to-2025/",
    "licence_name": "Open Government Licence v3.0", "licence_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/", "attribution": "Public Health Scotland",
}
PHS_HOSPITAL_CLASSIFICATION = {
    "id": "phs-hospital-classification", "provider": "Public Health Scotland", "dataset": "Costs hospital classification 2024-2025", "release": "2024/25",
    "file": "scotland-hospital-classification-2024-25.xlsx", "url": "https://publichealthscotland.scot/media/40003/costs_hospital_classification_2025.xlsx",
    "bytes": 12_110, "sha256": "c4a4ca92a204a17c01bc5a705b7ab344d7cba63b80c0f78fad278a262f1ad227",
    "homepage_url": "https://publichealthscotland.scot/publications/scottish-health-service-costs-reference-files-2024-to-2025/",
    "licence_name": "Open Government Licence v3.0", "licence_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/", "attribution": "Public Health Scotland",
}
ERIC = {
    "id": "nhs-eric", "provider": "NHS England", "dataset": "Estates Returns Information Collection (ERIC) site data", "release": "2024/25",
    "file": "eric-2024-25-site-data.csv", "url": "https://files.digital.nhs.uk/AA/2375EE/ERIC%20-%202024_25%20-%20Site%20data.csv",
    "bytes": 6_093_038, "sha256": "a9b6798fd6bc57aaf810164f575d4ac914ee313834cdfa87cabbe05f23dfe84f",
    "homepage_url": "https://digital.nhs.uk/data-and-information/publications/statistical/estates-returns-information-collection/summary-page-and-dataset-for-eric-2024-25",
    "licence_name": "Open Government Licence v3.0", "licence_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/", "attribution": "NHS England",
}
def digest_file(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""): digest.update(chunk)
    return digest.hexdigest()


def request_bytes(url: str, headers: dict[str, str] | None = None) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})}), timeout=120) as response:
        return response.read()


def download_small(source: dict, *, refresh: bool = False) -> dict:
    destination = SOURCE_DIR / source["file"]
    if refresh or not destination.is_file():
        data = request_bytes(source["url"]); destination.write_bytes(data)
        print(f"Downloaded {source['dataset']}: {len(data):,} bytes")
    result = dict(source); result["bytes"] = destination.stat().st_size; result["sha256"] = digest_file(destination, "sha256")
    if source.get("bytes") is not None and result["bytes"] != source["bytes"]: raise RuntimeError(f"{source['file']} size differs from pinned source")
    if source.get("sha256") is not None and result["sha256"] != source["sha256"]: raise RuntimeError(f"{source['file']} SHA-256 differs from pinned source")
    return result


def download_osm(verify: bool) -> dict:
    destination = SOURCE_DIR / OSM["file"]
    part = destination.with_suffix(destination.suffix + ".part")
    if not destination.is_file() or destination.stat().st_size != OSM["bytes"]:
        existing = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        with urlopen(Request(OSM["url"], headers={"User-Agent": USER_AGENT, **headers}), timeout=120) as response:
            status = getattr(response, "status", response.getcode())
            if existing and status != 206: existing = 0; part.unlink(missing_ok=True)
            with part.open("ab" if existing else "wb") as handle: shutil.copyfileobj(response, handle, length=CHUNK_SIZE)
        if part.stat().st_size != OSM["bytes"] or digest_file(part, "md5") != OSM["md5"]: raise RuntimeError("Downloaded OSM PBF failed pinned size/MD5")
        part.replace(destination); print(f"Downloaded and verified {destination}")
    elif verify:
        if digest_file(destination, "md5") != OSM["md5"]: raise RuntimeError("Cached OSM PBF failed pinned MD5")
        print(f"Verified {destination.name}")
    else: print(f"Using cached {destination} ({destination.stat().st_size:,} bytes)")
    return dict(OSM)


def download_codepoint() -> dict:
    product = json.loads(request_bytes("https://api.os.uk/downloads/v1/products/CodePointOpen"))
    if product.get("version") != CODEPOINT["release"]: raise RuntimeError(f"Code-Point Open release changed to {product.get('version')!r}")
    downloads = json.loads(request_bytes("https://api.os.uk/downloads/v1/products/CodePointOpen/downloads"))
    match = next((x for x in downloads if x.get("area") == "GB" and x.get("format") == "CSV"), None)
    if not match or match.get("md5") != CODEPOINT["md5"] or int(match.get("size", -1)) != CODEPOINT["bytes"]: raise RuntimeError("Code-Point Open metadata differs from pinned 2026-08 release")
    return download_small(CODEPOINT)




def main() -> int:
    parser = argparse.ArgumentParser(description="Download the offline source set for Goldilocks service POIs.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true", help="recompute MD5 for the cached 2 GiB OSM PBF")
    args = parser.parse_args()
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"OSM {OSM['extract_date']}; Code-Point Open {CODEPOINT['release']}; PHS GP {PHS_GP['release']}; PHS dental {PHS_DENTAL['release']}; PHS hospitals {PHS_HOSPITAL_CODES['release']}; PHS Costs Book {PHS_HOSPITAL_PROFILE['release']}; ERIC {ERIC['release']}")
    if args.dry_run: return 0
    sources: dict[str,dict] = {}
    for source in [
        download_osm(args.verify), download_codepoint(),
        download_small(ODS_GP, refresh=True), download_small(ODS_DENTAL, refresh=True),
        download_small(PHS_GP), download_small(PHS_DENTAL),
        download_small(PHS_HOSPITAL_CODES, refresh=True), download_small(PHS_HOSPITAL_PROFILE),
        download_small(PHS_HOSPITAL_CLASSIFICATION), download_small(ERIC),
    ]:
        sources[source["id"]] = source
    DISCOVERY_PATH.write_text(json.dumps({"format_version":2,"dataset":"Goldilocks service POI source set","generated_at_unix":int(time.time()),"sources":sources}, indent=2)+"\n", encoding="utf-8")
    print(f"Wrote {DISCOVERY_PATH}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
