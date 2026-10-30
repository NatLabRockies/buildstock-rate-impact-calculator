"""Build the county and PUMA boundary files bundled for the app's maps.

Downloads the Census Bureau's 2019 cartographic boundary shapefiles (1:500k)
and writes simplified GeoJSON to ``src/buildstock_rate_impact_calculator/data``:

- ``us_counties.geojson``: counties (STATEFP, COUNTYFP, NAME)
- ``us_pumas_2010.geojson``: 2010 PUMAs, as ResStock/ComStock use
  (STATEFP10, PUMACE10, NAME10)

The maps only draw these shapes; clicks are resolved from the tooltip's
GISJOIN, so geometry detail beyond what is visible is wasted download and
browser time. Simplification is topology-aware (shared borders are
simplified once), so neighboring areas never gain gaps or overlaps.

Run with the extra packages it needs, outside the project environment:

    pixi exec --spec python=3.13 --spec topojson --spec shapely --spec pyshp \
        python scripts/simplify_boundaries.py
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

import shapefile  # pyshp
import topojson

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "src" / "buildstock_rate_impact_calculator" / "data"
CENSUS_URL = "https://www2.census.gov/geo/tiger/GENZ2019/shp/{name}.zip"

# (Census file, output file, properties the app reads)
LAYERS = [
    ("cb_2019_us_county_500k", "us_counties.geojson", ["STATEFP", "COUNTYFP", "NAME"]),
    (
        "cb_2019_us_puma10_500k",
        "us_pumas_2010.geojson",
        ["STATEFP10", "PUMACE10", "NAME10"],
    ),
]

# Douglas-Peucker tolerance in degrees (~250 m): outlines stay within a few
# pixels of the 1:500k originals up to zoom 12, at about a fifth of the size.
TOLERANCE = 0.0025
# 4 decimal places is ~11 m, well below the tolerance.
DECIMALS = 4


def fetch(name: str, cache_dir: Path) -> Path:
    path = cache_dir / f"{name}.zip"
    if not path.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {name}...", flush=True)
        urllib.request.urlretrieve(CENSUS_URL.format(name=name), path)
    return path


def read_features(zip_path: Path, fields: list[str]) -> list[dict]:
    with shapefile.Reader(str(zip_path)) as reader:
        return [
            {
                "type": "Feature",
                "properties": {k: rec.record[k] for k in fields},
                "geometry": rec.shape.__geo_interface__,
            }
            for rec in reader.iterShapeRecords()
        ]


def round_coords(coords):
    if isinstance(coords[0], (int, float)):
        return [round(c, DECIMALS) for c in coords]
    return [round_coords(c) for c in coords]


def simplify(features: list[dict]) -> list[dict]:
    topo = topojson.Topology(
        {"type": "FeatureCollection", "features": features},
        prequantize=False,
        toposimplify=TOLERANCE,
        prevent_oversimplify=True,
    )
    out = json.loads(topo.to_geojson())["features"]
    for feat in out:
        feat.pop("id", None)
        feat["geometry"]["coordinates"] = round_coords(feat["geometry"]["coordinates"])
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--cache", type=Path, default=REPO_ROOT / "dist" / ".cache" / "census"
    )
    args = parser.parse_args()

    for census_name, out_name, fields in LAYERS:
        features = read_features(fetch(census_name, args.cache), fields)
        simplified = simplify(features)
        if len(simplified) != len(features) or not all(
            f["geometry"] and f["geometry"]["coordinates"] for f in simplified
        ):
            raise SystemExit(f"{census_name}: simplification dropped a shape")
        out = DATA_DIR / out_name
        text = json.dumps(
            {"type": "FeatureCollection", "features": simplified},
            separators=(",", ":"),
        )
        out.write_text(text, encoding="utf-8")
        print(f"Wrote {out} ({len(simplified)} features, {len(text) / 2**20:.1f} MiB)")


if __name__ == "__main__":
    main()
