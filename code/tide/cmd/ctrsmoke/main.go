// ctrsmoke — verifies that the Tide-pinned containerd Go client (v1.6.9)
// can talk to the host's containerd daemon (v2.2.3 on this machine).
//
// Purpose: gates Phase 2 of the cgroup v2 migration plan. If this works,
// pkg/containerxv2/ only needs to swap the cgroups library (branch A).
// If it fails, we must also upgrade to github.com/containerd/containerd/v2
// and github.com/containerd/cgroups/v3 (branch B).
//
// Run: sudo /tmp/ctrsmoke
package main

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/containerd/containerd"
	"github.com/containerd/containerd/cio"
	"github.com/containerd/containerd/namespaces"
	"github.com/containerd/containerd/oci"
)

const (
	socketPath = "/run/containerd/containerd.sock"
	ns         = "tide-smoke"
	imageRef   = "docker.io/library/hello-world:latest"
	ctnID      = "tide-smoke-1"
)

func main() {
	client, err := containerd.New(socketPath)
	if err != nil {
		die("containerd.New", err)
	}
	defer client.Close()

	v, err := client.Version(context.Background())
	if err != nil {
		die("client.Version", err)
	}
	fmt.Printf("OK  daemon version:  %s  revision: %s\n", v.Version, v.Revision)

	ctx := namespaces.WithNamespace(context.Background(), ns)

	img, err := client.Pull(ctx, imageRef, containerd.WithPullUnpack)
	if err != nil {
		die("client.Pull "+imageRef, err)
	}
	fmt.Printf("OK  pulled image:    %s (size=%d)\n", img.Name(), mustSize(ctx, img))

	// Clean any stale container from previous runs
	if existing, err := client.LoadContainer(ctx, ctnID); err == nil {
		if task, err := existing.Task(ctx, nil); err == nil {
			_, _ = task.Delete(ctx, containerd.WithProcessKill)
		}
		_ = existing.Delete(ctx, containerd.WithSnapshotCleanup)
	}

	ctn, err := client.NewContainer(ctx, ctnID,
		containerd.WithImage(img),
		containerd.WithNewSnapshot(ctnID+"-snap", img),
		containerd.WithNewSpec(
			oci.WithImageConfig(img),
			oci.WithHostNamespace(ociLinuxIPC),
			oci.WithCgroup("/tide-smoke/"+ctnID),
		),
	)
	if err != nil {
		die("client.NewContainer", err)
	}
	defer ctn.Delete(ctx, containerd.WithSnapshotCleanup)
	fmt.Printf("OK  container created: %s (cgroup=/tide-smoke/%s)\n", ctnID, ctnID)

	task, err := ctn.NewTask(ctx, cio.NullIO)
	if err != nil {
		die("ctn.NewTask", err)
	}
	defer task.Delete(ctx, containerd.WithProcessKill)

	exitCh, err := task.Wait(ctx)
	if err != nil {
		die("task.Wait", err)
	}
	if err := task.Start(ctx); err != nil {
		die("task.Start", err)
	}

	select {
	case status := <-exitCh:
		code, _, _ := status.Result()
		fmt.Printf("OK  task exited:     code=%d\n", code)
	case <-time.After(10 * time.Second):
		_ = task.Kill(ctx, 9)
		die("task timeout after 10s", nil)
	}

	fmt.Println("\nRESULT: v1.6.9 client can talk to v2.2.3 daemon.")
	fmt.Println("        → Phase 2 branch A is viable (only swap cgroups lib).")
}

// ociLinuxIPC mirrors specs.IPCNamespace without importing runtime-spec
// directly (oci.WithHostNamespace takes a specs.LinuxNamespaceType which
// is just a string type).
const ociLinuxIPC = "ipc"

func mustSize(ctx context.Context, img containerd.Image) int64 {
	s, err := img.Size(ctx)
	if err != nil {
		return -1
	}
	return s
}

func die(stage string, err error) {
	if err != nil {
		fmt.Fprintf(os.Stderr, "FAIL %s: %v\n", stage, err)
	} else {
		fmt.Fprintf(os.Stderr, "FAIL %s\n", stage)
	}
	fmt.Fprintln(os.Stderr, "\nRESULT: v1.6.9 client FAILS against v2.2.3 daemon.")
	fmt.Fprintln(os.Stderr, "        → Phase 2 must take branch B (upgrade containerd/v2 + cgroups/v3).")
	os.Exit(1)
}
