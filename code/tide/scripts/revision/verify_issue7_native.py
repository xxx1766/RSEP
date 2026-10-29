#!/usr/bin/env python3
"""Audit the bounded native C4 endpoint-fault experiment from retained raw rows."""
import argparse
from collections import Counter
import json
from pathlib import Path


def verify(root: Path):
    run = json.loads((root / "outcomes.json").read_text())
    rows = run["outcomes"]
    events = [json.loads(line) for line in (root / "remote-events.jsonl").read_text().splitlines()]
    assert len(rows) == 13 and len({r["ID"] for r in rows}) == 13
    assert len(events) == 9 and len({e["request_id"] for e in events}) == 9
    assert len(run["transitions"]) == 7
    assert len(run["test_binary_sha256"]) == 64
    assert run["config"]["c4_functions"] == {"local": True, "remote": True}
    assert all(e["critical"] and e["local_node_id"] == run["origin"] for e in events)
    counts = Counter((r["Phase"], r["RequestedRoute"], r["Status"]) for r in rows)
    assert counts == Counter({
        ("connected", "remote", "3"): 3,
        ("disconnected", "local", "3"): 3,
        ("disconnected", "remote-unanswered", "unanswered"): 3,
        ("recovered", "remote", "3"): 3,
        ("C4-screen", "C4-rejected", "4"): 1,
    }), counts
    checksums = {r["Result"] for r in rows if r["Status"] == "3"}
    assert len(checksums) == 1 and next(iter(checksums)).isdigit()
    assert all(r["ObservedReceiver"] == run["remote_stub_id"] for r in rows if r["RequestedRoute"] == "remote")
    assert all(r["ObservedReceiver"] == run["source_stub_id"] for r in rows if r["RequestedRoute"] == "local")
    blocked = {e["request_id"] for e in events if e["blocked"]}
    unanswered = {r["ID"] for r in rows if r["Status"] == "unanswered"}
    assert blocked == unanswered and len(blocked) == 3
    allowed = {e["request_id"] for e in events if not e["blocked"]}
    completed_remote = {r["ID"] for r in rows if r["RequestedRoute"] == "remote"}
    assert allowed == completed_remote and len(allowed) == 6
    assert "insufficient-local-CPU" not in {e["request_id"] for e in events}
    assert all(r["StartedNS"] <= r["EndedNS"] for r in rows)
    by_id = {r["ID"]: r for r in rows}
    event_by_id = {e["request_id"]: e for e in events}
    for cycle in range(1, 4):
        disconnected = run["transitions"][2 * cycle - 1]["time_ns"]
        recovered = run["transitions"][2 * cycle]["time_ns"]
        assert event_by_id[f"{cycle}-connected"]["time_ns"] < disconnected
        assert disconnected <= event_by_id[f"{cycle}-remote-fault"]["time_ns"] < recovered
        assert event_by_id[f"{cycle}-recovered"]["time_ns"] >= recovered
        local = by_id[f"{cycle}-local"]
        assert disconnected <= local["StartedNS"] <= local["EndedNS"] < recovered
        fault = by_id[f"{cycle}-remote-fault"]
        assert disconnected <= fault["StartedNS"] <= fault["EndedNS"] < recovered
    print(f"verified: {len(rows)} offered, {len(allowed)} remote completed, "
          f"3 local during outage, {len(blocked)} remote unanswered, 1 C4 rejected")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    verify(parser.parse_args().root)
