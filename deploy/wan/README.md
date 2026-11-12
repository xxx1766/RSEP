# WAN delay baseline

The WAN runs added a controlled round-trip delay between the client and the
worker nodes with Linux `tc netem`. The measured campaign applied a root
qdisc on both the host veth and the pod-side interface, so each direction
carried the one-way delay.

| Target RTT | One-way delay | Jitter | Loss | Rate |
|---|---|---|---|---|
| 25 ms | 12 ms | 5 ms | 0.1 % | 1 Gbit |
| 65 ms | 32 ms | 5 ms | 0.1 % | 1 Gbit |

The measured qdisc shape was:

```text
qdisc netem 22: root refcnt ... limit 100000 delay 32ms 5ms loss 0.1% rate 1Gbit
```

`netem.sh` applies the same shape with parameterized delivery values.

```sh
IFACES="vethc727d22f eth0" DELAY_MS=32 sh deploy/wan/netem.sh apply
IFACES="vethc727d22f eth0" sh deploy/wan/netem.sh show
IFACES="vethc727d22f eth0" sh deploy/wan/netem.sh remove
```

Apply the rule to every interface that carries the client/worker path, and
record `tc -s qdisc show` in the run evidence. Do not leave a qdisc installed
after a run: `remove` is idempotent and ignores a missing root qdisc.

`netem` requires `CAP_NET_ADMIN`. The measured runs used the host network
namespace; if the workload uses a private namespace, apply the rule inside
that namespace and keep the recorded interface name with the run metadata.
