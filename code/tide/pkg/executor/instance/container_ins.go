package instance

import (
	"github.com/containerd/containerd"
	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/dev"
	// NB: on this cgroup v2 host the v1 containerx ops (e.g. MoveCtn's
	// cg.Add with cgroups.Cpuset) silently hang or error; we route through
	// the cgroup v2 port instead. pkg/containerx is preserved untouched as
	// the ablation baseline but is no longer in the executor hot path.
	containerx "github.com/ict/tide/pkg/containerxv2"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/stringx"
	"github.com/sirupsen/logrus"
)

type containerIns struct {
	*baseIns

	ctr containerd.Container
}

func newContainerIns(args *NewInstanceArgs) (Instance, error) {
	logger := logrus.WithFields(logrus.Fields{
		"InstanceId": args.Id,
		"ImageName":  args.ImageName,
		"VolumePair": args.VolumePair,
	})
	logger.Debug("ContainerIns: start creating")
	defer logger.Debug("ContainerIns: finish creating")

	baseIns := newBaseIns(args.Id)
	if err := baseIns.initMQ(); err != nil {
		return nil, err
	}

	var ctrMode string
	envs := make([]string, 0)
	if args.Mode == interfaces.StubEntry {
		ctrMode = "entry"
	} else {
		ctrMode = "fn"
		envs = append(envs, "GOMAXPROCS=1")
	}

	envs = append(envs,
		stringx.Concat("TIDE_IN_MQ=", args.Id, "-in"),
		stringx.Concat("TIDE_OUT_MQ=", args.Id, "-out"),
		stringx.Concat("TIDE_MODE=", ctrMode),
	)

	// TODO: Command 可配置化
	opts := &containerx.CreateCtnOpts{
		ImageName:  args.ImageName,
		VolumePair: args.VolumePair,
		Commands:   args.Commands,
		ExecId:     args.Id,
		Envs:       envs,
	}
	ctn, err := containerx.CreateCtn(opts)
	if err != nil {
		return nil, err
	}

	ins := &containerIns{
		baseIns: baseIns,
		ctr:     ctn,
	}
	return ins, nil
}

func (ins *containerIns) Close() error {
	if err := containerx.StopCtn(ins.ctr); err != nil {
		logrus.Error(err)
	}
	return ins.baseIns.close()
}

func (ins *containerIns) AllocResource(physRes *processor.PhysicResource) error {
	if ins.physRes != nil {
		logrus.Fatal("ContainerIns: when alloc, instance physics resource is not nil")
	}
	ins.physRes = physRes
	// In the ablation, PI-off means every worker may use the shared parent
	// CPU slice, as in the HTTP baseline. Moving them into a shared per-pair
	// quota cgroup accidentally reduced the entire CC-off cell to two CPUs.
	if dev.RSEPTest && !dev.EnableCpuset {
		return nil
	}

	if clsstat.Singleton().GetSelf().Role == clsstat.EDGE {
		return containerx.MoveCtn(ins.ctr, physRes)
	} else if clsstat.Singleton().GetSelf().Role == clsstat.CLOUD {
		return containerx.UpdateRes(ins.ctr, physRes)
	}
	return nil
}

func (ins *containerIns) WithdrawResource() error {
	if ins.physRes == nil {
		logrus.Fatal("ContainerIns: when withdraw, instance physics resource is nil")
	}
	ins.physRes = nil
	return nil
	// return containerx.UpdateRes(ins.ctr, processor.EmptyResourse)
}

func (ins *containerIns) SendRoutine(rtn *routine.Routine) error {
	return ins.baseIns.sendRtnToMQ(rtn)
}

func (ins *containerIns) Start() error {
	return containerx.StartCtr(ins.ctr)
}

func (ins *containerIns) Pause() error {
	return containerx.PauseCtr(ins.ctr)
}

func (ins *containerIns) Resume() error {
	return containerx.ResumeCtr(ins.ctr)
}
