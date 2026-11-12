# Experiment environment

The runtime uses Linux/amd64, cgroup v2, containerd and POSIX message queues. The native measured build used Go 1.26.2 and CGO. Go module declarations remain in the matching source trees.

| Experiment | Configuration |
|---|---|
| Native ablation, Figs.5-6 | Debian13, Linux6.12.95, Xeon Platinum8163; six application vCPUs and two driver vCPUs; YOLOX-Nano/PyTorch2.0.1 CPU, 640x640; ten detections per request; two intra-op and eight inter-op threads, one OpenCV thread; six configurations, six rates1/2/4/8/16/32req/s, five blocks; 20-s arrivals, 180-s deadline |
| Calibration, Fig.7/TableII | FIFO simulation; two local and one remote two-CPU executor;30-s runs, remote gate closed10-20s;68 weight settings, six preference profiles, three calibration and three disjoint test seeds |
| Elasticity, Fig.8a | Three hosts,12 application CPUs each;42req/s for60s;20-s baseline and360-s recovery;350-ms deadline;three runs per system;HPA60%,1-18 replicas;2CPU/512MiB requests and2CPU/1GiB limits per Pod |
| Lifecycle, Fig.8b |30 paired cold/precreated sessions;600 requests;first request followed by nine reuse requests;cached image |
| Capacity, Fig.9 | Three-node1Gbps LAN, initial RTT below1ms;four serial workers distributed2/1/1;2CPU and512MiB each;CPU,memory-access,synchronous-I/O kernels;ten load levels, five paired repetitions,10-s windows,500-ms deadlines |
| Management, Fig.12 | RSEP,KubeEdge and Ray;10/20/40/80req/s;three repeats;200-ms deadline;CPU as percent of one CPU and memory as PSS MiB;application workers excluded from role totals |
| WAN, Fig.13 | Three local and three remote nested VMs;1CPU/2GiB each;worker request900mCPU/512MiB,limit1CPU/1GiB;1Gbps,32ms+/-5ms per direction,0.1%loss,medianRTT about65ms;135-ms deadline;1000 offers per arm and pattern |
| Domain autonomy, Fig.14 | Edge1 ingress,Edge2 peer,cloud control;2CPU/512MiB per edge;2req/s at0-30s,ramp to8req/s at30-60s,then8req/s to150s;cloud-path drop20-110s;500-ms deadline;three repeats per condition |
| Native weights, TableIII | Two placement layouts;50/700/1000req/s,steady and burst arrivals;2-s arrival windows;500-ms deadline;three weight settings and five repeats;retained-run membership in the dataset |
| Mixed QoS, supplement | Four workers distributed2/1/1;CPU kernel100 SHA-256 iterations over64KiB;100/500-ms deadlines interleaved equally;actual rates116/309/463req/s,displayed120/310/460;five paired repeats,60 treatments,177600 offers |

## Runtime and workload identities

`code/reproduction/runtime-issue42/Tide/` is the runtime source for the native ablation. `code/tide/` is the maintained implementation. The release manifest fingerprints the distributed files; experiment metadata retains measured build identities.

The recorded workload image is `docker.io/tide/fig5-piei-20261010:amd64`, manifest `sha256:defacfb3b2342c9a72fbdb6504844eccadb61a234647d33c34fc02b0019daa0a`. It uses an ONNX base plus an application layer, with the native PyTorch dependency tree mounted at `/legacy`. `containers/onnx-workload/append_measured_layer.py` provides the OCI assembly step. `containers/pytorch-workload/` supplies a self-contained reconstruction.

Checkpoint SHA-256: `cd28f55fbbc1829f99d9ac9b38a16d259a22889739c8728ea877610201feff7b`.

Input SHA-256: `6818cd9ad787e555879a52a706f66652be180b9f9d62a08397d74012705a9620`.

Current KubeEdge and Ray baselines use1.23.1 and2.58.0. The historical heterogeneous results use their recorded KubeEdge1.12,Kubernetes1.23 andRay2.2 configurations. Historical setup and current runtime measurements have separate data entries.
