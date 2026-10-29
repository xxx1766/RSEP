package executor

type Status uint8

const (
	IDLE    Status = iota // 未被使用
	BOUND                 // 绑定routine，等待调度执行
	RUNNING               // 占用Processor，正在执行
	WAITING               // 等待数据
	PENDING               // 数据获取成功，等待调度执行
)
