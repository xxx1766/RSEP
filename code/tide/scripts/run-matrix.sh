#!/usr/bin/env bash
# RSEP 单机延迟消融矩阵运行器
#
# 跑完整 6 格 × 5 RPS × 5 repeat = 150 次。每次生成
#   results/<cell>/rps<N>/run<k>/raw.csv
# 供 scripts/analyze.py 聚合出图。
#
# 调用约定：
#   sudo REPO_ROOT=/root/Tide scripts/run-matrix.sh [only=C1,C3]
#
# 环境变量（有默认值）：
#   REPO_ROOT        默认：该脚本上层目录
#   RESULTS_DIR      默认：$REPO_ROOT/results
#   HTTPBASE_BIN     默认：$REPO_ROOT/bin/httpbase
#   RESPTESTV2_BIN   默认：$REPO_ROOT/bin/resptestv2（Phase 3 后才存在）
#   RPS_LIST         默认："1 2 4 8 16"
#   REPEATS          默认：5
#   DURATION_SEC     默认：20
#   CELLS            默认："C1 C2 C3 C4 C5 C6"（可 only=... 覆盖）

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RESULTS_DIR="${RESULTS_DIR:-${REPO_ROOT}/results}"
HTTPBASE_BIN="${HTTPBASE_BIN:-${REPO_ROOT}/bin/httpbase}"
RESPTESTV2_BIN="${RESPTESTV2_BIN:-${REPO_ROOT}/bin/resptestv2}"
WORKLOAD_IMAGE="${WORKLOAD_IMAGE:-docker.io/tide/workload:yolox-nano}"

DURATION_SEC="${DURATION_SEC:-20}"
REPEATS="${REPEATS:-5}"
REPEAT_START="${REPEAT_START:-1}"
# Paper-exact default: RSEP §VI.B uses {1,2,4,8,16}. Heavy-workload (10 det/req)
# saturates at RPS≈4 so this range covers underload→deep saturation. Extending
# beyond 16 with heavy workload mostly produces 504s (queue >60s deadline).
# The light-workload variant (results-light/) used {1,2,4,8,16,32}.
RPS_LIST="${RPS_LIST:-1 2 4 8 16}"
CELLS="${CELLS:-C1 C2 C3 C4 C5 C6}"
# Per-request hard deadline. Heavy workload + queue at deep saturation can
# legitimately take 30-60s, so default 60s; raise via DEADLINE_SEC if needed.
DEADLINE_SEC="${DEADLINE_SEC:-60}"

# 解析 only=C1,C3 形式覆盖 CELLS
for kv in "$@"; do
  case "$kv" in
    only=*) CELLS="${kv#only=}"; CELLS="${CELLS//,/ }" ;;
  esac
done

PYTHON="${PYTHON:-python3}"
METRICS="${METRICS:-${REPO_ROOT}/scripts/revision/metrics.py}"
ACTIVE_OUT=""
OWNED_BACKEND=""

cell_config() {
  case "$1" in
    C1) echo "off off off" ;; C2) echo "on off off" ;;
    C3) echo "off off on" ;; C4) echo "on off on" ;;
    C5) echo "off on on" ;; C6) echo "on on on" ;;
    *) echo "Unknown cell $1" >&2; return 1 ;;
  esac
}

check_prereq() {
  [[ $EUID -eq 0 ]] || { echo "必须用 sudo 跑（mqueue+cgroup+containerd）"; exit 1; }
  local fstype
  fstype=$(stat -fc %T /sys/fs/cgroup)
  echo "[prereq] cgroup fstype=$fstype (v2=cgroup2fs, v1=tmpfs)"
  # mqueue sysctls
  local m n
  m=$(sysctl -n fs.mqueue.msg_max)
  n=$(sysctl -n fs.mqueue.msgsize_max)
  local need_m="${TIDE_MQ_MAX_MESSAGES:-65535}"
  local need_n="${TIDE_MQ_MESSAGE_BYTES:-16777216}"
  if [[ "$m" -lt "$need_m" || "$n" -lt "$need_n" ]]; then
    echo "[prereq] FAIL mqueue sysctls too small (msg_max=$m msgsize_max=$n; need $need_m/$need_n). See /etc/sysctl.d/99-tide.conf"
    exit 1
  fi
  # image present
  if ! ctr -n default images ls | grep -q "${WORKLOAD_IMAGE}"; then
    echo "[prereq] FAIL image ${WORKLOAD_IMAGE} not in containerd. Run scripts/build-workload.sh"
    exit 1
  fi
  [[ -x "$HTTPBASE_BIN" ]] || { echo "[prereq] FAIL missing $HTTPBASE_BIN (go build -o bin/httpbase ./cmd/httpbase/)"; exit 1; }

  # Paper-aligned 6-core slice. Pre-creates /sys/fs/cgroup/tide with
  # cpuset.cpus=2-7 so PI=off Tide containers (C3/C4) inherit the constraint;
  # PI=on (C5/C6) already pins within 2-7 via comb_cg_pool.go defaults; httpbase
  # backend (C1/C2) is pinned by entrypoint.py via WORKLOAD_CPUSET=2-7.
  "${REPO_ROOT}/scripts/setup-paper-cgroup.sh"
}

