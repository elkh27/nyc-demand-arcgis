"""Make the hourly layer time-aware so clients can filter it by time on the server.

Usage (standalone):
    python etl/time_settings.py
"""
from common import SERVICE_NAME, get_gis


def find_service(gis):
    """Return the service item owned by the signed-in user, or None."""
    owner = gis.users.me.username
    for item in gis.content.search(f'title:"{SERVICE_NAME}" AND owner:{owner}',
                                   item_type="Feature Layer"):
        if item.title == SERVICE_NAME:
            return item
    return None


def apply_time_info(layer) -> list[int]:
    """Read the min/max timestamp from the data and write timeInfo to the layer definition."""
    stats = layer.query(where="1=1", out_statistics=[
        {"statisticType": "min", "onStatisticField": "ts_utc", "outStatisticFieldName": "t_min"},
        {"statisticType": "max", "onStatisticField": "ts_utc", "outStatisticFieldName": "t_max"},
    ])
    attrs = stats.features[0].attributes
    extent = [int(attrs["t_min"]), int(attrs["t_max"])]

    result = layer.manager.update_definition({
        "timeInfo": {
            "startTimeField": "ts_utc",
            "endTimeField": None,
            "trackIdField": "zone_id",
            "timeExtent": extent,
            "timeReference": None,
            "timeInterval": 1,
            "timeIntervalUnits": "esriTimeUnitsHours",
            "exportOptions": {"useTime": True, "timeDataCumulative": False,
                              "timeOffset": None, "timeOffsetUnits": None},
            "hasLiveData": False,
        }
    })
    if not result.get("success"):
        raise RuntimeError(f"Could not set timeInfo: {result}")
    return extent


if __name__ == "__main__":
    gis = get_gis()
    item = find_service(gis)
    if item is None:
        raise SystemExit("Service not found; run etl/publish.py first.")
    t_min, t_max = apply_time_info(item.layers[1])
    print("Service URL:", item.url)
    print("Time extent (ms):", t_min, "->", t_max)