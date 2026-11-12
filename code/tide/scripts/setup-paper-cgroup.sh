#!/usr/bin/env bash

set -euo pipefail

CGROUP_ROOT="${CGROUP_ROOT:-/sys/fs/cgroup}"
TIDE_CG="${CGROUP_ROOT}/tide"
CPUSET="${PAPER_CPUSET:-2-7}"

if [[ $EUID -ne 0 ]]; then
  echo "[setup-paper-cgroup] must run as root (writes /sys/fs/cgroup/...)" >&2
  exit 1
fi

fstype=$(stat -fc %T "$CGROUP_ROOT")
if [[ "$fstype" != "cgroup2fs" ]]; then
  echo "[setup-paper-cgroup] $CGROUP_ROOT is not cgroup2fs (got: $fstype)." >&2
  echo "  This script targets cgroup v2 only. v1 hosts use pkg/containerx (legacy)." >&2
  exit 1
fi

if ! grep -qw cpuset "$CGROUP_ROOT/cgroup.subtree_control"; then
  echo "+cpuset" > "$CGROUP_ROOT/cgroup.subtree_control" 2>/dev/null || true
fi
if ! grep -qw cpuset "$CGROUP_ROOT/cgroup.subtree_control"; then
  echo "[setup-paper-cgroup] failed to enable +cpuset on root subtree_control" >&2
  echo "  Current: $(cat "$CGROUP_ROOT/cgroup.subtree_control")" >&2
  exit 1
fi

mkdir -p "$TIDE_CG"

echo "$CPUSET" > "$TIDE_CG/cpuset.cpus"

if ! grep -qw cpuset "$TIDE_CG/cgroup.subtree_control" 2>/dev/null; then
  echo "+cpuset" > "$TIDE_CG/cgroup.subtree_control" 2>/dev/null || true
fi

eff=$(cat "$TIDE_CG/cpuset.cpus.effective")
if [[ "$eff" != "$CPUSET" ]]; then
  echo "[setup-paper-cgroup] FAIL: $TIDE_CG/cpuset.cpus.effective=$eff (want $CPUSET)" >&2
  echo "  cgroup.controllers=$(cat "$TIDE_CG/cgroup.controllers" 2>/dev/null)" >&2
  exit 1
fi

echo "[setup-paper-cgroup] OK: $TIDE_CG/cpuset.cpus=$CPUSET, effective=$eff"
