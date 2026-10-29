package resource

type Logic struct {
	CpuNum int
}

var EmptyLogic *Logic = &Logic{}

func (r *Logic) Add(delta *Logic) {
	r.CpuNum += delta.CpuNum
}

func (r *Logic) Subtract(delta *Logic) {
	r.CpuNum -= delta.CpuNum
}

func (r *Logic) Greater(b *Logic) bool {
	return r.CpuNum > b.CpuNum
}

func (r *Logic) GreaterOrEqual(b *Logic) bool {
	return r.CpuNum >= b.CpuNum
}
