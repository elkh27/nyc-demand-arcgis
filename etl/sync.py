"""Daily sync: keep a rolling 7-day forecast window on the hosted layer.

The data only covers Oct-Dec 2025, so a simulated clock maps the real date onto
that period. The window holds 6 days with known actuals (ending "today") plus
tomorrow's forecast with no actuals yet. Each run reconciles the server with the
desired window:
  - already up to date   -> no-op (safe to re-run)
  - one day behind       -> incremental update (small edit payload)
  - anything else        -> full rebuild

Usage:
    python etl/sync.py                       # simulated date from today's date
    SIM_TODAY=2025-11-14 python etl/sync.py  # pin the simulated date
"""
import os
from datetime import date, timedelta

import pandas as pd

from common import get_gis
from publish import DATA, esri_polygon, get_or_create_service, load_zones, upload
from time_settings import apply_time_info

TZ = "America/New_York"
DATA_FIRST_DAY = date(2025, 10, 1)
DATA_LAST_DAY = date(2025, 12, 31)
KNOWN_DAYS = 6                 # days with actuals, ending with "today"
ANCHOR = date(2026, 10, 21)    # real date mapped to the first simulated day

FIRST_SIM_DAY = DATA_FIRST_DAY + timedelta(days=KNOWN_DAYS - 1)
LAST_SIM_DAY = DATA_LAST_DAY - timedelta(days=1)   # tomorrow must exist in the data


# ---------- Simulated clock ----------
def simulated_today() -> date:
    override = os.getenv("SIM_TODAY")
    if override:
        day = date.fromisoformat(override)
        if not FIRST_SIM_DAY <= day <= LAST_SIM_DAY:
            raise SystemExit(f"SIM_TODAY must be between {FIRST_SIM_DAY} and {LAST_SIM_DAY}")
        return day
    span = (LAST_SIM_DAY - FIRST_SIM_DAY).days + 1
    offset = (date.today() - ANCHOR).days % span
    return FIRST_SIM_DAY + timedelta(days=offset)


def window_days(today: date) -> tuple[date, date]:
    """First and last local day of the window (inclusive)."""
    return today - timedelta(days=KNOWN_DAYS - 1), today + timedelta(days=1)


# ---------- Desired state ----------
def to_ms(ts: pd.Series) -> pd.Series:
    return (ts - pd.Timestamp("1970-01-01")) // pd.Timedelta(milliseconds=1)


def ms_to_local_day(ms: int) -> date:
    return pd.Timestamp(ms, unit="ms", tz="UTC").tz_convert(TZ).date()


def utc_sql(day: date) -> str:
    """SQL timestamp literal (UTC) for local midnight of a day."""
    ts = pd.Timestamp(day).tz_localize(TZ).tz_convert("UTC")
    return f"TIMESTAMP '{ts:%Y-%m-%d %H:%M:%S}'"


def desired_rows(today: date, zone_info: pd.DataFrame) -> pd.DataFrame:
    first, last = window_days(today)
    fc = pd.read_parquet(DATA / "forecasts_q4_2025.parquet")
    df = fc[(fc["ts_local"].dt.date >= first) & (fc["ts_local"].dt.date <= last)].copy()
    df = df.merge(zone_info, on="zone_id")
    df["ts_ms"] = to_ms(df["ts_utc"])

    # Tomorrow has not happened yet: hide its actual demand
    future = df["ts_local"].dt.date > today
    df["actual"] = df["actual"].astype("Int64").mask(future)
    df["error"] = (df["predicted"] - df["actual"]).round(1)
    df["known"] = ~future
    return df


def point_features(df: pd.DataFrame) -> list[dict]:
    def clean(v):
        return None if pd.isna(v) else v
    return [{
        "geometry": {"x": r.lon, "y": r.lat, "spatialReference": {"wkid": 4326}},
        "attributes": {
            "zone_id": int(r.zone_id), "zone": r.zone, "borough": r.borough,
            "ts_utc": int(r.ts_ms), "hour_local": int(r.ts_local.hour),
            "predicted": float(r.predicted),
            "actual": None if pd.isna(r.actual) else int(r.actual),
            "error": clean(r.error),
        },
    } for r in df.itertuples()]


def zone_totals(df: pd.DataFrame) -> dict[int, dict]:
    """Totals over the days with known actuals."""
    known = df[df["known"]]
    totals = known.groupby("zone_id").agg(pred=("predicted", "sum"), act=("actual", "sum"))
    return {
        int(z): {
            "predicted_total": round(float(t.pred), 1),
            "actual_total": int(t.act),
            "error_pct": round((t.pred - t.act) / max(int(t.act), 1) * 100, 1),
        }
        for z, t in totals.iterrows()
    }


