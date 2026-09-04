#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DIST_DIR = ROOT / "dist"
TEMPLATE_PATH = ROOT / "templates" / "goldilocks.html"
APP_TS = ROOT / "src" / "app.ts"
APP_JS = DIST_DIR / "app.js"
OUTPUT_HTML = DIST_DIR / "goldilocks.html"
DEFAULT_MANIFEST = DATA_DIR / "derived" / "climate-metrics" / "manifest.json"


def load_metric_bundle(manifest_path: Path) -> dict:
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1:
        raise RuntimeError(f"Unsupported climate metric manifest format: {manifest.get('format_version')!r}")
    metrics = manifest.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise RuntimeError(f"Metric manifest has no metrics: {manifest_path}")

    embedded_metrics = []
    for metric in metrics:
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
        "format_version": 2,
        "grid": manifest["grid"],
        "metrics": embedded_metrics,
        "default_metric_id": embedded_metrics[0]["id"],
        "sources": manifest.get("sources", {}),
        "preview_partial_sources": bool(manifest.get("preview_partial_sources", False)),
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
    if template.count("__GOLDILOCKS_DATA__") != 1 or template.count("__GOLDILOCKS_APP_JS__") != 1:
        raise RuntimeError("Template must contain each build placeholder exactly once")
    html = template.replace("__GOLDILOCKS_DATA__", data_json).replace("__GOLDILOCKS_APP_JS__", app_js)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    APP_JS.unlink(missing_ok=True)
    print(f"Wrote {OUTPUT_HTML} ({OUTPUT_HTML.stat().st_size:,} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Goldilocks Map as a single HTML file.")
    parser.add_argument(
        "--metrics-manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"processed metric manifest to embed (default: {DEFAULT_MANIFEST.relative_to(ROOT)})",
    )
    args = parser.parse_args()
    if not args.metrics_manifest.is_file():
        raise SystemExit(
            f"Processed metric manifest not found: {args.metrics_manifest}. "
            "Run process_climate_metrics.py first, or pass --metrics-manifest for a preview bundle."
        )
    print(f"Loading processed climate metrics: {args.metrics_manifest}")
    data = load_metric_bundle(args.metrics_manifest)
    app_js = bundle_typescript()
    render_html(data, app_js)
    print("Build complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
