module github.com/ict/tide

go 1.22.0

toolchain go1.22.2

require (
	github.com/aceofkid/posix_mq v0.0.3
	github.com/containerd/cgroups v1.0.3
	github.com/containerd/cgroups/v3 v3.0.3
	github.com/containerd/containerd v1.6.9
	github.com/google/uuid v1.2.0
	github.com/ict/tide/proto v0.0.0-00010101000000-000000000000
	github.com/opencontainers/runtime-spec v1.0.3-0.20210326190908-1c3f411f0417
	github.com/pkg/errors v0.9.1
	github.com/sirupsen/logrus v1.9.3
	github.com/spf13/cobra v1.0.0
	golang.org/x/sys v0.27.0
	google.golang.org/grpc v1.51.0
	google.golang.org/protobuf v1.35.2
)

require (
	github.com/Microsoft/go-winio v0.5.2 // indirect
	github.com/Microsoft/hcsshim v0.9.4 // indirect
	github.com/cilium/ebpf v0.16.0 // indirect
	github.com/containerd/continuity v0.3.0 // indirect
	github.com/containerd/fifo v1.0.0 // indirect
	github.com/containerd/ttrpc v1.1.0 // indirect
	github.com/containerd/typeurl v1.0.2 // indirect
	github.com/coreos/go-systemd/v22 v22.5.0 // indirect
	github.com/docker/go-events v0.0.0-20190806004212-e31b211e4f1c // indirect
	github.com/docker/go-units v0.5.0 // indirect
	github.com/godbus/dbus/v5 v5.1.0 // indirect
	github.com/gogo/googleapis v1.4.0 // indirect
	github.com/gogo/protobuf v1.3.2 // indirect
	github.com/golang/groupcache v0.0.0-20210331224755-41bb18bfe9da // indirect
	github.com/golang/protobuf v1.5.2 // indirect
	github.com/inconshreveable/mousetrap v1.0.0 // indirect
	github.com/klauspost/compress v1.11.13 // indirect
	github.com/moby/locker v1.0.1 // indirect
	github.com/moby/sys/mountinfo v0.5.0 // indirect
	github.com/moby/sys/signal v0.6.0 // indirect
	github.com/opencontainers/go-digest v1.0.0 // indirect
	github.com/opencontainers/image-spec v1.0.3-0.20211202183452-c5a74bcca799 // indirect
	github.com/opencontainers/runc v1.1.2 // indirect
	github.com/opencontainers/selinux v1.10.1 // indirect
	github.com/spf13/pflag v1.0.5 // indirect
	github.com/stretchr/testify v1.8.2 // indirect
	go.opencensus.io v0.23.0 // indirect
	golang.org/x/exp v0.0.0-20241108190413-2d47ceb2692f // indirect
	golang.org/x/net v0.23.0 // indirect
	golang.org/x/sync v0.9.0 // indirect
	golang.org/x/text v0.14.0 // indirect
	google.golang.org/genproto v0.0.0-20220502173005-c8bf987b8c21 // indirect
)

replace github.com/ict/tide/proto => ./proto
