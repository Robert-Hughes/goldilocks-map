#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
import concurrent.futures
import hashlib
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = ROOT / "data" / "source" / "hadukgrid"
MANIFEST_PATH = SOURCE_ROOT / "download-manifest.json"

VARIABLES = ("tasmax", "tasmin")
HISTORICAL_START_YEAR = 2016
HISTORICAL_END_YEAR = 2025
PROVISIONAL_YEAR = 2026
CEDA_DATA_ROOT = (
    "https://data.ceda.ac.uk/badc/ukmo-hadobs/data/insitu/MOHC/HadOBS/"
    "HadUK-Grid/v1.3.2.ceda/1km"
)
CEDA_DAP_ROOT = "https://dap.ceda.ac.uk"
MET_OFFICE_PROVISIONAL_ROOT = "https://www.metoffice.gov.uk/hadobs/hadukgrid/data"
USER_AGENT = "GoldilocksMap/0.1"
CHUNK_SIZE = 4 * 1024 * 1024
PRINT_LOCK = threading.Lock()


@dataclass(frozen=True)
class SourceFile:
    source: str
    variable: str
    year: int
    month: int
    url: str
    destination: str
    size: int | None
    md5: str | None = None
    archive_version: str | None = None

    @property
    def path(self) -> Path:
        return ROOT / self.destination


class AuthenticationError(RuntimeError):
    pass


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not follow CEDA login redirects while carrying a bearer token."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


CEDA_OPENER = urllib.request.build_opener(NoRedirectHandler())


def log(message: str) -> None:
    with PRINT_LOCK:
        print(message, flush=True)


def request_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.load(response)


def ceda_day_version(variable: str) -> str:
    listing = request_json(f"{CEDA_DATA_ROOT}/{variable}/day?json=")
    versions = sorted(
        item["name"]
        for item in listing.get("items", [])
        if item.get("type") == "dir" and item.get("name", "").startswith("v")
    )
    if not versions:
        raise RuntimeError(f"No CEDA daily archive version found for {variable}")
    if len(versions) > 1:
        log(f"{variable}: CEDA exposes {versions}; using latest {versions[-1]}")
    return versions[-1]


def discover_ceda_files(variables: Iterable[str]) -> list[SourceFile]:
    files: list[SourceFile] = []
    for variable in variables:
        version = ceda_day_version(variable)
        listing_url = f"{CEDA_DATA_ROOT}/{variable}/day/{version}?json="
        listing = request_json(listing_url)
        selected = []
        for item in listing.get("items", []):
            if item.get("type") != "file" or item.get("ext") != ".nc":
                continue
            date = item.get("regex_date")
            if not date:
                continue
            year, month, _ = (int(part) for part in date.split("-"))
            if not HISTORICAL_START_YEAR <= year <= HISTORICAL_END_YEAR:
                continue
            selected.append((year, month, item))

        selected.sort(key=lambda entry: (entry[0], entry[1]))
        expected_count = (HISTORICAL_END_YEAR - HISTORICAL_START_YEAR + 1) * 12
        if len(selected) != expected_count:
            raise RuntimeError(
                f"Expected {expected_count} monthly CEDA {variable} files for "
                f"{HISTORICAL_START_YEAR}-{HISTORICAL_END_YEAR}, found {len(selected)}"
            )

        for year, month, item in selected:
            archive_path = item["path"]
            url = item.get("download") or f"{CEDA_DAP_ROOT}{archive_path}?download=1"
            destination = (
                SOURCE_ROOT
                / "v1.3.2.ceda"
                / "1km"
                / variable
                / "day"
                / version
                / item["name"]
            )
            files.append(
                SourceFile(
                    source="ceda-v1.3.2.ceda",
                    variable=variable,
                    year=year,
                    month=month,
                    url=url,
                    destination=str(destination.relative_to(ROOT)),
                    size=int(item["size"]) if item.get("size") is not None else None,
                    md5=item.get("md5"),
                    archive_version=version,
                )
            )
    return files


def provisional_filename(variable: str, year: int, month: int) -> str:
    last_day = calendar.monthrange(year, month)[1]
    return (
        f"{variable}_hadukgrid_uk_1km_day_"
        f"{year}{month:02d}01-{year}{month:02d}{last_day:02d}.nc"
    )


def head_content_length(url: str, headers: dict[str, str] | None = None) -> int | None:
    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers, method="HEAD")
    with urllib.request.urlopen(request, timeout=45) as response:
        value = response.headers.get("Content-Length")
        return int(value) if value is not None else None


def discover_provisional_files(variables: Iterable[str]) -> list[SourceFile]:
    files: list[SourceFile] = []
    for variable in variables:
        found_any = False
        for month in range(1, 13):
            filename = provisional_filename(variable, PROVISIONAL_YEAR, month)
            url = f"{MET_OFFICE_PROVISIONAL_ROOT}/{PROVISIONAL_YEAR}/{filename}"
            try:
                size = head_content_length(url)
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    continue
                raise
            found_any = True
            destination = SOURCE_ROOT / "provisional" / str(PROVISIONAL_YEAR) / "1km" / variable / "day" / filename
            files.append(
                SourceFile(
                    source="met-office-provisional",
                    variable=variable,
                    year=PROVISIONAL_YEAR,
                    month=month,
                    url=url,
                    destination=str(destination.relative_to(ROOT)),
                    size=size,
                )
            )
        if not found_any:
            raise RuntimeError(f"No {PROVISIONAL_YEAR} provisional {variable} files found")
    return files


