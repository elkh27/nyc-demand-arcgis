"""Print the account details that decide what this user is allowed to do."""
from common import get_gis

gis = get_gis()
me = gis.users.me
print("username:  ", me.username)
print("orgId:     ", me.get("orgId"))
print("role:      ", me.get("role"))
print("privileges:", me.get("privileges"))
print("org URL key:", gis.properties.get("urlKey"))
