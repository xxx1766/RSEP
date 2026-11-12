package containerx

import (
	"sync/atomic"

	"github.com/containerd/containerd"
)

func getClient() (*containerd.Client, error) {
	mu.Lock()
	defer mu.Unlock()
	var client *containerd.Client
	if clientNo > 0 {
		client = <-clientCh
	} else {
		var err error
		client, err = containerd.New("/run/containerd/containerd.sock")
		if err != nil {
			return nil, err
		}
	}
	return client, nil
}

func returnClient(cli *containerd.Client) {
	clientCh <- cli
	atomic.AddInt32(&clientNo, 1)
}
