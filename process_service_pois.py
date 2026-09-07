#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import shutil
import subprocess
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from pyproj import Transformer

ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = ROOT / "data" / "source" / "services"
DEFAULT_DISCOVERY = SOURCE_ROOT / "discovery.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "derived" / "services"
MIN_ZOOM = 12
BUCKET_SCALE = 4
COORDINATE_SCALE = 100_000
DEDUP_DISTANCE_M = 25.0

CATEGORIES = (
    {"id":"supermarket","label":"Supermarkets","order":0},
    {"id":"post_office","label":"Post offices","order":1},
    {"id":"pharmacy","label":"Pharmacies","order":2},
    {"id":"gp","label":"GPs","order":3},
    {"id":"dentist","label":"NHS dentists","order":4},
    {"id":"hospital_general","label":"General hospitals","order":5},
    {"id":"hospital_community","label":"Community hospitals","order":6},
)
CATEGORY_INDEX = {item["id"]: i for i,item in enumerate(CATEGORIES)}
SOURCE_PRIORITY = ["osm","nhs-ods-gp","nhs-ods-dental","phs-gp","phs-dental","nhs-eric","os-code-point-open"]

OSMCONF = """[general]
closed_ways_are_polygons=aeroway,amenity,boundary,building,craft,geological,historic,landuse,leisure,military,natural,office,place,shop,sport,tourism,highway=platform,public_transport=platform
[points]
osm_id=yes
attributes=name,amenity,shop,brand,operator
unsignificant=created_by,converted_by,source,time,ele,attribution
ignore=created_by,converted_by,source,time,ele,note,todo,openGeoDB:,fixme,FIXME
other_tags=no
[lines]
osm_id=yes
attributes=name,highway
other_tags=no
[multipolygons]
osm_id=yes
attributes=name,type,amenity,shop,brand,operator
other_tags=no
[multilinestrings]
osm_id=yes
attributes=name,type
other_tags=no
[other_relations]
osm_id=yes
attributes=name,type
other_tags=no
"""

class Poi(dict): pass

def norm_postcode(value: str) -> str: return "".join(value.upper().split())
def clean_name(value: str, fallback: str) -> str:
    value = " ".join((value or "").split())
    return value[:200] if value else fallback

def run_ogr_extract(pbf: Path, osmconf: Path, output: Path, polygons: bool) -> None:
    if polygons:
        sql = "SELECT CASE WHEN osm_id IS NOT NULL THEN 'r/' || CAST(osm_id AS TEXT) ELSE 'w/' || CAST(osm_way_id AS TEXT) END AS osm_ref, name, amenity, shop, brand, operator, ST_PointOnSurface(geometry) AS geometry FROM multipolygons WHERE shop = 'supermarket' OR amenity IN ('post_office','pharmacy')"
    else:
        sql = "SELECT 'n/' || CAST(osm_id AS TEXT) AS osm_ref, name, amenity, shop, brand, operator, geometry FROM points WHERE shop = 'supermarket' OR amenity IN ('post_office','pharmacy')"
    command = ["ogr2ogr","-f","GeoJSONSeq",str(output),str(pbf),"-oo",f"CONFIG_FILE={osmconf}","-dialect","SQLITE","-sql",sql,"-lco","RS=NO"]
    print("Extracting", "area POIs" if polygons else "node POIs", flush=True)
    subprocess.run(command, check=True)

def osm_categories(properties: dict) -> list[str]:
    result=[]
    if properties.get("shop") == "supermarket": result.append("supermarket")
    if properties.get("amenity") == "post_office": result.append("post_office")
    if properties.get("amenity") == "pharmacy": result.append("pharmacy")
    return result

def load_osm_geojsonseq(path: Path, source_rank: int) -> list[Poi]:
    result=[]
    with path.open("r",encoding="utf-8") as handle:
        for line_number,line in enumerate(handle,1):
            if not line.strip(): continue
            feature=json.loads(line); geometry=feature.get("geometry") or {}; coordinates=geometry.get("coordinates")
            if geometry.get("type") != "Point" or not isinstance(coordinates,list) or len(coordinates)<2: raise RuntimeError(f"{path.name}:{line_number}: expected Point")
            lon,lat=float(coordinates[0]),float(coordinates[1]); props=feature.get("properties") or {}; ref=str(props.get("osm_ref") or "")
            if not (-10 <= lon <= 4 and 49 <= lat <= 62) or not ref: raise RuntimeError(f"{path.name}:{line_number}: invalid OSM POI")
            for category in osm_categories(props):
                name=next((clean_name(str(props.get(k) or ""),"") for k in ("name","brand","operator") if str(props.get(k) or "").strip()),"")
                if not name: name={"supermarket":"Unnamed supermarket","post_office":"Unnamed post office","pharmacy":"Unnamed pharmacy"}[category]
                result.append(Poi(category_id=category,lat=lat,lon=lon,name=name,source_id="osm",source_ref=ref,source_rank=source_rank))
    return result