# Only the unique HTTP backend ID allocated by this invocation is ours.
# EI=on cleans its own queues/containers on normal exit. On failure we abort
# and retain evidence; unattributed queues, containers and cgroups are untouched.
cleanup_run() {
  if [[ -n "$OWNED_BACKEND" ]]; then
    ctr -n default tasks kill -s SIGKILL "$OWNED_BACKEND" 2>/dev/null || true
    ctr -n default tasks rm "$OWNED_BACKEND" 2>/dev/null || true
    ctr -n default containers rm "$OWNED_BACKEND" 2>/dev/null || true
    OWNED_BACKEND=""
  fi
}

record_manifest() {
  local state="$1" code="$2"
  "$PYTHON" - "$ACTIVE_OUT" "$state" "$code" "$expected" "$cell" "$rps" "$k" \
    "$DURATION_SEC" "$DEADLINE_SEC" "${cmd[@]}" <<'PYMANIFEST'
import datetime, hashlib, json, os, pathlib, sys
out, status, code, expected, cell, rps, repeat, duration, deadline = sys.argv[1:10]
path = pathlib.Path(out).parent / 'manifest.json'
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
data = json.loads(path.read_text()) if path.exists() else {}
if status == 'running':
    binary = pathlib.Path(sys.argv[10])
    data = dict(schema_version=1, status=status, cell=cell, rps=int(rps), repeat=int(repeat),
                expected_count=int(expected), duration_sec=duration, deadline_sec=deadline,
                command=sys.argv[10:], started_at=now,
                binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest() if binary.is_file() else None,
                environment={**{k: os.environ.get(k) for k in ('WORKLOAD_CPUSET', 'TIDE_DETECTIONS_PER_REQ', 'TIDE_COMB_CPUIDS', 'TIDE_COMB_DEV_CPUNUM')},
                             'PAPER_CPUSET': os.environ.get('PAPER_CPUSET') or '2-7',
                             'CGROUP_ROOT': os.environ.get('CGROUP_ROOT') or '/sys/fs/cgroup'},
                rate_window_policy='retained_submission_to_last_reply',
                cleanup_policy='only_unique_owned_http_backend; EI runtime owns normal cleanup')
else:
    data.update(status=status, exit_code=int(code), finished_at=now)
    if status == 'completed':
        data['raw_sha256'] = hashlib.sha256(pathlib.Path(out).read_bytes()).hexdigest()
tmp = path.with_suffix('.json.tmp')
tmp.write_text(json.dumps(data, indent=2) + '\n')
tmp.replace(path)
PYMANIFEST
}

check_existing() {
  "$PYTHON" "$METRICS" validate "$out" --expected-count "$expected" \
    --manifest "$(dirname "$out")/manifest.json" >/dev/null || return 1
  "$PYTHON" - "$(dirname "$out")/manifest.json" "$DURATION_SEC" "$DEADLINE_SEC" "$binary" "$cell" "$rps" "$k" <<'PYCHECK'
import hashlib, json, os, pathlib, sys
m = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert (m['cell'], m['rps'], m['repeat']) == (sys.argv[5], int(sys.argv[6]), int(sys.argv[7])), 'run identity changed'
assert m['duration_sec'] == sys.argv[2] and m['deadline_sec'] == sys.argv[3], 'run configuration changed'
assert m['binary_sha256'] == hashlib.sha256(pathlib.Path(sys.argv[4]).read_bytes()).hexdigest(), 'binary changed'
assert m['environment'] == {**{k: os.environ.get(k) for k in ('WORKLOAD_CPUSET', 'TIDE_DETECTIONS_PER_REQ', 'TIDE_COMB_CPUIDS', 'TIDE_COMB_DEV_CPUNUM')},
                            'PAPER_CPUSET': os.environ.get('PAPER_CPUSET') or '2-7',
                            'CGROUP_ROOT': os.environ.get('CGROUP_ROOT') or '/sys/fs/cgroup'}, 'environment changed'
PYCHECK
}

