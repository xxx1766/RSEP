#!/usr/bin/env python3
"""Create an issue-12-only BJ network path; never reshape shared eth0."""

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Sequence


BJ_PUBLIC = "9.0.3.35"
BJ_VETH = "10.212.12.1"
NS_VETH = "10.212.12.2"
SUBNET = "10.212.12.0/30"
NJ_SOURCE = "11.0.4.0/24"


def validate_run_id(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9]{1,10}", value):
        raise ValueError("run-id must contain 1–10 lowercase ASCII letters/digits")
    return value


@dataclass(frozen=True)
class NetworkPlan:
    run_id: str
    namespace: str
    host_veth: str
    namespace_veth: str
    delay_ms: int
    jitter_ms: int
    loss_pct: float
    callback_port: int
    setup: list[list[str]]
    teardown: list[list[str]]


def build_plan(run_id: str, delay_ms: int, callback_port: int,
               jitter_ms: int = 5, loss_pct: float = 0.1) -> NetworkPlan:
    validate_run_id(run_id)
    if (not 0 <= delay_ms <= 100 or not 0 <= jitter_ms <= 20 or
            loss_pct not in (0.0, 0.1) or not 20000 <= callback_port <= 60000):
        raise ValueError("delay 0–100 ms, jitter 0–20 ms, loss 0/0.1%, port 20000–60000 required")
    ns, host, peer = f"t12-{run_id}", f"h12{run_id}", f"n12{run_id}"
    tag = f"tide-issue12-{run_id}"
    nat_out = ["-s", f"{NS_VETH}/32", "-o", "eth0", "-m", "comment", "--comment", tag, "-j", "MASQUERADE"]
    nat_in = ["-i", "eth0", "-p", "tcp", "-s", NJ_SOURCE, "-d", BJ_PUBLIC,
              "--dport", str(callback_port), "-m", "comment", "--comment", tag,
              "-j", "DNAT", "--to-destination", f"{NS_VETH}:{callback_port}"]
    forward_out = ["-i", host, "-o", "eth0", "-s", f"{NS_VETH}/32",
                   "-m", "comment", "--comment", tag, "-j", "ACCEPT"]
    forward_return = ["-i", "eth0", "-o", host, "-d", f"{NS_VETH}/32",
                      "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED",
                      "-m", "comment", "--comment", tag, "-j", "ACCEPT"]
    forward_callback = ["-i", "eth0", "-o", host, "-p", "tcp", "-s", NJ_SOURCE,
                        "-d", f"{NS_VETH}/32", "--dport", str(callback_port),
                        "-m", "comment", "--comment", tag, "-j", "ACCEPT"]
    rules = [(["iptables", "-t", "nat"], "POSTROUTING", nat_out),
             (["iptables", "-t", "nat"], "PREROUTING", nat_in),
             (["iptables"], "FORWARD", forward_out),
             (["iptables"], "FORWARD", forward_return),
             (["iptables"], "FORWARD", forward_callback)]
    setup = [
        ["ip", "netns", "add", ns],
        ["ip", "link", "add", host, "type", "veth", "peer", "name", peer],
        ["ip", "link", "set", peer, "netns", ns],
        ["ip", "addr", "add", f"{BJ_VETH}/30", "dev", host],
        ["ip", "link", "set", host, "up"],
        ["ip", "netns", "exec", ns, "ip", "addr", "add", f"{NS_VETH}/30", "dev", peer],
        ["ip", "netns", "exec", ns, "ip", "link", "set", peer, "up"],
        ["ip", "netns", "exec", ns, "ip", "link", "set", "lo", "up"],
        ["ip", "netns", "exec", ns, "ip", "route", "add", "default", "via", BJ_VETH],
    ]
    setup += [prefix + ["-A", chain] + rule for prefix, chain, rule in rules]
    setup += [["ip", "netns", "exec", ns, "tc", "qdisc", "add", "dev", peer, "root",
               "netem", "delay", f"{delay_ms}ms", f"{jitter_ms}ms", "loss", f"{loss_pct:g}%"]]
    teardown = [prefix + ["-D", chain] + rule for prefix, chain, rule in reversed(rules)]
    teardown += [["ip", "netns", "exec", ns, "tc", "qdisc", "del", "dev", peer, "root"],
                 ["ip", "link", "del", host], ["ip", "netns", "del", ns]]
    return NetworkPlan(run_id, ns, host, peer, delay_ms, jitter_ms, loss_pct,
                       callback_port, setup, teardown)


