"""Static satellite config: channels, geography, ingest tuning."""
from typing import Any

# Bounding box to crop to Europe
LEFT, BOTTOM, RIGHT, TOP = -31.3, 36.0, 36.3, 64.7

# Output pixel resolution (EPSG:3857 metres): 1 pixel = 17 km × 17 km cells.
RESOLUTION_M = 17_388

# How far back to backfill missing data (in hours)
BACKFILL_HOURS = 48

# Prefix of the raw EUMETSAT .nat files in the raw-data bucket, per satellite type.
RAW_PREFIX = {"rss": "rss/raw/", "0deg": "odegree/raw/"}

# Per-channel inversion, and whether to black the channel out while the region is dark.
LAYER_CONFIG: dict[str, dict[str, Any]] = {
    "VIS006": {"range": (0, 100), "blackout": True},
    "VIS008": {"range": (0, 100), "blackout": True},
    "IR_016": {"range": (0, 100)},
    "IR_039": {"range": (200, 340)},
    "IR_087": {"range": (190, 320), "invert": True},
    "IR_097": {"range": (215, 282), "invert": True},
    "IR_108": {"range": (190, 320), "invert": True},
    "IR_120": {"range": (190, 320), "invert": True},
    "IR_134": {"range": (195, 280), "invert": True},
    "WV_062": {"range": (200, 260), "invert": True},
    "WV_073": {"range": (200, 280), "invert": True},
}

COMPOSITE_CONFIG: dict[str, list[str]] = {
    "COMPOSITE_VISIBLE": ["IR_016", "VIS008", "VIS006"],
    "COMPOSITE_INFRARED": ["IR_134", "IR_097", "IR_120", "IR_087", "IR_108"],
    "COMPOSITE_BLUE": ["WV_073", "WV_062"],
}

VALID_CHANNELS = frozenset(LAYER_CONFIG) | frozenset(COMPOSITE_CONFIG)

# Internal at which rolling window to compute the stack (in minutes).
STACK_INTERVAL_MINUTES = 15

# Composite blending: per-channel alpha cap (0-255) and overall layer opacity.
SAT_MAX_ALPHA = 180
SAT_OPACITY = 0.6
