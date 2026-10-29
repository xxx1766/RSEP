#!/usr/bin/env python3
"""Owned NJ-k1 origin path; shape only NJ-k2 traffic on dedicated veths."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import time

HOST = "11.0.4.47"
REMOTE = "11.0.4.48"
GATEWAY = "10.212.13.1"
ORIGIN = "10.212.13.2"
SUBNET = "10.212.13.0/30"


def shape_commands(plan, delay_ms, jitter_ms, loss_pct, operation="change"):
    if not 0 <= delay_ms <= 100 or not 0 <= jitter_ms <= 20 or loss_pct not in (0, 0.1):
        raise ValueError("bounded delay/jitter and loss 0 or 0.1 required")
    commands = []
    for prefix, device in (([], plan["host_veth"]),
                           (["ip", "netns", "exec", plan["namespace"]], plan["namespace_veth"])):
        commands.append(prefix + ["tc", "qdisc", operation, "dev", device,
                        "parent", "1:3", "handle", "30:", "netem", "limit", "10000",
                        "delay", f"{delay_ms:g}ms", f"{jitter_ms:g}ms",
                        "loss", f"{loss_pct:g}%", "rate", "1gbit"])
    return commands


def build_plan(run_id, delay_ms, callback_port, jitter_ms=5, loss_pct=0.1):
    if not re.fullmatch(r"[a-z0-9]{1,8}", run_id) or not 20000 <= callback_port <= 60000:
        raise ValueError("short lowercase run ID and experiment port required")
    ns, host, peer = f"t13-{run_id}", f"h13{run_id}", f"n13{run_id}"
    tag = f"tide-issue12-nj-{run_id}"
    plan = dict(run_id=run_id, namespace=ns, host_veth=host, namespace_veth=peer,
                subnet=SUBNET, origin=ORIGIN, host=HOST, remote=REMOTE,
                delay_ms=delay_ms, jitter_ms=jitter_ms, loss_pct=loss_pct,
                callback_port=callback_port, tag=tag, rate="1gbit")
    comment = ["-m", "comment", "--comment", tag]
    rules = [
        (["iptables", "-t", "nat"], "POSTROUTING",
         ["-s", ORIGIN + "/32", "-d", REMOTE + "/32", "-o", "eth0"] + comment + ["-j", "MASQUERADE"]),
        (["iptables", "-t", "nat"], "PREROUTING",
         ["-i", "eth0", "-s", REMOTE + "/32", "-d", HOST, "-p", "tcp", "--dport", str(callback_port)]
         + comment + ["-j", "DNAT", "--to-destination", f"{ORIGIN}:{callback_port}"]),
        (["iptables"], "FORWARD",
         ["-i", host, "-o", "eth0", "-s", ORIGIN + "/32", "-d", REMOTE + "/32"] + comment + ["-j", "ACCEPT"]),
        (["iptables"], "FORWARD",
         ["-i", "eth0", "-o", host, "-s", REMOTE + "/32", "-d", ORIGIN + "/32"] + comment + ["-j", "ACCEPT"]),
    ]
    setup = [["ip", "netns", "add", ns],
             ["ip", "link", "add", host, "type", "veth", "peer", "name", peer],
             ["ip", "link", "set", peer, "netns", ns],
             ["ip", "addr", "add", GATEWAY + "/30", "dev", host],
             ["ip", "link", "set", host, "up"],
             ["ip", "netns", "exec", ns, "ip", "addr", "add", ORIGIN + "/30", "dev", peer],
             ["ip", "netns", "exec", ns, "ip", "link", "set", peer, "up"],
             ["ip", "netns", "exec", ns, "ip", "link", "set", "lo", "up"],
             ["ip", "netns", "exec", ns, "ip", "route", "add", "default", "via", GATEWAY]]
    setup += [prefix + ["-A", chain] + args for prefix, chain, args in rules]
    for prefix, device, direction in (([], host, "src"), (["ip", "netns", "exec", ns], peer, "dst")):
        setup.append(prefix + ["tc", "qdisc", "add", "dev", device, "root", "handle", "1:",
                               "prio", "bands", "3", "priomap"] + ["0"] * 16)
        setup.append(prefix + ["tc", "filter", "add", "dev", device, "protocol", "ip",
                               "parent", "1:", "prio", "1", "u32", "match", "ip", direction,
                               REMOTE + "/32", "flowid", "1:3"])
    setup += shape_commands(plan, delay_ms, jitter_ms, loss_pct, "add")
    # Preserve packet order: netem's default time-ordered queue can otherwise
    # introduce substantial reordering from jitter alone (tc-netem(8)).
    for prefix, device in (([], host), (["ip", "netns", "exec", ns], peer)):
        setup.append(prefix + ["tc", "qdisc", "add", "dev", device, "parent", "30:1",
                               "handle", "31:", "pfifo", "limit", "10000"])
    teardown = [prefix + ["-D", chain] + args for prefix, chain, args in reversed(rules)]
    teardown += [["ip", "link", "del", host], ["ip", "netns", "del", ns]]
    plan.update(setup=setup, teardown=teardown)
    return plan


def capture(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


def execute(args):
    r = subprocess.run(args, text=True, capture_output=True)
    return dict(argv=args, exit_code=r.returncode, stdout=r.stdout, stderr=r.stderr,
                unix_ns=time.time_ns())


def snapshot(plan):
    p = ["ip", "netns", "exec", plan["namespace"]]
    return {"unix_ns": time.time_ns(),
            "shared_eth0": capture(["tc", "qdisc", "show", "dev", "eth0"]),
            "host_qdisc": capture(["tc", "-s", "qdisc", "show", "dev", plan["host_veth"]]),
            "origin_qdisc": capture(p + ["tc", "-s", "qdisc", "show", "dev", plan["namespace_veth"]]),
            "host_route": capture(["ip", "route", "get", REMOTE]),
            "origin_route": capture(p + ["ip", "route", "get", REMOTE])}


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("plan", "setup", "shape", "status", "teardown"))
    p.add_argument("--run-id", default="nj01")
    p.add_argument("--delay-ms", type=float, default=32)
    p.add_argument("--jitter-ms", type=float, default=5)
    p.add_argument("--loss-pct", type=float, default=0.1)
    p.add_argument("--callback-port", type=int, default=29682)
    p.add_argument("--root", type=Path, default=Path("/tmp/tide-issue12-nj-20261009"))
    a = p.parse_args()
    plan = build_plan(a.run_id, a.delay_ms, a.callback_port, a.jitter_ms, a.loss_pct)
    if a.action == "plan":
        print(json.dumps(plan, indent=2))
        return
    a.root.mkdir(parents=True, exist_ok=True)
    path = a.root / f"network-{a.run_id}.json"
    if a.action == "setup":
        if path.exists():
            raise RuntimeError("refusing to overwrite existing journal")
        if (capture(["id", "-u"]).strip() != "0" or
                capture(["cat", "/proc/sys/net/ipv4/ip_forward"]).strip() != "1"):
            raise RuntimeError("root and already enabled forwarding required")
        before = dict(namespaces=capture(["ip", "netns", "list"]),
                      links=capture(["ip", "-j", "link", "show"]),
                      routes=capture(["ip", "route", "show", SUBNET]),
                      firewall=capture(["iptables-save"]),
                      port=capture(["ss", "-H", "-ltn", "sport", "=", f":{a.callback_port}"]),
                      shared_eth0=capture(["tc", "qdisc", "show", "dev", "eth0"]))
        if (plan["namespace"] in before["namespaces"] or plan["host_veth"] in before["links"] or
                before["routes"].strip() or before["port"].strip() or plan["tag"] in before["firewall"]):
            raise RuntimeError("owned namespace/link/subnet/tag/port collision")
        journal = dict(plan=plan, before=before, status="starting", actions=[])
        save(path, journal)
        for cmd in plan["setup"]:
            row = execute(cmd)
            journal["actions"].append(row)
            if row["exit_code"]:
                journal["status"] = "setup_failed"
                save(path, journal)
                raise RuntimeError(row)
            save(path, journal)
        journal.update(status="active", after_setup=snapshot(plan))
    else:
        journal = json.loads(path.read_text())
        plan = journal["plan"]
        if a.action == "status":
            print(json.dumps(dict(status=journal["status"], live=snapshot(plan)), indent=2))
            return
        if a.action == "shape":
            if journal["status"] != "active":
                raise RuntimeError("active owned network required")
            change = dict(before=snapshot(plan), commands=[])
            journal.setdefault("changes", []).append(change)
            for cmd in shape_commands(plan, a.delay_ms, a.jitter_ms, a.loss_pct):
                row = execute(cmd)
                change["commands"].append(row)
                save(path, journal)
                if row["exit_code"]:
                    raise RuntimeError(row)
            change["after"] = snapshot(plan)
        elif journal["status"] != "removed":
            # Remove only the exact additions that this journal confirms succeeded.
            successful = [row["argv"] for row in journal["actions"] if row["exit_code"] == 0]
            journal["cleanup"] = []
            for cmd in plan["teardown"]:
                added = ["-A" if part == "-D" else part for part in cmd]
                owned = added in successful if cmd[0] == "iptables" else (
                    plan["setup"][1] in successful if cmd[:3] == ["ip", "link", "del"] else plan["setup"][0] in successful)
                if owned:
                    journal["cleanup"].append(execute(cmd))
                    save(path, journal)
            journal["status"] = "removed" if all(r["exit_code"] == 0 for r in journal["cleanup"]) else "cleanup_incomplete"
            journal["after_cleanup"] = dict(namespaces=capture(["ip", "netns", "list"]),
                firewall=capture(["iptables-save"]), shared_eth0=capture(["tc", "qdisc", "show", "dev", "eth0"]))
    save(path, journal)
    print(json.dumps(dict(path=str(path), status=journal["status"])))


if __name__ == "__main__":
    main()
