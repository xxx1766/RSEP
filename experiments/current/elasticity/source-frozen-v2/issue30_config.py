"""Portable host and path configuration for the elasticity campaign."""
import json
import os
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parents[1]
REMOTE = os.environ.get("RSEP_ELASTICITY_REMOTE_ROOT", "/var/lib/rsep/issue30")
OLD = os.environ.get("RSEP_ELASTICITY_OLD_ROOT", "/var/lib/rsep/historical-threefig")

def _json_env(name, default):
    raw = os.environ.get(name)
    return json.loads(raw) if raw else default

HOSTS = _json_env("RSEP_ELASTICITY_HOSTS", {"NJ-k1": "198.51.100.11", "NJ-k2": "198.51.100.12", "NJ-k3": "198.51.100.13"})
SSH_ALIASES = _json_env("RSEP_ELASTICITY_SSH_ALIASES", {name: name for name in HOSTS})
NODES = [(name, alias, role, HOSTS[name]) for name, alias, role in [
    ("NJ-k1", "threefig-nj1", "cloud"),
    ("NJ-k2", "threefig-nj2", "origin"),
    ("NJ-k3", "threefig-nj3", "edge"),
]]
KNOWN_HOSTS_FILE = Path(os.environ.get("RSEP_ELASTICITY_KNOWN_HOSTS", str(Path(tempfile.gettempdir()) / "rsep-issue20-known-hosts")))
OBSERVE_HOSTS = os.environ.get(
    "RSEP_ELASTICITY_OBSERVE_HOSTS",
    ",".join(HOSTS[name] for name in ("NJ-k1", "NJ-k2", "NJ-k3")),
).split(",")
