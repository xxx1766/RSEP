package routine

// Resource represents the resource of a node.
type Resource struct {
	CpuNum int
	GpuNum int
}

var EmptyResourse *Resource = &Resource{}

// NewResource adds a new resource.
func (r *Resource) Add(delta *Resource) {
	r.CpuNum += delta.CpuNum
	r.GpuNum += delta.GpuNum
}

// Subtract subtracts the given resource from the current resource.
func (r *Resource) Subtract(delta *Resource) {
	r.CpuNum -= delta.CpuNum
	r.GpuNum -= delta.GpuNum
}

// Greater returns true if the current resource is greater than the given resource.
func (r *Resource) Greater(b *Resource) bool {
	return r.CpuNum > b.CpuNum && r.GpuNum > b.GpuNum
}

// GreaterOrEqual returns true if the current resource is greater than or equal to the given resource.
func (r *Resource) GreaterOrEqual(b *Resource) bool {
	return r.CpuNum >= b.CpuNum && r.GpuNum >= b.GpuNum
}

// Copy returns a copy of the given resource.
func (r *Resource) Copy() *Resource {
	return &Resource{
		CpuNum: r.CpuNum,
		GpuNum: r.GpuNum,
	}
}
