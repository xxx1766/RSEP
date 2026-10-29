package containerx

import (
	"context"
	"os"
	"runtime"
	"strconv"
	"strings"

	"github.com/containerd/cgroups"
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

// var coreId int64 = -1

// CreateCtnOpts is the options for creating a container.
type CreateCtnOpts struct {
	ImageName  string
	VolumePair string
	ExecId     string
	Commands   []string
	Envs       []string
	CgroupPath string
}

// CreateCtn creates a container with the given options.
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
	logger.Debug("Containerx: start creating new container")
	defer logger.Debug("Containerx: finish creating new container")

	client, err := getClient()
	if err != nil {
		return nil, err
	}
	defer returnClient(client)
	ctx := namespaces.WithNamespace(context.Background(), "tide")

	image, err := client.GetImage(ctx, imgName)
	if err != nil {
		return nil, errors.Wrap(err, "containerx: get image failed")
	}

	// cpu1 := strconv.FormatInt(atomic.AddInt64(&coreId, 1), 10)
	// cpu2 := strconv.FormatInt(atomic.AddInt64(&coreId, 1), 10)

	var my_mounts []specs.Mount
	if opts.VolumePair != "" {
		volumePairs := strings.Split(opts.VolumePair, ",")

		// Iterate over volume pairs and create mount specifications
		for _, pair := range volumePairs {
			paths := strings.Split(pair, ":")
			if len(paths) != 2 {
				logger.Error(volumePairs)
				return nil, errors.New("containerx: invalid volume pair format")
			}
			sourcePath := paths[0]
			// check if the source path is exist
			sourceExists, err := pathExists(sourcePath)
			if err != nil {
				logger.Error("Containerx: check source path failed")
			}
			if !sourceExists {
				err := os.MkdirAll(sourcePath, 0777)
				if err != nil {
					logger.Error("Containerx: create source path failed")
				}
			}

			destinationPath := paths[1]
			mount := specs.Mount{
				Destination: destinationPath,
				Type:        "bind",
				Source:      sourcePath,
				Options:     []string{"rbind", "rw"},
			}
			my_mounts = append(my_mounts, mount)
		}
	}

	specOpts := []oci.SpecOpts{
		oci.WithImageConfig(image),
		oci.WithProcessArgs(opts.Commands...),
		oci.WithHostNamespace(specs.IPCNamespace),
		oci.WithHostResolvconf,
		oci.WithHostNamespace(specs.NetworkNamespace),
		// oci.WithCPUs(stringx.Concat(cpu1, ",", cpu2)),
		oci.WithCgroup(opts.CgroupPath),
		oci.WithEnv(opts.Envs),
		oci.WithMounts(my_mounts),
	}
	if getGPU() {
		specOpts = append(specOpts, nvidia.WithGPUs(nvidia.WithDevices(0), nvidia.WithAllCapabilities))
	}

	logger.Debug("Containerx: new container")
	ctn, err := client.NewContainer(ctx, opts.ExecId,
		containerd.WithNewSnapshot(stringx.Concat(opts.ExecId, "snapshot"), image),
		containerd.WithNewSpec(specOpts...),
		// containerd.WithRuntime("io.containerd.runtime.v1.linux", nil),
	)
	if err != nil {
		return nil, errors.Wrap(err, "containerx: create container failed")
	}

	baseIOPath := "/tmp/tide/exec-logs"
	ioDirpath := stringx.Concat(baseIOPath, "/", opts.ExecId)
	if _, err := os.Stat(ioDirpath); os.IsNotExist(err) {
		if err := os.MkdirAll(ioDirpath, 0777); err != nil {
			return nil, errors.Wrap(err, "containerx: make io dir failed")
		}
	}
	outFile, err := os.OpenFile(stringx.Concat(ioDirpath, "/stdout"), os.O_WRONLY|os.O_CREATE, 0666)
	if err != nil {
		return nil, errors.Wrap(err, "containerx: create stdout file failed")
	}
	errFile, err := os.OpenFile(stringx.Concat(ioDirpath, "/stderr"), os.O_WRONLY|os.O_CREATE, 0666)
	if err != nil {
		return nil, errors.Wrap(err, "containerx: create stderr file failed")
	}

	logger.Debug("Containerx: new task")
	_, err = ctn.NewTask(ctx, cio.NewCreator(cio.WithStreams(nil, outFile, errFile)))
	if err != nil {
		ctn.Delete(ctx)
		return nil, errors.Wrap(err, "containerx: create task failed")
	}

	return ctn, nil
}

// 检查路径是否存在
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

// if gpu exist
func getGPU() bool {
	_, err := os.Stat("/dev/nvidia0")
	return !os.IsNotExist(err)
}

// MoveCtn moves a container to the given cgroup.
func MoveCtn(ctn containerd.Container, phyRes *processor.PhysicResource) error {
	logger := logrus.WithFields(logrus.Fields{
		"ExecutorId": ctn.ID(),
	})
	logger.Debug("Containerx: start moving a container")
	defer logger.Debug("Containerx: finish moving a container")

	ctx := namespaces.WithNamespace(context.Background(), "tide")
	task, err := ctn.Task(ctx, nil)
	if err != nil {
		return err
	}

	pids, err := task.Pids(ctx)
	if err != nil {
		return err
	}

	cgPath := getCombCgroup(phyRes)
	cg, err := cgroups.Load(cgroups.V1, cgroups.StaticPath(cgPath))
	if err != nil {
		return err
	}
	logger.WithFields(logrus.Fields{"pids": pids, "cgPath": cgPath, "phyRes": phyRes}).Debug("Containerx: move a container")

	for _, pid := range pids {
		if err := cg.Add(cgroups.Process{Pid: int(pid.Pid)}, cgroups.Cpuset); err != nil {
			return err
		}
	}
	return nil
}

// PauseCtr pauses a container.
func PauseCtr(ctr containerd.Container) error {
	logger := logrus.WithFields(logrus.Fields{
		"ExecutorId": ctr.ID(),
	})
	logger.Debug("Containerx: start pausing a container")
	defer logger.Debug("Containerx: finish pasuing a container")

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

// ResumeCtr resumes a container.
func ResumeCtr(ctr containerd.Container) error {
	logger := logrus.WithFields(logrus.Fields{
		"ExecutorId": ctr.ID(),
	})
	logger.Debug("Containerx: start resuming a container")
	defer logger.Debug("Containerx: finish resuming a container")

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

// StartCtr starts a container.
func StartCtr(ctr containerd.Container) error {
	logger := logrus.WithFields(logrus.Fields{
		"ExecutorId": ctr.ID(),
	})
	logger.Debug("Containerx: start starting a container")
	defer logger.Debug("Containerx: finish starting a container")

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

// StopCtn stops a container.
func StopCtn(ctn containerd.Container) error {
	logger := logrus.WithFields(logrus.Fields{
		"ExecutorId": ctn.ID(),
	})
	logger.Debug("Containerx: start stopping a container")
	defer logger.Debug("Containerx: finish stopping a container")

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

// UpdateRes updates the resources of a container.
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

	image, err := ctn.Image(ctx)
	if err != nil {
		logrus.Error("Error getting container image:", err)
	}
	logrus.WithFields(logrus.Fields{
		"ctnId":    ctn.ID(),
		"ctnImage": image,
		"cpu":      cpus,
	}).Debug("try to update resources")

	resource := &specs.LinuxResources{
		CPU: &specs.LinuxCPU{
			Cpus: cpus,
		},
	}
	err = task.Update(ctx, containerd.WithResources(resource))
	return err
}
