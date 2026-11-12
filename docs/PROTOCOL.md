# Measurement protocol

## Source and configuration

Use the runtime source, workload, application budget and arrival configuration listed for the experiment in `CURRENT_ENVIRONMENT.md`. Host templates contain logical cloud/edge identities. Configure the experiment modules before starting live drivers. The offline figure and table commands read only packaged data.

Native configuration order and resource settings are in `experiments/current/native_pytorch/frozen/formal-plan.json`. Native queues use10 messages and262144 bytes per message. Both `tide` and `default` containerd namespaces are configured for this campaign. Application vCPUs are2-7;driver vCPUs are0-1. C1/C2 use HTTP;C3-C6 use native Stubs and Executors. Each request runs ten complete detections on the same input.

The elasticity plan is `experiments/current/elasticity/plan-v2.json`. CPU reservations are reported relative to each system's baseline. Three paired blocks produce the median curves and minimum/maximum bands. Lifecycle records form30 separate cold/precreated pairs;reuse statistics use the later requests in those sessions.

## Metrics and aggregation

Deadline yield divides correct timely returns by all offered requests. Failed,rejected,timed-out and late requests retain their cohort membership. Successful-return latency is conditional on correct completed requests. Native ablation throughput divides valid returns by the complete client observation interval, including its bounded drain.

Capacity and mixed-QoS goodput count timely returns inside the10-s observation window and divide by10s. Mixed-QoS rows use each request's own deadline;Mixed totals sum the two class means. Display rates for mixed QoS round the actual116/309/463req/s to120/310/460.

WAN goodput uses270.135s for gradual arrivals and240.135s for burst arrivals. Each arm receives1000 offers. The two traces and their checksums are retained in the run metadata. Remote-path shaping is32ms+/-5ms in each direction with0.1%loss and1Gbps.

Domain panel(a) bins timely returns by return time. Panel(b) groups offered requests into5-s arrival cohorts. The outage comparisons retain the Edge1-Edge2 path while dropping the cloud paths from20s to110s.

Run summaries are computed before averaging repetitions. Paired comparisons retain block pairing. Pointwise95%Student t intervals use the recorded repetition count. Historical figure assets and case-summary values retain their original measurement identities.

## Build and deployment

See `REPRODUCTION.md` for container builds and `../deploy/README.md` for baseline templates. The deployment renderer writes a separate output directory. Live commands operate only on a configured reproduction testbed.
