#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
from html import escape
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DIST_DIR = ROOT / "dist"
TEMPLATE_PATH = ROOT / "templates" / "goldilocks.html"
THIRD_PARTY_NOTICES_PATH = ROOT / "THIRD-PARTY-NOTICES.txt"
APP_TS = ROOT / "src" / "app.ts"
APP_JS = DIST_DIR / "app.js"
OUTPUT_HTML = DIST_DIR / "goldilocks.html"
DEFAULT_MANIFEST = DATA_DIR / "derived" / "goldilocks-metrics" / "manifest.json"
DEFAULT_SERVICES_MANIFEST = DATA_DIR / "derived" / "services" / "manifest.json"


def load_metric_bundle(manifest_path: Path) -> dict:
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != 3 or manifest.get("kind") != "goldilocks-bundle":
        raise RuntimeError(
            f"Unsupported Goldilocks metric bundle: version={manifest.get('format_version')!r}, kind={manifest.get('kind')!r}"
        )
    metrics = manifest.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise RuntimeError(f"Metric manifest has no metrics: {manifest_path}")
    categories = manifest.get("categories")
    if not isinstance(categories, list) or not categories:
        raise RuntimeError(f"Metric manifest has no categories: {manifest_path}")
    category_ids = {category.get("id") for category in categories if isinstance(category, dict)}
    if None in category_ids or len(category_ids) != len(categories):
        raise RuntimeError(f"Metric manifest has invalid/duplicate categories: {manifest_path}")
    sources = manifest.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise RuntimeError(f"Metric manifest has no data sources: {manifest_path}")
    source_ids = set(sources)

    embedded_metrics = []
    for metric in metrics:
        if metric.get("category_id") not in category_ids:
            raise RuntimeError(f"Metric {metric.get('id')!r} references unknown category {metric.get('category_id')!r}")
        if metric.get("source_id") not in source_ids:
            raise RuntimeError(f"Metric {metric.get('id')!r} references unknown source {metric.get('source_id')!r}")
        blob_name = metric.get("blob_file")
        if not blob_name:
            raise RuntimeError(f"Metric {metric.get('id')!r} has no blob_file")
        blob_path = manifest_path.parent / blob_name
        compressed = blob_path.read_bytes()
        expected_size = metric.get("compressed_bytes")
        if expected_size is not None and len(compressed) != int(expected_size):
            raise RuntimeError(
                f"Metric blob {blob_path.name} size {len(compressed)} does not match manifest {expected_size}"
            )
        raw = gzip.decompress(compressed)
        expected_raw_size = metric.get("raw_bytes")
        if expected_raw_size is not None and len(raw) != int(expected_raw_size):
            raise RuntimeError(
                f"Metric blob {blob_path.name} decodes to {len(raw)} bytes; expected {expected_raw_size}"
            )
        expected_sha = metric.get("sha256_raw")
        if expected_sha and hashlib.sha256(raw).hexdigest() != expected_sha:
            raise RuntimeError(f"Metric blob {blob_path.name} failed raw SHA-256 validation")
        embedded = dict(metric)
        embedded.pop("blob_file", None)
        embedded["blob_base64"] = base64.b64encode(compressed).decode("ascii")
        embedded_metrics.append(embedded)

    return {
        "format_version": 5,
        "grid": manifest["grid"],
        "categories": categories,
        "metrics": embedded_metrics,
        "default_metric_id": embedded_metrics[0]["id"],
        "sources": manifest.get("sources", {}),
        "data_pruning": manifest.get("data_pruning", []),
        "preview_partial_sources": bool(manifest.get("preview_partial_sources", False)),
    }