def discover_files(source: str, variables: Iterable[str]) -> list[SourceFile]:
    files: list[SourceFile] = []
    if source in {"all", "ceda"}:
        files.extend(discover_ceda_files(variables))
    if source in {"all", "provisional"}:
        files.extend(discover_provisional_files(variables))
    files.sort(key=lambda item: (item.source, item.variable, item.year, item.month))
    return files


def write_manifest(files: list[SourceFile]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "historical_period": [HISTORICAL_START_YEAR, HISTORICAL_END_YEAR],
        "provisional_year": PROVISIONAL_YEAR,
        "generated_at_unix": int(time.time()),
        "files": [asdict(item) for item in files],
    }
    MANIFEST_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def human_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.2f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def is_complete(item: SourceFile, verify: bool = False) -> bool:
    path = item.path
    if not path.is_file():
        return False
    if item.size is not None and path.stat().st_size != item.size:
        return False
    if verify and item.md5 and file_md5(path) != item.md5:
        return False
    return True


def maybe_import_legacy_cache(item: SourceFile) -> bool:
    """Reuse the old single-file cache where possible without duplicating data."""
    destination = item.path
    if destination.exists():
        return False
    legacy = ROOT / "data" / destination.name
    if not legacy.is_file():
        return False
    if item.size is not None and legacy.stat().st_size != item.size:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(legacy, destination)
        action = "hard-linked"
    except OSError:
        shutil.copy2(legacy, destination)
        action = "copied"
    log(f"Reused legacy cache ({action}): {destination.name}")
    return True


def auth_headers(item: SourceFile, token: str | None) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    if item.source.startswith("ceda-"):
        if not token:
            raise AuthenticationError(
                "CEDA_ACCESS_TOKEN is required for CEDA downloads. "
                "Set it in the environment; do not put the token in the repository."
            )
        headers["Authorization"] = f"Bearer {token}"
    return headers


def check_ceda_auth(item: SourceFile, token: str | None) -> None:
    headers = auth_headers(item, token)
    request = urllib.request.Request(item.url, headers=headers, method="HEAD")
    try:
        with CEDA_OPENER.open(request, timeout=45) as response:
            size_header = response.headers.get("Content-Length")
            size = int(size_header) if size_header is not None else None
    except urllib.error.HTTPError as error:
        if error.code in {301, 302, 303, 307, 308, 401, 403}:
            raise AuthenticationError(
                f"CEDA rejected/redirected the bearer token with HTTP {error.code}. "
                "Generate a fresh archive access token and try again."
            ) from error
        raise
    if item.size is not None and size is not None and size != item.size:
        raise RuntimeError(
            f"CEDA auth check reached the file but size differs: catalogue={item.size}, HTTP={size}"
        )
    log(f"CEDA authentication OK: {item.path.name} ({human_bytes(item.size or size)})")


def download_once(item: SourceFile, token: str | None, verify: bool) -> str:
    destination = item.path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if is_complete(item, verify=verify):
        return f"cached {destination.name}"
    maybe_import_legacy_cache(item)
    if is_complete(item, verify=verify):
        return f"cached {destination.name}"

    part = destination.with_suffix(destination.suffix + ".part")
    existing = part.stat().st_size if part.exists() else 0
    if item.size is not None and existing > item.size:
        part.unlink()
        existing = 0

    headers = auth_headers(item, token)
    if existing:
        headers["Range"] = f"bytes={existing}-"
    request = urllib.request.Request(item.url, headers=headers)

    try:
        if item.source.startswith("ceda-"):
            response = CEDA_OPENER.open(request, timeout=120)
        else:
            response = urllib.request.urlopen(request, timeout=120)
    except urllib.error.HTTPError as error:
        if item.source.startswith("ceda-") and error.code in {301, 302, 303, 307, 308, 401, 403}:
            raise AuthenticationError(f"CEDA authentication failed/redirected with HTTP {error.code}") from error
        if error.code == 416 and item.size is not None and existing == item.size:
            part.replace(destination)
            response = None
        else:
            raise

    if response is not None:
        with response:
            status = getattr(response, "status", response.getcode())
            append = existing > 0 and status == 206
            if existing > 0 and not append:
                log(f"Server did not resume {destination.name}; restarting that file")
                existing = 0
            mode = "ab" if append else "wb"
            with part.open(mode) as output:
                shutil.copyfileobj(response, output, length=CHUNK_SIZE)
        if item.size is not None and part.stat().st_size != item.size:
            raise RuntimeError(
                f"Incomplete download for {destination.name}: "
                f"got {part.stat().st_size:,}, expected {item.size:,} bytes"
            )
        part.replace(destination)

    if item.md5:
        digest = file_md5(destination)
        if digest != item.md5:
            bad = destination.with_suffix(destination.suffix + ".bad-md5")
            destination.replace(bad)
            raise RuntimeError(
                f"MD5 mismatch for {destination.name}: got {digest}, expected {item.md5}; "
                f"moved bad file to {bad.name}"
            )
    return f"downloaded {destination.name} ({human_bytes(destination.stat().st_size)})"


