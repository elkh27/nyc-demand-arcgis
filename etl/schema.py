"""Layer definitions for the hosted feature service (ArcGIS REST JSON)."""

NYC_EXTENT = {
    "xmin": -74.27, "ymin": 40.49, "xmax": -73.68, "ymax": 40.92,
    "spatialReference": {"wkid": 4326},
}


def _oid():
    return {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID",
            "nullable": False, "editable": False}


def _field(name, ftype, alias, length=None):
    field = {"name": name, "type": ftype, "alias": alias, "nullable": True, "editable": True}
    if length:
        field["length"] = length
    return field


ZONES_LAYER = {
    "id": 0,
    "name": "zones",
    "type": "Feature Layer",
    "geometryType": "esriGeometryPolygon",
    "objectIdField": "OBJECTID",
    "extent": NYC_EXTENT,
    "fields": [
        _oid(),
        _field("zone_id", "esriFieldTypeInteger", "Zone ID"),
        _field("zone", "esriFieldTypeString", "Zone", 100),
        _field("borough", "esriFieldTypeString", "Borough", 50),
        _field("predicted_total", "esriFieldTypeDouble", "Predicted requests (window)"),
        _field("actual_total", "esriFieldTypeInteger", "Actual requests (window)"),
        _field("error_pct", "esriFieldTypeDouble", "Forecast error (%)"),
    ],
}

HOURLY_LAYER = {
    "id": 1,
    "name": "hourly_forecast",
    "type": "Feature Layer",
    "geometryType": "esriGeometryPoint",
    "objectIdField": "OBJECTID",
    "extent": NYC_EXTENT,
    "fields": [
        _oid(),
        _field("zone_id", "esriFieldTypeInteger", "Zone ID"),
        _field("zone", "esriFieldTypeString", "Zone", 100),
        _field("borough", "esriFieldTypeString", "Borough", 50),
        _field("ts_utc", "esriFieldTypeDate", "Hour (UTC)"),
        _field("hour_local", "esriFieldTypeInteger", "Hour of day (NYC)"),
        _field("predicted", "esriFieldTypeDouble", "Predicted requests"),
        _field("actual", "esriFieldTypeInteger", "Actual requests"),
        _field("error", "esriFieldTypeDouble", "Error (predicted - actual)"),
    ],
    # Indexes speed up the time-slider and per-zone queries in the web app
    "indexes": [
        {"name": "idx_ts_utc", "fields": "ts_utc", "isAscending": True, "isUnique": False, "description": ""},
        {"name": "idx_zone_id", "fields": "zone_id", "isAscending": True, "isUnique": False, "description": ""},
    ],
}