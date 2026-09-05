#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path
from typing import Any

from goldilocks_raster import BUNDLE_MANIFEST_VERSION, DATASET_MANIFEST_VERSION, grids_match

ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET_MANIFESTS = (
    ROOT / "data" / "derived" / "climate-metrics" / "manifest.json",
    ROOT / "data" / "derived" / "pollution-metrics" / "manifest.json",
    ROOT / "data" / "derived" / "terrain-metrics" / "manifest.json",
    ROOT / "data" / "derived" / "woodland-metrics" / "manifest.json",
)
DEFAULT_OUTPUT_DIR = ROOT / "data" / "derived" / "goldilocks-metrics"


def load_dataset(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != DATASET_MANIFEST_VERSION or manifest.get("kind") != "goldilocks-dataset":
        raise RuntimeError(
            f"Unsupported dataset manifest {path}: version={manifest.get('format_version')!r}, kind={manifest.get('kind')!r}"
        )
    if not manifest.get("dataset_id"):
        raise RuntimeError(f"Dataset manifest has no dataset_id: {path}")
    if not isinstance(manifest.get("metrics"), list) or not manifest["metrics"]:
        raise RuntimeError(f"Dataset manifest has no metrics: {path}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assemble multiple Goldilocks raster dataset manifests into one browser-ready metric bundle."
    )
    parser.add_argument(
        "--dataset-manifest",
        dest="dataset_manifests",
        action="append",
        type=Path,
        help="dataset manifest to include; may be repeated (defaults to Climate + Pollution + Terrain + Woodland)",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    manifest_paths = tuple(args.dataset_manifests or DEFAULT_DATASET_MANIFESTS)
    missing = [path for path in manifest_paths if not path.is_file()]
    if missing:
        raise SystemExit("Missing dataset manifest(s): " + ", ".join(str(path) for path in missing))
    datasets = [(path.resolve(), load_dataset(path.resolve())) for path in manifest_paths]

    canonical_grid = dict(datasets[0][1]["grid"])
    categories: dict[str, dict[str, Any]] = {}
    sources: dict[str, dict[str, Any]] = {}
    metrics: list[tuple[Path, dict[str, Any]]] = []
    data_pruning: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []
    metric_ids: set[str] = set()

    for path, dataset in datasets:
        if not grids_match(canonical_grid, dataset["grid"]):
            raise RuntimeError(f"Dataset {dataset['dataset_id']} does not use the canonical Goldilocks raster grid")
        try:
            manifest_label = str(path.relative_to(ROOT))
        except ValueError:
            manifest_label = path.name
        components.append(
            {
                "dataset_id": dataset["dataset_id"],
                "manifest": manifest_label,
                "generated_at_unix": dataset.get("generated_at_unix"),
            }
        )
        for category in dataset.get("categories", []):
            category_id = category.get("id")
            if not category_id:
                raise RuntimeError(f"Dataset {dataset['dataset_id']} has a category without an id")
            if category_id in categories and categories[category_id] != category:
                raise RuntimeError(f"Conflicting category definition for {category_id}")
            categories[category_id] = category
        for source_id, source in dataset.get("sources", {}).items():
            if source_id in sources and sources[source_id] != source:
                raise RuntimeError(f"Conflicting source definition for {source_id}")
            sources[source_id] = source
        for metric in dataset["metrics"]:
            metric_id = metric.get("id")
            if not metric_id or metric_id in metric_ids:
                raise RuntimeError(f"Duplicate or invalid metric id {metric_id!r}")
            metric_ids.add(metric_id)
            if metric.get("category_id") not in categories:
                raise RuntimeError(f"Metric {metric_id} references unknown category {metric.get('category_id')!r}")
            if metric.get("source_id") not in sources:
                raise RuntimeError(f"Metric {metric_id} references unknown source {metric.get('source_id')!r}")
            blob_name = metric.get("blob_file")
            if not isinstance(blob_name, str) or Path(blob_name).name != blob_name:
                raise RuntimeError(f"Metric {metric_id} has invalid blob filename {blob_name!r}")
            blob_path = path.parent / blob_name
            if not blob_path.is_file():
                raise RuntimeError(f"Metric {metric_id} is missing blob {blob_path}")
            metrics.append((blob_path, dict(metric)))
        data_pruning.extend(dataset.get("data_pruning", []))

    category_order = {category_id: int(category.get("order", 999)) for category_id, category in categories.items()}
    metric_order_within_category: dict[str, int] = {}
    for _, metric in metrics:
        category_id = metric["category_id"]
        metric_order_within_category.setdefault(category_id, 0)
        metric["_assembly_order"] = metric_order_within_category[category_id]
        metric_order_within_category[category_id] += 1
    metrics.sort(key=lambda pair: (category_order.get(pair[1]["category_id"], 999), pair[1]["_assembly_order"]))

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    final_metrics: list[dict[str, Any]] = []
    for blob_path, metric in metrics:
        metric.pop("_assembly_order", None)
        destination = output_dir / metric["blob_file"]
        shutil.copy2(blob_path, destination)
        expected_size = metric.get("compressed_bytes")
        if expected_size is not None and destination.stat().st_size != int(expected_size):
            raise RuntimeError(f"Copied blob {destination.name} does not match its manifest size")
        final_metrics.append(metric)

    category_list = sorted(categories.values(), key=lambda item: (int(item.get("order", 999)), str(item.get("id"))))
    manifest = {
        "format_version": BUNDLE_MANIFEST_VERSION,
        "kind": "goldilocks-bundle",
        "generated_at_unix": int(time.time()),
        "preview_partial_sources": any(bool(dataset.get("preview_partial_sources")) for _, dataset in datasets),
        "grid": canonical_grid,
        "categories": category_list,
        "metrics": final_metrics,
        "sources": sources,
        "data_pruning": data_pruning,
        "components": components,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    keep = {"manifest.json", *(metric["blob_file"] for metric in final_metrics)}
    for existing in output_dir.iterdir():
        if existing.is_file() and existing.name not in keep:
            existing.unlink()
    total = sum((output_dir / name).stat().st_size for name in keep)
    print(
        f"Assembled {len(datasets)} datasets, {len(category_list)} categories and {len(final_metrics)} metrics "
        f"into {manifest_path} ({total:,} bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
