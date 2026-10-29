#!/usr/bin/env python3
"""Legacy entry point: generate the VERIFIED DATA notebook, never synthetic outcomes.
Original synthetic design is archived at archive/synthetic-design-20260928/.
"""
from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).with_name("revision") / "build_revision_notebook.py"), run_name="__main__")
