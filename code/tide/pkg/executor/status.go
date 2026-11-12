package executor

type Status uint8

const (
	IDLE Status = iota
	BOUND
	RUNNING
	WAITING
	PENDING
)
