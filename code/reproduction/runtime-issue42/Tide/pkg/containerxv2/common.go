package containerxv2

import (
	"sync"

	"github.com/containerd/containerd"
)

var (
	mu           sync.Mutex
	sharedClient *containerd.Client
)

func getClient() (*containerd.Client, error) {
	mu.Lock()
	defer mu.Unlock()
	if sharedClient == nil {
		client, err := containerd.New("/run/containerd/containerd.sock")
		if err != nil {
			return nil, err
		}
		sharedClient = client
	}
	return sharedClient, nil
}

func returnClient(_ *containerd.Client) {}
