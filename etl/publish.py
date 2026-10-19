"""Publish NYC Lyft day-ahead demand forecasts to a hosted feature service.

Usage:
    python etl/publish.py --start 2025-11-10 --days 7
"""
import argparse
import json
from pathlib import Path

import pandas as pd
from arcgis.features import FeatureLayerCollection
from shapely.geometry import shape
from shapely.geometry.polygon import orient

from common import SERVICE_NAME, get_gis
from schema import HOURLY_LAYER, ZONES_LAYER

DATA = Path(__file__).resolve().parent.parent / "data"
BATCH_SIZE = 2000


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2025-11-10", help="First local day (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=7, help="Number of days to publish")
    return parser.parse_args()


def load_window(start: str, days: int) -> pd.DataFrame:
    fc = pd.read_parquet(DATA / "forecasts_q4_2025.parquet")
    t0 = pd.Timestamp(start)
    t1 = t0 + pd.Timedelta(days=days)
    window = fc[(fc["ts_local"] >= t0) & (fc["ts_local"] < t1)].copy()
    if window.empty:
        raise SystemExit(f"No forecasts between {t0} and {t1}; data covers Oct-Dec 2025.")
    return window


def load_zones() -> list[dict]:
    with open(DATA / "zones.geojson") as f:
        return json.load(f)["features"]


def esri_polygon(geojson_geometry: dict) -> dict:
    """Convert a GeoJSON (Multi)Polygon to Esri JSON rings."""
    geom = shape(geojson_geometry)
    polygons = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    rings = []
    for poly in polygons:
        poly = orient(poly, sign=-1.0)  # Esri: outer rings clockwise, holes counter-clockwise
        rings.append([[round(x, 6), round(y, 6)] for x, y in poly.exterior.coords])
        for hole in poly.interiors:
            rings.append([[round(x, 6), round(y, 6)] for x, y in hole.coords])
    return {"rings": rings, "spatialReference": {"wkid": 4326}}


def build_zone_features(zone_feats: list[dict], window: pd.DataFrame) -> list[dict]:
    totals = window.groupby("zone_id").agg(predicted_total=("predicted", "sum"),
                                           actual_total=("actual", "sum"))
    features = []
    for feat in zone_feats:
        props = feat["properties"]
        zone_id = int(props["zone_id"])
        if zone_id not in totals.index:
            continue
        pred = float(totals.at[zone_id, "predicted_total"])
        act = int(totals.at[zone_id, "actual_total"])
        features.append({
            "geometry": esri_polygon(feat["geometry"]),
            "attributes": {
                "zone_id": zone_id,
                "zone": props["zone"],
                "borough": props["borough"],
                "predicted_total": round(pred, 1),
                "actual_total": act,
                "error_pct": round((pred - act) / max(act, 1) * 100, 1),
            },
        })
    return features


def build_point_features(zone_feats: list[dict], window: pd.DataFrame) -> list[dict]:
    info = pd.DataFrame([{
        "zone_id": int(f["properties"]["zone_id"]),
        "zone": f["properties"]["zone"],
        "borough": f["properties"]["borough"],
        "lon": f["properties"]["lon"],
        "lat": f["properties"]["lat"],
    } for f in zone_feats])
    df = window.merge(info, on="zone_id")
    # ArcGIS date fields expect milliseconds since the Unix epoch (UTC)
    df["ts_ms"] = (df["ts_utc"] - pd.Timestamp("1970-01-01")) // pd.Timedelta(milliseconds=1)
    df["error"] = (df["predicted"] - df["actual"]).round(1)
    return [{
        "geometry": {"x": r.lon, "y": r.lat, "spatialReference": {"wkid": 4326}},
        "attributes": {
            "zone_id": int(r.zone_id),
            "zone": r.zone,
            "borough": r.borough,
            "ts_utc": int(r.ts_ms),
            "hour_local": int(r.ts_local.hour),
            "predicted": float(r.predicted),
            "actual": int(r.actual),
            "error": float(r.error),
        },
    } for r in df.itertuples()]


def get_or_create_service(gis):
    """Return the existing service item, or create it with our schema."""
    owner = gis.users.me.username
    for item in gis.content.search(f'title:"{SERVICE_NAME}" AND owner:{owner}',
                                   item_type="Feature Layer"):
        if item.title == SERVICE_NAME:
            return item, False

    item = gis.content.create_service(
        name=SERVICE_NAME,
        service_description="NYC Lyft day-ahead demand forecasts by taxi zone",
        wkid=4326,
        capabilities="Create,Delete,Query,Update,Editing",
    )
    result = FeatureLayerCollection.fromitem(item).manager.add_to_definition(
        {"layers": [ZONES_LAYER, HOURLY_LAYER]}
    )
    if not result.get("success"):
        raise RuntimeError(f"Could not add layers: {result}")
    return gis.content.get(item.id), True


def upload(layer, features: list[dict], label: str) -> None:
    """Add features in batches and fail loudly if any row is rejected."""
    done = 0
    for i in range(0, len(features), BATCH_SIZE):
        result = layer.edit_features(adds=features[i:i + BATCH_SIZE])
        failed = [r for r in result.get("addResults", []) if not r.get("success")]
        if failed:
            raise RuntimeError(f"{label}: {len(failed)} features rejected, e.g. {failed[0]}")
        done += len(result.get("addResults", []))
        print(f"  {label}: {done:,}/{len(features):,}", end="\r")
    print()


def main():
    args = parse_args()
    window = load_window(args.start, args.days)
    zone_feats = load_zones()
    zones = build_zone_features(zone_feats, window)
    points = build_point_features(zone_feats, window)
    print(f"Window {args.start} + {args.days} day(s): {len(zones)} zones, {len(points):,} hourly points")

    gis = get_gis()
    item, created = get_or_create_service(gis)
    print(f"{'Created' if created else 'Found'} service: {item.title} ({item.id})")
    zones_layer, hourly_layer = item.layers[0], item.layers[1]

    # Empty the layers first so re-running the script never duplicates rows
    zones_layer.manager.truncate()
    hourly_layer.manager.truncate()

    upload(zones_layer, zones, "zones")
    upload(hourly_layer, points, "hourly")

    # Verify what actually landed on the server
    n_zones = zones_layer.query(return_count_only=True)
    n_points = hourly_layer.query(return_count_only=True)
    print(f"Server counts: zones={n_zones}, hourly={n_points:,}")
    if (n_zones, n_points) != (len(zones), len(points)):
        raise RuntimeError("Server counts do not match the uploaded data")


if __name__ == "__main__":
    main()