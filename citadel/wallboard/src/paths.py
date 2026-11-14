"""Canonical, overridable paths for wallboard configuration and runtime state."""

import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = Path(os.environ.get("WALLBOARD_CONFIG_DIR", ROOT_DIR / "config"))
VAR_DIR = Path(os.environ.get("WALLBOARD_VAR_DIR", ROOT_DIR / "var"))
DATA_DIR = VAR_DIR / "data"
CERT_DIR = VAR_DIR / "certs"
CURRENT_PATH = VAR_DIR / "current"
ROTATION_PATH = CONFIG_DIR / "rotation.json"
SCHEDULE_PATH = CONFIG_DIR / "schedule.json"
KANBN_ENV_FILE = Path(
    os.environ.get("KANBN_ENV_FILE", ROOT_DIR.parent / "kanbn" / ".env")
)
