package containerxv2

// Container handles retain their client after CreateCtn returns. Keep one
// process-lifetime client so later handle operations can still use its connection.

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

// The gRPC connection supports concurrent RPCs. Returning from one operation
// must not close it while a container handle or another operation still uses it.
func returnClient(_ *containerd.Client) {}
