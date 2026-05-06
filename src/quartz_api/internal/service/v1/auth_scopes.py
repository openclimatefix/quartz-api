"""Permission constants for the v1 API."""

# Permissions that bypass per-country checks and grant access to all countries.
ALL_COUNTRY_PERMISSIONS: frozenset[str] = frozenset({"read:trial", "read:partner"})

ADMIN_PERMISSION = "ocf:admin"
