package containerx

import (
	"context"
	"log"
	"sync"

	"github.com/containerd/containerd"
	"github.com/containerd/containerd/namespaces"
)

var clientNo int32
var clientCh chan *containerd.Client
var mu sync.Mutex

func init() {
	clientCh = make(chan *containerd.Client, 100)
}

func PullImg(imgName string) error {
	client, err := getClient()
	if err != nil {
		return err
	}
	defer returnClient(client)

	ctx := namespaces.WithNamespace(context.Background(), "example")
	image, err := client.Pull(ctx, "docker.io/library/redis:alpine", containerd.WithPullUnpack)
	if err != nil {
		return err
	}
	log.Printf("Successfully pulled %s image\n", image.Name())

	return nil
}
