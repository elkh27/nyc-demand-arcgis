"""Shared helpers for the ETL scripts."""
import os
from getpass import getpass

from arcgis.gis import GIS

PORTAL_URL = "https://www.arcgis.com"
SERVICE_NAME = "nyc_lyft_demand"


def get_gis() -> GIS:
    """Sign in with environment variables (used in CI) or interactive prompts (local)."""
    username = os.getenv("ARCGIS_USERNAME") or input("Username: ")
    password = os.getenv("ARCGIS_PASSWORD") or getpass("Password: ")
    return GIS(PORTAL_URL, username, password)