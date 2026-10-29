#!/usr/bin/env bash
# setup-paper-cgroup.sh — pre-create the /tide cgroup (v2) with cpuset.cpus=2-7
# so that Tide's per-instance child cgroups (created later by
# pkg/containerxv2.CreateCgroup as /tide/<id>) inherit the constraint.
#
# Why: paper RSEP §VI.A allots 3 physical / 6 logical cores to the experiment
# slice (cpus 2-7 here). PI=on path (containerxv2/comb_cg_pool.go:43-46) already
# pins per-executor cpusets within 2-7, but PI=off (cgroup_op.go:48) only sets
# cpu.max — without a parent constraint those containers can run on all 16
# cores. This script provides that parent constraint.
#
# Idempotent. Safe to run multiple times. Run as root.

set -euo pipefail

CGROUP_ROOT="${CGROUP_ROOT:-/sys/fs/cgroup}"
TIDE_CG="${CGROUP_ROOT}/tide"
CPUSET="${PAPER_CPUSET:-2-7}"

if [[ $EUID -ne 0 ]]; then
  echo "[setup-paper-cgroup] must run as root (writes /sys/fs/cgroup/...)" >&2
  exit 1
fi

# Sanity: must be cgroup v2 unified
fstype=$(stat -fc %T "$CGROUP_ROOT")
if [[ "$fstype" != "cgroup2fs" ]]; then
  echo "[setup-paper-cgroup] $CGROUP_ROOT is not cgroup2fs (got: $fstype)." >&2
  echo "  This script targets cgroup v2 only. v1 hosts use pkg/containerx (legacy)." >&2
  exit 1
fi

# 1) Enable +cpuset in root's subtree_control so children of root can use cpuset
if ! grep -qw cpuset "$CGROUP_ROOT/cgroup.subtree_control"; then
  # Ignore failure when cpuset is already on (race-safe)
  echo "+cpuset" > "$CGROUP_ROOT/cgroup.subtree_control" 2>/dev/null || true
fi
if ! grep -qw cpuset "$CGROUP_ROOT/cgroup.subtree_control"; then
  echo "[setup-paper-cgroup] failed to enable +cpuset on root subtree_control" >&2
  echo "  Current: $(cat "$CGROUP_ROOT/cgroup.subtree_control")" >&2
  exit 1
fi

# 2) Pre-create /tide parent
mkdir -p "$TIDE_CG"

# 3) Set cpuset.cpus on /tide. cgroup v2 child inheritance: when a sub-cgroup
#    leaves cpuset.cpus empty (or sets a subset of parent's), its
#    cpuset.cpus.effective = parent.effective ∩ child (or parent.effective if
#    child empty). So PI=off children with empty cpuset will be scoped to 2-7.
echo "$CPUSET" > "$TIDE_CG/cpuset.cpus"

# 4) Allow children of /tide to also use cpuset (for PI=on path which writes
#    its own cpuset.cpus on grandchildren).
if ! grep -qw cpuset "$TIDE_CG/cgroup.subtree_control" 2>/dev/null; then
  echo "+cpuset" > "$TIDE_CG/cgroup.subtree_control" 2>/dev/null || true
fi

# 5) Verify effective view
eff=$(cat "$TIDE_CG/cpuset.cpus.effective")
if [[ "$eff" != "$CPUSET" ]]; then
  echo "[setup-paper-cgroup] FAIL: $TIDE_CG/cpuset.cpus.effective=$eff (want $CPUSET)" >&2
  echo "  cgroup.controllers=$(cat "$TIDE_CG/cgroup.controllers" 2>/dev/null)" >&2
  exit 1
fi

echo "[setup-paper-cgroup] OK: $TIDE_CG/cpuset.cpus=$CPUSET, effective=$eff"