def capture(args: Sequence[str]) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


def preflight(plan: NetworkPlan) -> dict:
    if subprocess.run(["id", "-u"], capture_output=True, text=True, check=True).stdout.strip() != "0":
        raise RuntimeError("root is required")
    namespace_list = capture(["ip", "netns", "list"])
    links = capture(["ip", "-j", "link", "show"])
    routes = capture(["ip", "route", "show", SUBNET])
    ports = capture(["ss", "-H", "-ltn", "sport", "=", f":{plan.callback_port}"])
    firewall = capture(["iptables-save"])
    if (plan.namespace in namespace_list or plan.host_veth in links or
            plan.namespace_veth in links or routes.strip() or ports.strip() or
            f"tide-issue12-{plan.run_id}" in firewall):
        raise RuntimeError("namespace, veth, route, callback port or firewall tag already exists")
    if capture(["cat", "/proc/sys/net/ipv4/ip_forward"]).strip() != "1":
        raise RuntimeError("IP forwarding must already be enabled; refusing to change global state")
    return {"namespace_list": namespace_list, "links": links, "routes": routes,
            "port_listeners": ports, "firewall": firewall,
            "eth0_qdisc": capture(["tc", "-s", "qdisc", "show", "dev", "eth0"]),
            "route_to_nj": capture(["ip", "route", "get", "11.0.4.48"])}


def execute(command: list[str], *, allow_failure: bool = False) -> dict:
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    row = {"argv": command, "exit_code": result.returncode, "output": result.stdout}
    if result.returncode and not allow_failure:
        raise RuntimeError(json.dumps(row))
    return row


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("plan", "setup", "status", "teardown"))
    p.add_argument("--run-id", required=True)
    p.add_argument("--delay-ms", type=int, default=0)
    p.add_argument("--jitter-ms", type=int, default=5)
    p.add_argument("--loss-pct", type=float, default=0.1)
    p.add_argument("--callback-port", type=int, default=29582)
    p.add_argument("--root", type=Path, default=Path("/tmp/tide-issue12-20261007"))
    a = p.parse_args()
    plan = build_plan(a.run_id, a.delay_ms, a.callback_port, a.jitter_ms, a.loss_pct)
    manifest = a.root / f"network-{a.run_id}.json"
    if a.action == "plan":
        print(json.dumps(asdict(plan), indent=2))
        return
    if a.action == "status":
        print(json.dumps({"namespace": capture(["ip", "netns", "list"]),
                          "eth0": capture(["tc", "-s", "qdisc", "show", "dev", "eth0"]),
                          "manifest": json.loads(manifest.read_text()) if manifest.exists() else None}, indent=2))
        return
    if a.action == "setup":
        if manifest.exists():
            raise RuntimeError(f"refusing to overwrite {manifest}")
        before = preflight(plan)
        journal = {"plan": asdict(plan), "before": before, "status": "starting", "actions": []}
        save(manifest, journal)
        try:
            for command in plan.setup:
                row = execute(command)
                journal["actions"].append(row)
                save(manifest, journal)
        except BaseException:
            journal["status"] = "setup_failed"
            save(manifest, journal)
            raise
        journal["status"] = "active"
        save(manifest, journal)
        print(manifest)
        return
    if not manifest.exists():
        raise RuntimeError(f"no owned setup manifest at {manifest}")
    journal = json.loads(manifest.read_text())
    if journal["plan"] != asdict(plan):
        raise RuntimeError("arguments differ from recorded network plan")
    if journal["status"] == "removed":
        print(manifest)
        return
    journal["cleanup"] = [execute(command, allow_failure=True) for command in plan.teardown]
    journal["after"] = {"namespace": capture(["ip", "netns", "list"]),
                        "eth0_qdisc": capture(["tc", "-s", "qdisc", "show", "dev", "eth0"]),
                        "firewall": capture(["iptables-save"])}
    journal["status"] = "removed" if all(row["exit_code"] == 0 for row in journal["cleanup"]) else "cleanup_incomplete"
    save(manifest, journal)
    if journal["status"] != "removed":
        raise RuntimeError(f"cleanup incomplete; inspect {manifest}")
    print(manifest)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"issue12_netns: {error}", file=sys.stderr)
        raise SystemExit(1)
