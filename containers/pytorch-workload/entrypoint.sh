#!/bin/sh
set -eu
mode="${1:-${WORKLOAD_MODE:-native}}"
case "$mode" in
    native|http) worker="/legacy/source/workload/${mode}_worker.py" ;;
    *) echo "unknown workload mode: $mode" >&2; exit 2 ;;
esac
exec python3 -u -c 'import os,runpy,sys,torch; torch.set_num_interop_threads(int(os.environ.get("TIDE_TORCH_INTEROP_THREADS", "8"))); runpy.run_path(sys.argv[1], run_name="__main__")' "$worker"
