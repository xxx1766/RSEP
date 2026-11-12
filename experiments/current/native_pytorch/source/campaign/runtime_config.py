"""Portable runtime locations for the native PyTorch campaign."""
import os
import shutil
import sys
from pathlib import Path

BASE = Path(os.environ.get("RSEP_NATIVE_EVIDENCE_ROOT", str(Path(__file__).resolve().parent)))
REPO = Path(os.environ.get("RSEP_RUNTIME_SOURCE", str(BASE / "runtime-source" / "Tide")))
LEGACY_ROOT = Path(os.environ.get("RSEP_NATIVE_LEGACY_ROOT", str(BASE.parent)))
DEPENDENCY_ROOT = Path(os.environ.get("RSEP_NATIVE_DEPENDENCY_ROOT", str(LEGACY_ROOT / "deps")))
NERDCTL = os.environ.get("NERDCTL", shutil.which("nerdctl") or "nerdctl")
PYTHON = os.environ.get("PYTHON", sys.executable)
KUBECONFIG = os.environ.get("KUBECONFIG", "/etc/kubernetes/admin.conf")