def load_ods_records(path: Path, category: str) -> list[dict]:
    result=[]
    with path.open("r",encoding="utf-8-sig",newline="") as handle:
        for row in csv.reader(handle):
            if len(row)!=27 or row[12] != "ACTIVE": continue
            if category=="gp" and row[25] != "RO76": continue
            result.append({"category_id":category,"code":row[0],"name":row[1],"postcode":row[9],"source_id":"nhs-ods-gp" if category=="gp" else "nhs-ods-dental","source_rank":10})
    return result

def load_phs_gp(path: Path) -> list[dict]:
    with path.open("r",encoding="utf-8-sig",newline="") as handle:
        return [{"category_id":"gp","code":row["PracticeCode"],"name":row["GPPracticeName"],"postcode":row["Postcode"],"source_id":"phs-gp","source_rank":20} for row in csv.DictReader(handle)]

def load_phs_dental(path: Path) -> list[dict]:
    with path.open("r",encoding="utf-8-sig",newline="") as handle:
        return [{"category_id":"dentist","code":row["Dental_Practice_Code"],"name":row.get("address1") or "NHS dental practice","postcode":row["pc7"],"source_id":"phs-dental","source_rank":20} for row in csv.DictReader(handle)]

def load_eric(path: Path) -> list[dict]:
    accepted={"General acute hospital":"hospital_general","Mixed service hospital":"hospital_general","Community hospital (with inpatient beds)":"hospital_community"}
    result=[]
    with path.open("r",encoding="utf-8-sig",errors="replace",newline="") as handle:
        for row in csv.DictReader(handle):
            category=accepted.get(row.get("Site Type") or "")
            if category: result.append({"category_id":category,"code":row["Site Code"],"name":row["Site Name"],"postcode":row["Post Code"],"source_id":"nhs-eric","source_rank":10})
    return result


def geocode_postcodes(codepoint_zip: Path, needed: set[str]) -> dict[str,tuple[float,float]]:
    transformer=Transformer.from_crs("EPSG:27700","EPSG:4326",always_xy=True); result={}
    with zipfile.ZipFile(codepoint_zip) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".csv"): continue
            with archive.open(name) as raw:
                text=(line.decode("utf-8") for line in raw)
                for row in csv.reader(text):
                    if len(row)<4: continue
                    postcode=norm_postcode(row[0])
                    if postcode not in needed: continue
                    try: lon,lat=transformer.transform(float(row[2]),float(row[3]))
                    except ValueError: continue
                    result[postcode]=(lat,lon)
    return result

def add_geocoded(records: list[dict], geocodes: dict[str,tuple[float,float]], missing: Counter) -> list[Poi]:
    result=[]
    for record in records:
        postcode=norm_postcode(record["postcode"]); point=geocodes.get(postcode)
        if not point: missing[record["category_id"]]+=1; continue
        result.append(Poi(category_id=record["category_id"],lat=point[0],lon=point[1],name=clean_name(record["name"],record["category_id"]),source_id=record["source_id"],source_ref=str(record["code"]),source_rank=record["source_rank"]))
    return result

def normalized_name(value: str) -> str: return "".join(c.lower() for c in value if c.isalnum())
def approximate_distance_m(a:Poi,b:Poi)->float:
    mean=math.radians((float(a["lat"])+float(b["lat"]))/2); dx=(float(a["lon"])-float(b["lon"]))*111_320*math.cos(mean); dy=(float(a["lat"])-float(b["lat"]))*110_540
    return math.hypot(dx,dy)

def deduplicate(pois:list[Poi])->tuple[list[Poi],int]:
    pois.sort(key=lambda x:(int(x["source_rank"]),str(x["category_id"]),str(x["source_ref"])))
    cells:dict[tuple[str,str,int,int],list[Poi]]=defaultdict(list); kept=[]; removed=0; cell_degrees=.0005
    for poi in pois:
        key=normalized_name(str(poi["name"]));
        if key.startswith("unnamed"): key += ":"+str(poi["source_ref"])
        latc=math.floor(float(poi["lat"])/cell_degrees); lonc=math.floor(float(poi["lon"])/cell_degrees); duplicate=False
        for dy in (-1,0,1):
            for dx in (-1,0,1):
                for candidate in cells.get((str(poi["category_id"]),key,latc+dy,lonc+dx),[]):
                    if approximate_distance_m(poi,candidate)<=DEDUP_DISTANCE_M: duplicate=True; break
                if duplicate: break
            if duplicate: break
        if duplicate: removed+=1; continue
        kept.append(poi); cells[(str(poi["category_id"]),key,latc,lonc)].append(poi)
    return kept,removed

def public_source(source:dict)->dict:
    keys=("id","provider","distributor","dataset","release","extract_date","homepage_url","licence_name","licence_url","attribution")
    return {k:source[k] for k in keys if source.get(k) is not None}