# ---------- Server state ----------
def server_state(layer) -> tuple:
    res = layer.query(where="1=1", out_statistics=[
        {"statisticType": "min", "onStatisticField": "ts_utc", "outStatisticFieldName": "t_min"},
        {"statisticType": "max", "onStatisticField": "ts_utc", "outStatisticFieldName": "t_max"},
        {"statisticType": "count", "onStatisticField": "OBJECTID", "outStatisticFieldName": "n"},
    ])
    a = res.features[0].attributes
    if not a.get("n"):
        return None, None, 0
    return ms_to_local_day(a["t_min"]), ms_to_local_day(a["t_max"]), int(a["n"])


def check_edits(result: dict, key: str, label: str) -> int:
    rows = result.get(key, [])
    failed = [r for r in rows if not r.get("success")]
    if failed:
        raise RuntimeError(f"{label}: {len(failed)} edits failed, e.g. {failed[0]}")
    return len(rows)


# ---------- Update paths ----------
def full_rebuild(zones_layer, hourly_layer, df, zone_feats):
    print("Full rebuild")
    totals = zone_totals(df)
    zones = [{
        "geometry": esri_polygon(f["geometry"]),
        "attributes": {"zone_id": int(f["properties"]["zone_id"]),
                       "zone": f["properties"]["zone"],
                       "borough": f["properties"]["borough"],
                       **totals[int(f["properties"]["zone_id"])]},
    } for f in zone_feats if int(f["properties"]["zone_id"]) in totals]

    zones_layer.manager.truncate()
    hourly_layer.manager.truncate()
    upload(zones_layer, zones, "zones")
    upload(hourly_layer, point_features(df), "hourly")


def incremental_update(zones_layer, hourly_layer, df, today: date):
    print("Incremental update")
    first, _ = window_days(today)

    # 1. Drop the day that fell out of the window
    res = hourly_layer.delete_features(where=f"ts_utc < {utc_sql(first)}")
    print(f"  deleted: {check_edits(res, 'deleteResults', 'delete')}")

    # 2. Fill in today's actuals on rows that were published as tomorrow yesterday
    today_rows = df[df["ts_local"].dt.date == today].set_index(["zone_id", "ts_ms"])
    existing = hourly_layer.query(
        where=f"ts_utc >= {utc_sql(today)} AND ts_utc < {utc_sql(today + timedelta(days=1))}",
        out_fields="OBJECTID,zone_id,ts_utc", return_geometry=False,
    )
    updates = []
    for f in existing.features:
        a = f.attributes
        row = today_rows.loc[(a["zone_id"], a["ts_utc"])]
        updates.append({"attributes": {"OBJECTID": a["OBJECTID"],
                                       "actual": int(row["actual"]),
                                       "error": float(row["error"])}})
    for i in range(0, len(updates), 2000):
        check_edits(hourly_layer.edit_features(updates=updates[i:i + 2000]), "updateResults", "update")
    print(f"  updated actuals: {len(updates):,}")

    # 3. Add tomorrow's forecasts (no actuals yet)
    upload(hourly_layer, point_features(df[df["ts_local"].dt.date == today + timedelta(days=1)]), "added")

    # 4. Refresh zone totals (attributes only, no geometry)
    totals = zone_totals(df)
    zone_rows = zones_layer.query(where="1=1", out_fields="OBJECTID,zone_id", return_geometry=False)
    zone_updates = [{"attributes": {"OBJECTID": f.attributes["OBJECTID"], **totals[f.attributes["zone_id"]]}}
                    for f in zone_rows.features if f.attributes["zone_id"] in totals]
    check_edits(zones_layer.edit_features(updates=zone_updates), "updateResults", "zones")
    print(f"  zone totals: {len(zone_updates)}")


# ---------- Main ----------
def main():
    today = simulated_today()
    first, last = window_days(today)
    print(f"Simulated today: {today} | window {first} -> {last} (tomorrow has no actuals)")

    zone_feats = load_zones()
    zone_info = pd.DataFrame([{k: f["properties"][k] for k in ("zone_id", "zone", "borough", "lon", "lat")}
                              for f in zone_feats])
    df = desired_rows(today, zone_info)

    gis = get_gis()
    item, _ = get_or_create_service(gis)
    zones_layer, hourly_layer = item.layers[0], item.layers[1]

    s_first, s_last, s_count = server_state(hourly_layer)
    print(f"Server window: {s_first} -> {s_last}, {s_count:,} rows")

    if (s_first, s_last, s_count) == (first, last, len(df)):
        print("Already up to date, nothing to do")
        return
    if (s_first, s_last) == (first - timedelta(days=1), last - timedelta(days=1)):
        incremental_update(zones_layer, hourly_layer, df, today)
    else:
        full_rebuild(zones_layer, hourly_layer, df, zone_feats)

    apply_time_info(hourly_layer)

    # Verify the server now matches the desired state
    final = server_state(hourly_layer)
    if final != (first, last, len(df)):
        raise RuntimeError(f"Server state {final} does not match desired {(first, last, len(df))}")
    print(f"Done: {final[2]:,} rows, {first} -> {last}")


if __name__ == "__main__":
    main()