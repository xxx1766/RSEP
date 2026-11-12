# Wan experiment

The source under `source/` implements the recorded procedure. Configure `hosts.example.json` and the environment options in `source/issue29_config.py` for the reproduction hosts. Workload,resource and timing settings are listed in `../../../docs/CURRENT_ENVIRONMENT.md` and the local plan files.

Run `python tools/reproduce_current.py figures` from the repository root to redraw the included measurements. Runtime source is under `code/tide/`;the native PyTorch source variant is under `code/reproduction/runtime-issue42/Tide/`. Container and baseline instructions are in `../../../docs/REPRODUCTION.md`.
