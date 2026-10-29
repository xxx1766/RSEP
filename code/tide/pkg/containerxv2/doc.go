// Package containerxv2 is the cgroup v2 port of pkg/containerx.
//
// Why a separate package: on cgroup v2 hosts the Tide v1 code (which hard-codes
// github.com/containerd/cgroups's cgroups.V1) cannot run at all — there is no
// /sys/fs/cgroup/cpuset/... hierarchy to create under. We keep pkg/containerx
// intact (research baseline comparability) and add a parallel v2 implementation
// here.
//
// Scope of changes vs pkg/containerx (confirmed via P0.3 smoke test):
//
//   - cgroup_op.go      — cgroups.New/Load(cgroups.V1,…) replaced with
//                         cgroup2.NewManager/LoadManager from
//                         github.com/containerd/cgroups/v3/cgroup2.
//   - cgroup_pool.go    — identical logic, calls the v2 ops.
//   - comb_cg_pool.go   — SAME AS ABOVE, PLUS the rsepInitCombCgroup()
//                         hardcoded {devCpuNum:8, cpuIds:[0,1,2,4,5,6]} is
//                         replaced with env-var-driven configuration so this
//                         harness works on hosts with != 8 logical cores.
//   - container_op.go   — MoveCtn()’s cg.Add(Process, Cpuset) becomes an
//                         AddProc write to the unified cgroup.procs. All other
//                         functions use containerd's OCI APIs which already
//                         translate /tide/* paths to v2 automatically; they
//                         are copied verbatim from pkg/containerx.
//   - common.go, image.go — copied unchanged (no cgroup interaction).
//
// The v1 containerd Go client (v1.6.9) is kept — P0.3 verified it can talk to
// the v2.2.3 daemon and that oci.WithCgroup("/tide/foo") auto-creates a v2
// cgroup via runc’s unified path handling.
package containerxv2
