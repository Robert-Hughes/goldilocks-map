#!/usr/bin/env python3
"""Compare Goldilocks model-route CSV output with a Google reference snapshot."""
from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def metrics(rows: list[dict]) -> dict:
    errors = [row["error_min"] for row in rows]
    absolute = [abs(value) for value in errors]
    distance_errors = [row["distance_error_pct"] for row in rows]
    return {
        "n": len(rows),
        "bias_min": statistics.mean(errors),
        "mae_min": statistics.mean(absolute),
        "median_ae_min": statistics.median(absolute),
        "p90_ae_min": percentile(absolute, 0.90),
        "p95_ae_min": percentile(absolute, 0.95),
        "within_10": sum(value <= 10 for value in absolute),
        "within_15": sum(value <= 15 for value in absolute),
        "distance_mae_pct": statistics.mean(distance_errors),
    }


def fmt(label: str, result: dict) -> str:
    return (
        f"{label:30s} n={result['n']:3d}  MAE={result['mae_min']:5.2f} min  "
        f"bias={result['bias_min']:+5.2f}  p90={result['p90_ae_min']:5.1f}  "
        f"within15={result['within_15']:3d}/{result['n']}  dist={result['distance_mae_pct']:4.2f}%"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path, help="CSV with variant, origin, destination, minutes and distance_miles")
    parser.add_argument("reference", type=Path, help="CSV emitted by collect-google-reference.py")
    args = parser.parse_args()

    reference = {}
    with args.reference.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if not row.get("google_minutes") or not row.get("google_miles"):
                continue
            reference[(row["origin"], row["destination"])] = row

    variants: dict[str, list[dict]] = defaultdict(list)
    with args.model.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = (row["origin"], row["destination"])
            ref = reference.get(key)
            if ref is None:
                continue
            model_minutes = float(row["minutes"])
            model_miles = float(row["distance_miles"])
            google_minutes = float(ref["google_minutes"])
            google_miles = float(ref["google_miles"])
            variants[row.get("variant") or "model"].append(
                {
                    **row,
                    "traffic_label": ref.get("traffic_label", "unspecified"),
                    "error_min": model_minutes - google_minutes,
                    "distance_error_pct": abs(model_miles - google_miles) / google_miles * 100.0,
                }
            )

    for variant, rows in sorted(variants.items()):
        print("\n" + variant)
        print(fmt("all", metrics(rows)))
        for destination in ("York", "Cambridge"):
            subset = [row for row in rows if row["destination"] == destination]
            if subset:
                print(fmt(destination, metrics(subset)))
        for traffic in ("usual-traffic", "current-traffic", "unspecified"):
            subset = [row for row in rows if row["traffic_label"] == traffic]
            if subset:
                print(fmt(traffic, metrics(subset)))
        regions = sorted({row.get("region", "") for row in rows if row.get("region")})
        for region in regions:
            subset = [row for row in rows if row.get("region") == region]
            print(fmt(region, metrics(subset)))
        worst = sorted(rows, key=lambda row: abs(row["error_min"]), reverse=True)[:8]
        print("  worst absolute time errors:")
        for row in worst:
            print(
                f"    {row['origin']} -> {row['destination']}: {row['error_min']:+.1f} min; "
                f"distance error {row['distance_error_pct']:.1f}%; {row['traffic_label']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