on_exit() {
  local code=$?
  trap - EXIT
  if [[ -n "$ACTIVE_OUT" ]]; then
    record_manifest failed "${code:-1}" || true
  fi
  cleanup_run
  exit "$code"
}

main() {
  command -v "$PYTHON" >/dev/null
  [[ "$REPEATS" =~ ^[1-9][0-9]*$ ]] || { echo "REPEATS must be positive" >&2; return 1; }
  check_prereq
  mkdir -p "$RESULTS_DIR"
  local start total=0 done=0 skip=0 rc cc pi ei expected cell rps k out binary cc_flag pi_flag run_id config
  local -a cmd
  start=$(date +%s)
  trap on_exit EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  for cell in $CELLS; do
    config=$(cell_config "$cell")
    IFS=' ' read -r cc pi ei <<<"$config"
    for rps in $RPS_LIST; do
      expected=$("$PYTHON" - "$rps" "$DURATION_SEC" <<'PYCOUNT'
from decimal import Decimal
import sys
rps, duration = int(sys.argv[1]), Decimal(sys.argv[2])
assert 0 < rps <= 10**9 and duration > 0
# Both legacy Go clients use duration_ns / floor(1e9/rps).
count = int(duration * 10**9) // (10**9 // rps)
assert count > 0, 'duration must admit at least one request'
print(count)
PYCOUNT
      )
      for k in $(seq "$REPEAT_START" "$REPEATS"); do
        total=$((total + 1))
        out="$RESULTS_DIR/$cell/rps${rps}/run${k}/raw.csv"
        binary="$HTTPBASE_BIN"
        [[ "$ei" == "on" ]] && binary="$RESPTESTV2_BIN"
        if [[ -e "$out" || -e "$(dirname "$out")/manifest.json" ]]; then
          if check_existing; then
            echo "skip validated completed run $out"
            skip=$((skip + 1))
            continue
          fi
          echo "FAIL existing run is incomplete, invalid, or mismatched: $out; preserve it and use a new RESULTS_DIR" >&2
          return 1
        fi
        mkdir -p "$(dirname "$out")"
        run_id=$("$PYTHON" -c 'import uuid; print(uuid.uuid4().hex)')
        if [[ "$ei" == "on" ]]; then
          cc_flag=false; pi_flag=false
          [[ "$cc" == "on" ]] && cc_flag=true
          [[ "$pi" == "on" ]] && pi_flag=true
          cmd=("$binary" "-concurr-ctrl=$cc_flag" "-cpusets=$pi_flag" "-fps=$rps"
               "-duration=${DURATION_SEC}s" "-deadline=${DEADLINE_SEC}s" "-outfile=$out"
               "-log=$(dirname "$out")/runtime.jsonl")
        else
          OWNED_BACKEND="tide-matrix-$run_id"
          cmd=("$binary" "-cc=$cc" "-rps=$rps" "-duration=${DURATION_SEC}s"
               "-deadline=${DEADLINE_SEC}s" "-outfile=$out"
               "-backend-bind=127.0.0.1:8080" "-backend-id=$OWNED_BACKEND")
        fi
        ACTIVE_OUT="$out"
        record_manifest running 0
        echo "$cell (cc=$cc pi=$pi ei=$ei) rps=$rps run=$k"
        rc=0
        "${cmd[@]}" >"$(dirname "$out")/stdout.log" 2>"$(dirname "$out")/stderr.log" || rc=$?
        cleanup_run
        if [[ "$rc" -ne 0 ]]; then
          record_manifest failed "$rc"
          ACTIVE_OUT=""
          echo "FAIL command exit=$rc; see $(dirname "$out")/stderr.log" >&2
          return "$rc"
        fi
        if ! "$PYTHON" "$METRICS" validate "$out" --expected-count "$expected" \
            >"$(dirname "$out")/validation.json" 2>"$(dirname "$out")/validation.log"; then
          record_manifest invalid 0
          ACTIVE_OUT=""
          echo "FAIL raw CSV validation; see $(dirname "$out")/validation.log" >&2
          return 1
        fi
        record_manifest completed 0
        ACTIVE_OUT=""
        done=$((done + 1))
      done
    done
  done
  echo "done: $done completed, $skip skipped (total=$total, elapsed=$(( $(date +%s) - start ))s)"
  echo "next: $PYTHON scripts/analyze.py --root $RESULTS_DIR --no-trim-also"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