def load_service_bundle(manifest_path: Path) -> dict:
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != 2 or manifest.get("kind") != "goldilocks-services":
        raise RuntimeError(
            f"Unsupported Goldilocks service bundle: version={manifest.get('format_version')!r}, kind={manifest.get('kind')!r}"
        )
    categories = manifest.get("categories")
    if not isinstance(categories, list) or not categories:
        raise RuntimeError(f"Service manifest has no categories: {manifest_path}")
    category_ids = [item.get("id") for item in categories if isinstance(item, dict)]
    if len(category_ids) != len(categories) or len(set(category_ids)) != len(category_ids) or any(not item for item in category_ids):
        raise RuntimeError(f"Service manifest has invalid/duplicate categories: {manifest_path}")
    sources = manifest.get("sources")
    source_order = manifest.get("source_order")
    if not isinstance(sources, dict) or not isinstance(source_order, list) or any(source_id not in sources for source_id in source_order):
        raise RuntimeError(f"Service manifest has invalid sources: {manifest_path}")
    payload_name = manifest.get("payload_file")
    if not isinstance(payload_name, str) or Path(payload_name).name != payload_name:
        raise RuntimeError(f"Service manifest has invalid payload filename: {payload_name!r}")
    payload_path = manifest_path.parent / payload_name
    compressed = payload_path.read_bytes()
    if len(compressed) != int(manifest.get("compressed_bytes", -1)):
        raise RuntimeError(f"Service payload {payload_path.name} compressed size does not match manifest")
    raw = gzip.decompress(compressed)
    if len(raw) != int(manifest.get("raw_bytes", -1)):
        raise RuntimeError(f"Service payload {payload_path.name} raw size does not match manifest")
    if hashlib.sha256(raw).hexdigest() != manifest.get("sha256_raw"):
        raise RuntimeError(f"Service payload {payload_path.name} failed raw SHA-256 validation")
    json.loads(raw.decode("utf-8"))
    return {
        "format_version": 2,
        "min_zoom": int(manifest["min_zoom"]),
        "bucket_scale": int(manifest["bucket_scale"]),
        "coordinate_scale": int(manifest["coordinate_scale"]),
        "categories": categories,
        "source_order": source_order,
        "sources": sources,
        "payload_encoding": manifest["payload_encoding"],
        "raw_bytes": int(manifest["raw_bytes"]),
        "compressed_bytes": len(compressed),
        "sha256_raw": manifest["sha256_raw"],
        "blob_base64": base64.b64encode(compressed).decode("ascii"),
    }


def ensure_frontend_dependencies() -> Path:
    executable = ROOT / "node_modules" / ".bin" / "esbuild"
    required_packages = [ROOT / "node_modules" / package / "package.json" for package in ("proj4", "fflate")]
    if executable.exists() and all(path.exists() for path in required_packages):
        return executable
    print("Installing frontend build dependencies with npm...")
    subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=ROOT, check=True)
    if not executable.exists() or not all(path.exists() for path in required_packages):
        raise RuntimeError("npm install completed but required frontend dependencies are still missing")
    return executable


def bundle_typescript() -> str:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    esbuild = ensure_frontend_dependencies()
    subprocess.run(
        [
            str(esbuild),
            str(APP_TS),
            "--bundle",
            "--minify",
            "--format=iife",
            "--target=es2020",
            f"--outfile={APP_JS}",
        ],
        cwd=ROOT,
        check=True,
    )
    return APP_JS.read_text(encoding="utf-8")


def render_html(data: dict, app_js: str) -> None:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    data_json = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("<", "\\u003c")
    third_party_notices = escape(THIRD_PARTY_NOTICES_PATH.read_text(encoding="utf-8"), quote=False)
    placeholders = ("__GOLDILOCKS_DATA__", "__GOLDILOCKS_APP_JS__", "__THIRD_PARTY_NOTICES__")
    if any(template.count(placeholder) != 1 for placeholder in placeholders):
        raise RuntimeError("Template must contain each build placeholder exactly once")
    html = (
        template.replace("__GOLDILOCKS_DATA__", data_json)
        .replace("__GOLDILOCKS_APP_JS__", app_js)
        .replace("__THIRD_PARTY_NOTICES__", third_party_notices)
    )
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    APP_JS.unlink(missing_ok=True)
    print(f"Wrote {OUTPUT_HTML} ({OUTPUT_HTML.stat().st_size:,} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Goldilocks Map as a single HTML file.")
    parser.add_argument(
        "--metrics-manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"assembled metric manifest to embed (default: {DEFAULT_MANIFEST.relative_to(ROOT)})",
    )
    parser.add_argument(
        "--services-manifest",
        type=Path,
        default=DEFAULT_SERVICES_MANIFEST,
        help=f"processed service POI manifest to embed (default: {DEFAULT_SERVICES_MANIFEST.relative_to(ROOT)})",
    )
    args = parser.parse_args()
    if not args.metrics_manifest.is_file():
        raise SystemExit(
            f"Assembled metric manifest not found: {args.metrics_manifest}. "
            "Run the dataset processors and assemble_metrics.py first, or pass --metrics-manifest for another assembled bundle."
        )
    if not args.services_manifest.is_file():
        raise SystemExit(
            f"Service POI manifest not found: {args.services_manifest}. "
            "Run download_service_sources.py and process_service_pois.py first, or pass --services-manifest for another processed bundle."
        )
    print(f"Loading assembled Goldilocks metrics: {args.metrics_manifest}")
    data = load_metric_bundle(args.metrics_manifest)
    print(f"Loading processed Goldilocks services: {args.services_manifest}")
    data["services"] = load_service_bundle(args.services_manifest)
    app_js = bundle_typescript()
    render_html(data, app_js)
    print("Build complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
