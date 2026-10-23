"""piMap 2.0 settings: the same config.py piMap has always used.

Looked for, in order:
  PIMAP_CONFIG_DIR/config.py   (the FieldLab kiosk mounts its own here)
  <piMap folder>/config.py     (the shack Pi: unchanged)
  config.example.py            with QRZ off, so the map still runs before setup

piMap's data modules (sat_tracker, space_weather, ...) do `import config`, so whichever
is found is registered as the `config` module before they're imported.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from types import ModuleType

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Salina, KS: used only when no settings give a home at all.
DEFAULT_HOME = (38.8403, -97.6114)


def load_config() -> tuple[ModuleType, str]:
    """(config module, where it came from: 'own' or 'example')."""
    if "config" in sys.modules and getattr(sys.modules["config"], "_pimap2_source", None):
        cfg = sys.modules["config"]
        return cfg, cfg._pimap2_source
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    candidates = []
    if os.environ.get("PIMAP_CONFIG_DIR"):
        candidates.append(os.path.join(os.environ["PIMAP_CONFIG_DIR"], "config.py"))
    candidates.append(os.path.join(ROOT, "config.py"))
    path = next((p for p in candidates if os.path.isfile(p)), None)
    source = "own" if path else "example"
    spec = importlib.util.spec_from_file_location("config", path or os.path.join(ROOT, "config.example.py"))
    cfg = importlib.util.module_from_spec(spec)
    sys.modules["config"] = cfg
    spec.loader.exec_module(cfg)
    if source == "example":
        cfg.QRZ_ENABLE = False
        cfg.HOME_LAT, cfg.HOME_LON = DEFAULT_HOME
    cfg._pimap2_source = source
    return cfg, source


def get(cfg: ModuleType, name: str, default):
    return getattr(cfg, name, default)
