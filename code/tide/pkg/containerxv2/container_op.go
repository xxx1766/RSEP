package containerxv2

// container_op.go — cgroup v2 port of pkg/containerx/container_op.go.
//
// On v2 the controllers are unified. Request-level CPU binding updates the
// container's own cpuset without moving it out of its runtime-owned cgroup.
//
// All other functions (CreateCtn/StartCtr/PauseCtr/...) use containerd's OCI
// APIs and are cgroup-version-agnostic (P0.3 smoke test verified oci.WithCgroup
// auto-translates /tide/* paths on v2). They are copied verbatim.

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"

	"github.com/containerd/cgroups/v3/cgroup2"
	"github.com/containerd/containerd"
	"github.com/containerd/containerd/cio"
	"github.com/containerd/containerd/contrib/nvidia"
	"github.com/containerd/containerd/namespaces"
	"github.com/containerd/containerd/oci"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/stringx"
	"github.com/opencontainers/runtime-spec/specs-go"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"
)

// CreateCtnOpts is the options for creating a container. Mirrors v1.
type CreateCtnOpts struct {
	ImageName  string
	VolumePair string
	ExecId     string
	Commands   []string
	Envs       []string
	CgroupPath string
}

// CreateCtn creates a container with the given options. Unchanged vs v1:
// containerd's oci.WithCgroup produces a runtime-spec path that runc
// translates into a v2 unified cgroup automatically (verified by P0.3).
func CreateCtn(opts *CreateCtnOpts) (containerd.Container, error) {
	if opts.CgroupPath == "" {
		opts.CgroupPath = getCgroup()
	}
	imgName := stringx.Concat(opts.ImageName, ":", runtime.GOARCH)
	logger := logrus.WithFields(logrus.Fields{
		"ImageName":  imgName,
		"ExecutorId": opts.ExecId,
		"Command":    opts.Commands,
		"VolumePair": opts.VolumePair,
		"CgroupPath": opts.CgroupPath,
	})
	logger.Debug("Containerxv2: start creating new container")
	defer logger.Debug("Containerxv2: finish creating new container")

	client, err := getClient()
	if err != nil {
		return nil, err
	}
	defer returnClient(client)
	ctx := namespaces.WithNamespace(context.Background(), "tide")

	image, err := client.GetImage(ctx, imgName)
	if err != nil {
		return nil, errors.Wrap(err, "containerxv2: get image failed")
	}

	var myMounts []specs.Mount
	if opts.VolumePair != "" {
		volumePairs := strings.Split(opts.VolumePair, ",")
		for _, pair := range volumePairs {
			paths := strings.Split(pair, ":")
			if len(paths) != 2 {
				logger.Error(volumePairs)
				return nil, errors.New("containerxv2: invalid volume pair format")
			}
			sourcePath := paths[0]
			sourceExists, err := pathExists(sourcePath)
			if err != nil {
				logger.Error("Containerxv2: check source path failed")
			}
			if !sourceExists {
				if err := os.MkdirAll(sourcePath, 0777); err != nil {
					logger.Error("Containerxv2: create source path failed")
				}
			}

			destinationPath := paths[1]
			mount := specs.Mount{
				Destination: destinationPath,
				Type:        "bind",
				Source:      sourcePath,
				Options:     []string{"rbind", "rw"},
			}
			myMounts = append(myMounts, mount)
		}
	}

	specOpts := []oci.SpecOpts{
		oci.WithImageConfig(image),
		oci.WithProcessArgs(opts.Commands...),
		oci.WithHostNamespace(specs.IPCNamespace),
		oci.WithHostResolvconf,
		oci.WithHostNamespace(specs.NetworkNamespace),
		oci.WithCgroup(opts.CgroupPath),
		oci.WithEnv(opts.Envs),
		oci.WithMounts(myMounts),
	}
	if getGPU() {
		specOpts = append(specOpts, nvidia.WithGPUs(nvidia.WithDevices(0), nvidia.WithAllCapabilities))
	}

	ctn, err := client.NewContainer(ctx, opts.ExecId,
		containerd.WithNewSnapshot(stringx.Concat(opts.ExecId, "snapshot"), image),
		containerd.WithNewSpec(specOpts...),
	)
	if err != nil {
		return nil, errors.Wrap(err, "containerxv2: create container failed")
	}

	baseIOPath := "/tmp/tide/exec-logs"
	ioDirpath := stringx.Concat(baseIOPath, "/", opts.ExecId)
	if _, err := os.Stat(ioDirpath); os.IsNotExist(err) {
		if err := os.MkdirAll(ioDirpath, 0777); err != nil {
			return nil, errors.Wrap(err, "containerxv2: make io dir failed")
		}
	}
	outFile, err := os.OpenFile(stringx.Concat(ioDirpath, "/stdout"), os.O_WRONLY|os.O_CREATE, 0666)
	if err != nil {
		return nil, errors.Wrap(err, "containerxv2: create stdout file failed")
	}
	errFile, err := os.OpenFile(stringx.Concat(ioDirpath, "/stderr"), os.O_WRONLY|os.O_CREATE, 0666)
	if err != nil {
		return nil, errors.Wrap(err, "containerxv2: create stderr file failed")
	}

	if _, err := ctn.NewTask(ctx, cio.NewCreator(cio.WithStreams(nil, outFile, errFile))); err != nil {
		ctn.Delete(ctx)
		return nil, errors.Wrap(err, "containerxv2: create task failed")
	}

	return ctn, nil
}

func pathExists(path string) (bool, error) {
	_, err := os.Stat(path)
	if err == nil {
		return true, nil
	}
	if os.IsNotExist(err) {
		return false, nil
	}
	return false, err
}

