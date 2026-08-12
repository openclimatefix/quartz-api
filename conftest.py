"""Test environment setup, applied before the app is imported."""

import os

os.environ.setdefault("AUTH_DISABLED", "true")
