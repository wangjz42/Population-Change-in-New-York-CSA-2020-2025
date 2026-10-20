"""
Write nycsa_counties.geojson (county outlines for the 3D map) at higher detail.

Downloads the Census Bureau's 2023 cartographic boundary file for counties at
1:500,000 (includes the Connecticut planning regions) and keeps the counties that
appear in county_pop_estimates_csa408_wide.csv.

Requirements:  pip install pyshp requests
Usage (from the static folder):
               python make_county_boundaries.py
               python make_county_boundaries.py --res 5m     # lighter, less detail
"""

import argparse
import csv
import json
from pathlib import Path

import requests
import shapefile  # pyshp

URL = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_{res}.zip"
CACHE = Path("cache")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--res", default="500k", choices=["500k", "5m", "20m"])
    ap.add_argument("--csv", default="county_pop_estimates_csa408_wide.csv")
    ap.add_argument("--out", default="nycsa_counties.geojson")
    args = ap.parse_args()

    with open(args.csv, newline="", encoding="utf-8") as f:
        geoids = {r["geoid"] for r in csv.DictReader(f) if r["geography"] == "county"}

    CACHE.mkdir(exist_ok=True)
    zpath = CACHE / f"cb_2023_us_county_{args.res}.zip"
    if not zpath.exists():
        print(f"  downloading {zpath.name} ...")
        r = requests.get(URL.format(res=args.res), timeout=300)
        r.raise_for_status()
        zpath.write_bytes(r.content)

    feats = []
    with shapefile.Reader(str(zpath)) as shp:
        for sr in shp.iterShapeRecords():
            rec = sr.record.as_dict()
            if rec["GEOID"] not in geoids:
                continue
            geom = sr.shape.__geo_interface__
            rnd = lambda c: [round(c[0], 5), round(c[1], 5)]
            if geom["type"] == "Polygon":
                coords = [[rnd(p) for p in ring] for ring in geom["coordinates"]]
            else:
                coords = [[[rnd(p) for p in ring] for ring in poly] for poly in geom["coordinates"]]
            feats.append({"type": "Feature",
                          "properties": {"geoid": rec["GEOID"], "name": rec["NAME"]},
                          "geometry": {"type": geom["type"], "coordinates": coords}})

    missing = geoids - {f["properties"]["geoid"] for f in feats}
    if missing:
        print(f"  warning: no boundary for {', '.join(sorted(missing))}")
    out = {"type": "FeatureCollection",
           "source": f"US Census cartographic boundaries 2023, 1:{args.res}",
           "features": sorted(feats, key=lambda f: f["properties"]["geoid"])}
    Path(args.out).write_text(json.dumps(out, separators=(",", ":")))
    print(f"Wrote {args.out}: {len(feats)} counties")


if __name__ == "__main__":
    main()