def build_payload(pois:list[Poi], source_order:list[str])->dict:
    source_index={source:i for i,source in enumerate(source_order)}; buckets:dict[str,list[list]]=defaultdict(list)
    for poi in sorted(pois,key=lambda x:(float(x["lat"]),float(x["lon"]),str(x["category_id"]),str(x["source_ref"]))):
        lat=float(poi["lat"]); lon=float(poi["lon"]); cat=str(poi["category_id"]); ref=str(poi["source_ref"])
        buckets[f"{math.floor(lat*BUCKET_SCALE)}:{math.floor(lon*BUCKET_SCALE)}"].append([f"{cat}:{poi['source_id']}:{ref}",CATEGORY_INDEX[cat],round(lat*COORDINATE_SCALE),round(lon*COORDINATE_SCALE),str(poi["name"]),source_index[str(poi["source_id"])],ref])
    return {"buckets":dict(sorted(buckets.items()))}

def main()->int:
    parser=argparse.ArgumentParser(description="Build Goldilocks OSM and NHS service POI bundle."); parser.add_argument("--discovery",type=Path,default=DEFAULT_DISCOVERY); parser.add_argument("--output-dir",type=Path,default=DEFAULT_OUTPUT_DIR); args=parser.parse_args()
    if shutil.which("ogr2ogr") is None: raise RuntimeError("ogr2ogr with GDAL OSM driver is required")
    discovery=json.loads(args.discovery.read_text(encoding="utf-8"))
    if discovery.get("format_version")!=2: raise RuntimeError("Run updated download_service_sources.py first")
    sources=discovery["sources"]
    def source_path(source_id:str)->Path:
        source=sources.get(source_id); filename=source.get("file") if source else None
        if not filename: raise RuntimeError(f"Missing service source {source_id}")
        path=args.discovery.parent/filename
        if not path.is_file(): raise RuntimeError(f"Missing source file {path}")
        return path

    pbf=source_path("osm"); cache_dir=SOURCE_ROOT/"cache"/pbf.stem; cache_dir.mkdir(parents=True,exist_ok=True); osmconf=cache_dir/"osmconf.ini"; osmconf.write_text(OSMCONF,encoding="utf-8")
    points=cache_dir/"points.geojsonl"; areas=cache_dir/"areas.geojsonl"
    if not points.is_file() or points.stat().st_size==0: points.unlink(missing_ok=True); run_ogr_extract(pbf,osmconf,points,False)
    else: print(f"Using cached node POIs: {points}")
    if not areas.is_file() or areas.stat().st_size==0: areas.unlink(missing_ok=True); run_ogr_extract(pbf,osmconf,areas,True)
    else: print(f"Using cached area POIs: {areas}")
    pois=load_osm_geojsonseq(points,0)+load_osm_geojsonseq(areas,1)

    records=load_ods_records(source_path("nhs-ods-gp"),"gp")+load_ods_records(source_path("nhs-ods-dental"),"dentist")+load_phs_gp(source_path("phs-gp"))+load_phs_dental(source_path("phs-dental"))+load_eric(source_path("nhs-eric"))
    needed={norm_postcode(r["postcode"]) for r in records if r.get("postcode")}; geocodes=geocode_postcodes(source_path("os-code-point-open"),needed)
    missing=Counter(); pois.extend(add_geocoded(records,geocodes,missing))
    raw_counts=Counter(str(x["category_id"]) for x in pois); deduped,duplicates=deduplicate(pois); counts=Counter(str(x["category_id"]) for x in deduped)
    used_sources=[s for s in SOURCE_PRIORITY if any(x["source_id"]==s for x in deduped)]; payload=build_payload(deduped,used_sources); raw=json.dumps(payload,separators=(",",":"),ensure_ascii=False).encode(); compressed=gzip.compress(raw,compresslevel=9,mtime=0)
    output=args.output_dir.resolve(); output.mkdir(parents=True,exist_ok=True); (output/"services.json.gz").write_bytes(compressed)
    categories=[dict(item,count=int(counts[item["id"]])) for item in CATEGORIES]
    manifest={"format_version":2,"kind":"goldilocks-services","generated_at_unix":int(time.time()),"min_zoom":MIN_ZOOM,"bucket_scale":BUCKET_SCALE,"coordinate_scale":COORDINATE_SCALE,"categories":categories,"counts":{x["id"]:int(counts[x["id"]]) for x in CATEGORIES},"raw_counts_before_spatial_dedup":{x["id"]:int(raw_counts[x["id"]]) for x in CATEGORIES},"spatial_duplicates_removed":duplicates,"missing_postcode_geocodes":dict(missing),"source_order":used_sources,"sources":{s:public_source(sources[s]) for s in used_sources},"geocoder_source":public_source(sources["os-code-point-open"]),"payload_file":"services.json.gz","payload_encoding":"gzip+json","raw_bytes":len(raw),"compressed_bytes":len(compressed),"sha256_raw":hashlib.sha256(raw).hexdigest()}
    (output/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    print(f"Service POIs: {len(deduped):,}; removed {duplicates:,} duplicates; missing postcode geocodes {sum(missing.values()):,}")
    print("Counts: "+", ".join(f"{x['id']}={counts[x['id']]:,}" for x in CATEGORIES)); print(f"Payload {len(raw):,} -> {len(compressed):,} bytes gzip"); print(f"Wrote {output/'manifest.json'}")
    return 0

if __name__=="__main__": raise SystemExit(main())