def download_with_retries(item: SourceFile, token: str | None, verify: bool, retries: int) -> str:
    for attempt in range(1, retries + 1):
        try:
            return download_once(item, token, verify)
        except AuthenticationError:
            raise
        except Exception as error:
            if attempt == retries:
                raise
            delay = min(60, 2 ** attempt)
            log(f"Retry {attempt}/{retries - 1} for {item.path.name} after {type(error).__name__}: {error}; sleeping {delay}s")
            time.sleep(delay)
    raise AssertionError("unreachable")


def pending_summary(files: list[SourceFile], verify: bool) -> tuple[int, int]:
    pending = [item for item in files if not is_complete(item, verify=verify)]
    bytes_pending = sum(item.size or 0 for item in pending)
    return len(pending), bytes_pending


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Discover and download the HadUK-Grid daily tasmax/tasmin source files used by Goldilocks. "
            "CEDA credentials are supplied only through the CEDA_ACCESS_TOKEN environment variable."
        )
    )
    parser.add_argument("--source", choices=("all", "ceda", "provisional"), default="all")
    parser.add_argument("--variables", nargs="+", choices=VARIABLES, default=list(VARIABLES))
    parser.add_argument("--workers", type=int, default=3, help="Concurrent downloads (default: 3)")
    parser.add_argument("--retries", type=int, default=5, help="Attempts per file (default: 5)")
    parser.add_argument("--dry-run", action="store_true", help="Discover files and print totals without downloading")
    parser.add_argument("--check-auth", action="store_true", help="Check the CEDA bearer token against one file and exit")
    parser.add_argument("--verify", action="store_true", help="MD5-check already cached CEDA files as well as new downloads")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.workers < 1:
        raise SystemExit("--workers must be at least 1")
    if args.retries < 1:
        raise SystemExit("--retries must be at least 1")

    log("Discovering HadUK-Grid source files...")
    files = discover_files(args.source, args.variables)
    write_manifest(files)
    total_bytes = sum(item.size or 0 for item in files)
    pending_count, pending_bytes = pending_summary(files, verify=args.verify)
    by_source: dict[str, list[SourceFile]] = {}
    for item in files:
        by_source.setdefault(item.source, []).append(item)
    for source, source_files in by_source.items():
        source_bytes = sum(item.size or 0 for item in source_files)
        log(f"  {source}: {len(source_files)} files, {human_bytes(source_bytes)}")
    log(f"Total: {len(files)} files, {human_bytes(total_bytes)}")
    log(f"Pending: {pending_count} files, {human_bytes(pending_bytes)}")
    log(f"Manifest: {MANIFEST_PATH}")

    token = os.environ.get("CEDA_ACCESS_TOKEN")
    ceda_files = [item for item in files if item.source.startswith("ceda-")]
    if args.check_auth:
        if not ceda_files:
            raise SystemExit("--check-auth requires --source all or --source ceda")
        check_ceda_auth(ceda_files[0], token)
        return 0
    if args.dry_run:
        return 0
    if ceda_files and any(not is_complete(item, verify=args.verify) for item in ceda_files) and not token:
        raise AuthenticationError(
            "CEDA_ACCESS_TOKEN is not set. Run with --dry-run without a token, or export a token before downloading."
        )

    pending = [item for item in files if not is_complete(item, verify=args.verify)]
    if not pending:
        log("Everything is already downloaded.")
        return 0

    started = time.monotonic()
    completed = 0
    failures: list[tuple[SourceFile, BaseException]] = []
    log(f"Starting {len(pending)} downloads with {args.workers} workers...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_item = {
            executor.submit(download_with_retries, item, token, args.verify, args.retries): item
            for item in pending
        }
        for future in concurrent.futures.as_completed(future_to_item):
            item = future_to_item[future]
            try:
                message = future.result()
                completed += 1
                elapsed = time.monotonic() - started
                log(f"[{completed}/{len(pending)}] {message} ({elapsed / 60:.1f} min elapsed)")
            except BaseException as error:
                failures.append((item, error))
                log(f"FAILED {item.path.name}: {type(error).__name__}: {error}")
                if isinstance(error, AuthenticationError):
                    for other in future_to_item:
                        other.cancel()
                    break

    if failures:
        log(f"{len(failures)} download(s) failed. Partial .part files are retained for resumption.")
        for item, error in failures:
            log(f"  {item.path.name}: {error}")
        return 1

    elapsed = time.monotonic() - started
    log(f"Download complete: {completed} files in {elapsed / 60:.1f} minutes")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuthenticationError as error:
        print(f"Authentication error: {error}", file=sys.stderr)
        raise SystemExit(2)
