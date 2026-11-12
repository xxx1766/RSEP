package routine

type Resource struct {
	CpuNum int
	GpuNum int
}

var EmptyResourse *Resource = &Resource{}

func (r *Resource) Add(delta *Resource) {
	r.CpuNum += delta.CpuNum
	r.GpuNum += delta.GpuNum
}

func (r *Resource) Subtract(delta *Resource) {
	r.CpuNum -= delta.CpuNum
	r.GpuNum -= delta.GpuNum
}

func (r *Resource) Greater(b *Resource) bool {
	return r.CpuNum > b.CpuNum && r.GpuNum > b.GpuNum
}

func (r *Resource) GreaterOrEqual(b *Resource) bool {
	return r.CpuNum >= b.CpuNum && r.GpuNum >= b.GpuNum
}

func (r *Resource) Copy() *Resource {
	return &Resource{
		CpuNum: r.CpuNum,
		GpuNum: r.GpuNum,
	}
}
