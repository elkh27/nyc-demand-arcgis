from getpass import getpass
import arcgis
from arcgis.gis import GIS

gis = GIS("https://www.arcgis.com", input("Username: "), getpass("Password: "))
me = gis.users.me

# Location Platform accounts may not have every field ArcGIS Online users have
print("Signed in as:", me.username)
print("Role:", me.get("role"))
print("User type:", me.get("userLicenseTypeId", "n/a (Location Platform)"))
print("Org:", gis.properties.get("name"))
print("arcgis version:", arcgis.__version__)