func getGPU() bool {
	// v1 variant checks /dev/nvidia0 only. On some hosts (e.g. this GCP box)
	// the device nodes exist but nvidia-container-cli isn't installed, which
	// would cause nvidia.WithGPUs to fail at container start. Require both.
	if _, err := os.Stat("/dev/nvidia0"); os.IsNotExist(err) {
		return false
	}
	if _, err := exec.LookPath("nvidia-container-cli"); err != nil {
		return false
	}
	return true
}

// MoveCtn applies a Processor's CPU set to the container's own cgroup.
// Moving processes out of that cgroup breaks runc's subsequent process
// enumeration on v2: task.Pids can then return an empty list. Updating the
// cpuset in place preserves container ownership and rebinds all its threads.
func MoveCtn(ctn containerd.Container, phyRes *processor.PhysicResource) error {
	logger := logrus.WithFields(logrus.Fields{"ExecutorId": ctn.ID()})
	ctx := namespaces.WithNamespace(context.Background(), "tide")
	task, err := ctn.Task(ctx, nil)
	if err != nil {
		return err
	}
	pids, err := task.Pids(ctx)
	if err != nil {
		return err
	}
	if len(pids) == 0 {
		return errors.New("containerxv2: cannot bind an empty container process set")
	}
	target := getCombCgroup(phyRes)
	if target == "" {
		return errors.New("containerxv2: no CPU set for requested Processor")
	}
	mask, err := os.ReadFile(filepath.Join(cgroupRoot, target, "cpuset.cpus.effective"))
	if err != nil {
		return errors.Wrap(err, "containerxv2: read Processor CPU set")
	}
	cpus := strings.TrimSpace(string(mask))
	if cpus == "" {
		return errors.New("containerxv2: empty Processor CPU set")
	}
	spec, err := ctn.Spec(ctx)
	if err != nil {
		return err
	}
	if spec.Linux == nil || spec.Linux.CgroupsPath == "" {
		return errors.New("containerxv2: missing container cgroup")
	}
	cgPath := spec.Linux.CgroupsPath
	mgr, err := cgroup2.Load(cgPath)
	if err != nil {
		return errors.Wrap(err, "containerxv2: load container cgroup")
	}
	if err := mgr.Update(&cgroup2.Resources{CPU: &cgroup2.CPU{Cpus: cpus}}); err != nil {
		return errors.Wrap(err, "containerxv2: bind container CPU set")
	}
	effective, err := os.ReadFile(filepath.Join(cgroupRoot, cgPath, "cpuset.cpus.effective"))
	if err != nil || strings.TrimSpace(string(effective)) != cpus {
		return errors.Errorf("containerxv2: effective CPU set %q differs from target %q: %v", effective, cpus, err)
	}
	logger.WithFields(logrus.Fields{"pids": pids, "cgPath": cgPath, "phyRes": phyRes, "cpus": cpus}).
		Debug("Containerxv2: bound container CPU set")
	return nil
}

// PauseCtr pauses a container (unchanged vs v1 — containerd task APIs).
func PauseCtr(ctr containerd.Container) error {
	ctx := namespaces.WithNamespace(context.Background(), "tide")
	task, err := ctr.Task(ctx, nil)
	if err != nil {
		return err
	}
	if err := task.Pause(ctx); err != nil {
		ctr.Delete(ctx, containerd.WithSnapshotCleanup)
		return err
	}
	return nil
}

// ResumeCtr resumes a container (unchanged).
func ResumeCtr(ctr containerd.Container) error {
	ctx := namespaces.WithNamespace(context.Background(), "tide")
	task, err := ctr.Task(ctx, nil)
	if err != nil {
		return err
	}
	if err := task.Resume(ctx); err != nil {
		ctr.Delete(ctx, containerd.WithSnapshotCleanup)
		return err
	}
	return nil
}

// StartCtr starts a container (unchanged).
func StartCtr(ctr containerd.Container) error {
	ctx := namespaces.WithNamespace(context.Background(), "tide")
	task, err := ctr.Task(ctx, nil)
	if err != nil {
		return err
	}
	if err := task.Start(ctx); err != nil {
		ctr.Delete(ctx, containerd.WithSnapshotCleanup)
		return err
	}
	return nil
}

// StopCtn stops and deletes a container (unchanged).
func StopCtn(ctn containerd.Container) error {
	ctx := namespaces.WithNamespace(context.Background(), "tide")
	task, err := ctn.Task(ctx, nil)
	if err != nil {
		return err
	}
	if _, err := task.Delete(ctx, containerd.WithProcessKill); err != nil {
		ctn.Delete(ctx, containerd.WithSnapshotCleanup)
		return err
	}
	if spec, err := ctn.Spec(ctx); err == nil {
		retCgroup(spec.Linux.CgroupsPath)
	}
	return ctn.Delete(ctx, containerd.WithSnapshotCleanup)
}

// UpdateRes updates the resources of a container via the containerd task
// update path (unchanged vs v1 — runc handles v1/v2 translation).
func UpdateRes(ctn containerd.Container, res *processor.PhysicResource) error {
	ctx := namespaces.WithNamespace(context.Background(), "tide")
	task, err := ctn.Task(ctx, nil)
	if err != nil {
		return err
	}

	cpuIds := make([]string, 0, 1)
	for i, used := range res.CpuSet {
		if used == 0 {
			continue
		}
		cpuIds = append(cpuIds, strconv.Itoa(i))
	}
	cpus := strings.Join(cpuIds, ",")

	logrus.WithFields(logrus.Fields{"ctnId": ctn.ID(), "cpu": cpus}).Debug("Containerxv2: update resources")
	resource := &specs.LinuxResources{CPU: &specs.LinuxCPU{Cpus: cpus}}
	return task.Update(ctx, containerd.WithResources(resource))
}
