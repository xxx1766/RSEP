"""Portable host, path and script configuration for the WAN pilot drivers.

No networking happens at import time. Real runs require explicit host
configuration through RSEP_WAN_HOSTS (JSON: logical name -> address) and
optionally RSEP_WAN_SSH_ALIASES (JSON: logical name -> SSH alias). The
defaults are documentation placeholders and local repository paths.
"""
import json
import os
import tempfile
from pathlib import Path

REPO_ROOT = Path(os.environ.get("RSEP_ROOT", str(Path(__file__).resolve().parents[4])))
EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
SOURCE_DIR = Path(__file__).resolve().parent
OUT = Path(os.environ.get("RSEP_WAN_OUT", str(REPO_ROOT / "results-issue29-three-pair-pilot-20261031")))
OLD = Path(os.environ.get("RSEP_WAN_OLD", str(REPO_ROOT / "results-issue23-vm-wan-20261031")))
SCRIPTS_DIR = Path(os.environ.get("RSEP_WAN_SCRIPTS", str(REPO_ROOT / "code" / "tide" / "scripts" / "revision")))
REMOTE = os.environ.get("RSEP_WAN_REMOTE_ROOT", "/var/lib/rsep/issue29-pilot")
GUEST_ROOT = os.environ.get("RSEP_WAN_GUEST_ROOT", REMOTE)
LEGACY_GUEST_ROOT = os.environ.get("RSEP_WAN_LEGACY_GUEST_ROOT", "/var/lib/rsep/issue23")
GUEST_SUBNET = os.environ.get("RSEP_WAN_GUEST_SUBNET", "10.223.23")
VNC_BIND_ADDRESS = os.environ.get("RSEP_WAN_VNC_BIND_ADDRESS", "")

def _json_env(name, default):
    raw = os.environ.get(name)
    return json.loads(raw) if raw else default

HOSTS = _json_env("RSEP_WAN_HOSTS", {"NJ-k1": "198.51.100.11", "NJ-k2": "198.51.100.12", "NJ-k3": "198.51.100.13"})
SSH_ALIASES = _json_env("RSEP_WAN_SSH_ALIASES", {name: name for name in HOSTS})
KNOWN_HOSTS_FILE = Path(os.environ.get("RSEP_WAN_KNOWN_HOSTS", str(Path(tempfile.gettempdir()) / "rsep-issue20-known-hosts")))
VM_KEY_ROOT = Path(os.environ.get("RSEP_WAN_VM_KEY_ROOT", str(Path(tempfile.gettempdir()) / "rsep-issue23-vm-keys")))
