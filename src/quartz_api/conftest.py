"""Test-wide setup that has to run before any quartz_api module is imported."""

import os

# country_config filters the v1 catalogue from these at import time. Tests assert on
# every country and model, so a deployment scope in the shell must not leak in.
os.environ.pop("V1_COUNTRIES", None)
os.environ["V1_STAGE"] = "development"
