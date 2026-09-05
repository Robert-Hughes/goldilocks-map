#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = ROOT / "data" / "source" / "defra-pcm"
PCM_PAGE_URL = "https://uk-air.defra.gov.uk/data/pcm-data"
DEFAULT_YEARS = (2022, 2023, 2024)
DEFAULT_VARIABLES = ("pm25", "no2", "pm10", "ozone_dgt120")
USER_AGENT = "GoldilocksMap/0.1 (+https://github.com/)"


@dataclass(frozen=True)
class SourceFile:
    variable: str
    year: int
    filename: str
    url: str


def expected_filename(variable: str, year: int) -> str:
    patterns = {
        "pm25": f"mappm25{year}g.csv",
        "pm10": f"mappm10{year}g.csv",
        "no2": f"mapno2{year}.csv",
        "ozone_dgt120": f"mapdgt120{year % 100:02d}.csv",
    }
    try:
        return patterns[variable]
    except KeyError as exc:
        raise RuntimeError(f"Unsupported PCM variable {variable!r}") from exc


class LinkParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(urljoin(self.base_url, href))


def fetch_bytes(url: str, *, range_start: int | None = None) -> tuple[bytes, int, dict[str, str]]:
    headers = {"User-Agent": USER_AGENT}
    if range_start:
        headers["Range"] = f"bytes={range_start}-"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=60) as response:
        return response.read(), int(getattr(response, "status", 200)), dict(response.headers.items())


def discover_sources(years: tuple[int, ...], variables: tuple[str, ...]) -> list[SourceFile]:
    html, _, _ = fetch_bytes(PCM_PAGE_URL)
    parser = LinkParser(PCM_PAGE_URL)
    parser.feed(html.decode("utf-8", "replace"))
    csv_by_name = {
        Path(url.split("?", 1)[0]).name.lower(): url
        for url in parser.links
        if url.lower().split("?", 1)[0].endswith(".csv")
    }
    result: list[SourceFile] = []
    missing: list[str] = []
    for year in years:
        for variable in variables:
            filename = expected_filename(variable, year)
            url = csv_by_name.get(filename.lower())
            if not url:
                missing.append(filename)
                continue
            result.append(SourceFile(variable, year, filename, url))
    if missing:
        raise RuntimeError(
            "The UK-AIR PCM page did not expose the expected source file(s): " + ", ".join(missing)
        )
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_pcm_csv(path: Path) -> None:
    if path.stat().st_size < 10_000:
        raise RuntimeError(f"Downloaded PCM file is unexpectedly small: {path}")
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        head = "".join(handle.readline() for _ in range(10)).lower()
    if "gridcode" not in head or ",x,y," not in head:
        raise RuntimeError(f"Downloaded file does not look like a PCM grid CSV: {path}")


def download_one(item: SourceFile, *, force: bool) -> dict:
    target_dir = SOURCE_ROOT / str(item.year)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / item.filename
    part = target.with_suffix(target.suffix + ".part")
    if target.is_file() and not force:
        validate_pcm_csv(target)
        return {
            "variable": item.variable,
            "year": item.year,
            "file": str(target.relative_to(SOURCE_ROOT)),
            "url": item.url,
            "bytes": target.stat().st_size,
            "sha256": sha256_file(target),
            "cached": True,
        }
    if force:
        target.unlink(missing_ok=True)
        part.unlink(missing_ok=True)

    resume_from = part.stat().st_size if part.exists() else 0
    data, status, headers = fetch_bytes(item.url, range_start=resume_from or None)
    if resume_from and status == 206:
        mode = "ab"
    else:
        mode = "wb"
        resume_from = 0
    with part.open(mode) as handle:
        handle.write(data)

    content_length = headers.get("Content-Length")
    if content_length is not None:
        expected = resume_from + int(content_length)
        if part.stat().st_size != expected:
            raise RuntimeError(
                f"Short download for {item.filename}: {part.stat().st_size} bytes, expected {expected}"
            )
    part.replace(target)
    validate_pcm_csv(target)
    return {
        "variable": item.variable,
        "year": item.year,
        "file": str(target.relative_to(SOURCE_ROOT)),
        "url": item.url,
        "bytes": target.stat().st_size,
        "sha256": sha256_file(target),
        "cached": False,
    }


def parse_csv_ints(value: str) -> tuple[int, ...]:
    return tuple(int(item.strip()) for item in value.split(",") if item.strip())


def parse_csv_strings(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover and download Defra UK-AIR Pollution Climate Mapping 1 km CSV grids."
    )
    parser.add_argument("--years", default=",".join(map(str, DEFAULT_YEARS)))
    parser.add_argument("--variables", default=",".join(DEFAULT_VARIABLES))
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    years = parse_csv_ints(args.years)
    variables = parse_csv_strings(args.variables)
    unknown = sorted(set(variables) - set(DEFAULT_VARIABLES))
    if unknown:
        raise SystemExit(f"Unknown pollution variables: {', '.join(unknown)}")
    sources = discover_sources(years, variables)
    print(f"Discovered {len(sources)} PCM files on {PCM_PAGE_URL}")
    for item in sources:
        print(f"  {item.year} {item.variable:12s} {item.url}")
    if args.dry_run:
        return 0

    records: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(download_one, item, force=args.force): item for item in sources}
        for future in as_completed(futures):
            item = futures[future]
            record = future.result()
            records.append(record)
            status = "cached" if record["cached"] else "downloaded"
            print(f"{status:10s} {item.year} {item.variable:12s} {record['bytes']:,} bytes", flush=True)

    records.sort(key=lambda item: (item["year"], item["variable"]))
    SOURCE_ROOT.mkdir(parents=True, exist_ok=True)
    discovery = {
        "format_version": 1,
        "provider": "Department for Environment, Food and Rural Affairs (Defra)",
        "dataset": "UK-AIR Pollution Climate Mapping (PCM) background pollution data",
        "source_page": PCM_PAGE_URL,
        "retrieved_at_unix": int(time.time()),
        "years": list(years),
        "variables": list(variables),
        "files": records,
    }
    path = SOURCE_ROOT / "discovery.json"
    path.write_text(json.dumps(discovery, indent=2) + "\n", encoding="utf-8")
    total = sum(int(item["bytes"]) for item in records)
    print(f"Wrote {path}; {len(records)} files, {total:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
