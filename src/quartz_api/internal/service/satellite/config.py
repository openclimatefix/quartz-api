"""Static satellite config: channels, geography, ingest tuning."""


# Bounding box to crop to UK
LEFT, BOTTOM, RIGHT, TOP = -17.05, 46.49, 11.60, 63.31

# How far back to backfill missing data (in hours)
BACKFILL_HOURS = 48

# Per-channel inversion, and whether to black the channel out while the region is dark.
LAYER_CONFIG = {
    "VIS006": {"blackout": True},
    "VIS008": {"blackout": True},
    "IR_016": {"blackout": True},
    "IR_039": {},
    "IR_087": {"invert": True},
    "IR_097": {"invert": True},
    "IR_108": {"invert": True},
    "IR_120": {"invert": True},
    "IR_134": {"invert": True},
    "WV_062": {"invert": True},
    "WV_073": {"invert": True},
}

COMPOSITE_CONFIG: dict[str, list[str]] = {
    "COMPOSITE_VISIBLE": ["IR_016", "VIS008", "VIS006"],
    "COMPOSITE_INFRARED": ["IR_134", "IR_097", "IR_120", "IR_087", "IR_108"],
    "COMPOSITE_BLUE": ["WV_073", "WV_062"],
}

VALID_CHANNELS = frozenset(LAYER_CONFIG) | frozenset(COMPOSITE_CONFIG)

# Re-sign a cached entry once its presigned URL has less than this much validity
REFRESH_MARGIN_SECS = 24 * 60 * 60

# Composite blending: per-channel alpha cap (0-255) and overall layer opacity.
SAT_MAX_ALPHA = 180
SAT_OPACITY = 0.6
