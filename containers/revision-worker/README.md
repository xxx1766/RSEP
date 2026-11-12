# Go worker image

The baseline systems execute one persistent Go worker per serial application
slot. `cmd/revision-worker` provides CPU, memory-access and synchronous-I/O
kernels through HTTP, PAIX or a one-shot calibration command. The domain
workload is `cmd/revision-domain-worker`.

```sh
docker build -f containers/revision-worker/Dockerfile --target worker \
  -t tmc/revision-worker:local code/tide
docker build -f containers/revision-worker/Dockerfile --target domain \
  -t tmc/revision-domain-worker:local code/tide
```

The build context is `code/tide`, because the module uses
`replace github.com/ict/tide/proto => ./proto`. The image stages build static
CGO binaries for linux/amd64 with Go 1.26.2. The module metadata declares
`go 1.22.0` and `toolchain go1.22.2`; those declarations are preserved and are
independent of the measured compiler.

Modes:

| Binary | Mode | Command | Use |
|---|---|---|---|
| `/worker` | `paix` | `--mode=paix` | Tide executor |
| `/worker` | `http` | `--mode=http --listen=0.0.0.0:19280` | Bare/Kubernetes/Ray |
| `/worker` | `once` | `--mode=once --work '<json>'` | calibration |
| `/domain-worker` | `paix` | `--mode=paix` | domain executor |
| `/domain-worker` | `http` | `--mode=http --listen=127.0.0.1:34062` | domain service |

`GOMAXPROCS=1` is set in the image. The HTTP worker exposes `/run` and
`/health`; the domain worker also exposes `--cpu-list`, `--tmpdir` and
`--node`. The measured baseline pinned the process with `taskset -c 0,1`
after start; the Pod resource request/limit does not replace that affinity
step.

The measured worker image is
`docker.io/tide/revision-worker:amd64`, manifest
`sha256:3d7cf3a3...`. A newly built image has its own digest. The OCI recipe
used by the campaign without a Docker daemon is
`code/tide/scripts/revision/build_worker_image.py`; it packages `/worker`
with `GOMAXPROCS=1` and `--mode=paix` as the default command.
