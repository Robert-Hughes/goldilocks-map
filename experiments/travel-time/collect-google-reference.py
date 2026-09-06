#!/usr/bin/env python3
"""Prepare a local worksheet for manual Google Maps comparison.

This optional experiment helper does not fetch Google Maps, automate a browser,
or extract/store Google Maps content. It only turns the reviewed origin list
into ordinary Google Maps directions URLs plus empty columns for local manual
QA. Do not commit or redistribute populated Google-reference results.
"""
from __future__ import annotations

import argparse
import csv
import urllib.parse
from pathlib import Path

TARGETS = (("York", "York, UK"), ("Cambridge", "Cambridge, UK"))
FIELDS = (
    "origin",
    "region",
    "destination",
    "google_minutes",
    "google_miles",
    "traffic_label",
    "captured_local",
    "notes",
    "url",
)


def directions_url(origin: str, destination: str) -> str:
    return (
        "https://www.google.com/maps/dir/"
        + urllib.parse.quote(origin, safe="")
        + "/"
        + urllib.parse.quote(destination, safe="")
        + "/data=!4m2!4m1!3e0"
    )


def main() -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Prepare a local CSV of Google Maps directions URLs for manual travel-model QA."
    )
    parser.add_argument("--origins", type=Path, default=here / "validation-origins.csv")
    parser.add_argument("--output", type=Path, default=Path("/tmp/goldilocks-google-reference.csv"))
    args = parser.parse_args()

    with args.origins.open(newline="", encoding="utf-8") as handle:
        origins = list(csv.DictReader(handle))

    rows = []
    for origin in origins:
        for destination_name, destination_query in TARGETS:
            rows.append(
                {
                    "origin": origin["name"],
                    "region": origin["region"],
                    "destination": destination_name,
                    "google_minutes": "",
                    "google_miles": "",
                    "traffic_label": "",
                    "captured_local": "",
                    "notes": "",
                    "url": directions_url(origin["query"], destination_query),
                }
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {args.output} with {len(rows)} direction URLs.")
    print("Open URLs manually and keep any populated Google comparison results local/uncommitted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())