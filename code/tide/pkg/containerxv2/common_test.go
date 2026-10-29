package containerxv2

import (
	"context"
	"net"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/containerd/containerd"
	containersapi "github.com/containerd/containerd/api/services/containers/v1"
	"github.com/containerd/containerd/namespaces"
	"google.golang.org/grpc"
)

func resetSharedClient(t *testing.T) *containerd.Client {
	t.Helper()
	client := testClient(t)
	old := sharedClient
	sharedClient = client
	t.Cleanup(func() { sharedClient = old })
	return client
}

func testClient(t *testing.T) *containerd.Client {
	t.Helper()
	socket := filepath.Join(t.TempDir(), "client.sock")
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	server := grpc.NewServer()
	containersapi.RegisterContainersServer(server, &testContainerService{})
	t.Cleanup(server.Stop)
	go func() { _ = server.Serve(listener) }()
	c, err := containerd.New(socket)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = c.Close() })
	return c
}

type testContainerService struct {
	containersapi.ContainersServer
}

func (*testContainerService) Get(_ context.Context, req *containersapi.GetContainerRequest) (*containersapi.GetContainerResponse, error) {
	return &containersapi.GetContainerResponse{Container: containersapi.Container{ID: req.ID}}, nil
}

func TestReturnedClientKeepsContainerHandleUsable(t *testing.T) {
	resetSharedClient(t)
	retained, err := getClient()
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(namespaces.WithNamespace(context.Background(), "tide"), time.Second)
	defer cancel()
	handle, err := retained.LoadContainer(ctx, "retained-container")
	if err != nil {
		t.Fatal(err)
	}
	// CreateCtn returns its client before callers finish using its handle.
	returnClient(retained)
	// Repeated use must not exhaust a bounded return channel or close a
	// connection still owned by this handle.
	for i := 0; i < 101; i++ {
		client, err := getClient()
		if err != nil {
			t.Fatal(err)
		}
		returnClient(client)
	}
	if _, err := handle.Info(ctx); err != nil {
		t.Fatalf("return invalidated a live container handle: %v", err)
	}
}

func TestSharedClientConcurrentAcquisition(t *testing.T) {
	want := resetSharedClient(t)
	var workers sync.WaitGroup
	for i := 0; i < 32; i++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			ctx, cancel := context.WithTimeout(namespaces.WithNamespace(context.Background(), "tide"), 5*time.Second)
			defer cancel()
			for j := 0; j < 20; j++ {
				client, err := getClient()
				if err != nil || client != want {
					t.Errorf("acquired client %p, %v; want shared client %p", client, err, want)
					return
				}
				handle, err := client.LoadContainer(ctx, "concurrent-container")
				if err != nil {
					t.Error(err)
					return
				}
				returnClient(client)
				if _, err := handle.Info(ctx); err != nil {
					t.Errorf("container handle RPC after return: %v", err)
					return
				}
			}
		}()
	}
	workers.Wait()
}
