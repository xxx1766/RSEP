package ipc

func New(name string) (Queue, error) {
	return newPosixMQ(name)
}
