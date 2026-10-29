import unittest

try:
    from issue12_nj_netns import build_plan
except ImportError:
    build_plan = None


class NJNetworkIsolationTests(unittest.TestCase):
    def plan(self):
        self.assertIsNotNone(build_plan, "NJ-specific isolated network planner is missing")
        return build_plan("nj01", 32, 29682, jitter_ms=5, loss_pct=0.1)

    def test_only_owned_veths_can_be_shaped(self):
        p = self.plan()
        tc = [cmd for cmd in p["setup"] if "tc" in cmd]
        self.assertTrue(tc)
        for cmd in tc:
            self.assertIn(cmd[cmd.index("dev") + 1], (p["host_veth"], p["namespace_veth"]))
        self.assertEqual(sum("netem" in cmd for cmd in tc), 2)
        for cmd in tc:
            if "netem" in cmd:
                self.assertEqual(cmd[cmd.index("rate") + 1], "1gbit")

    def test_remote_only_filters_leave_local_path_unshaped(self):
        p = self.plan()
        filters = [cmd for cmd in p["setup"] if "filter" in cmd]
        self.assertEqual(len(filters), 2)
        self.assertTrue(any("dst" in cmd and "11.0.4.48/32" in cmd for cmd in filters))
        self.assertTrue(any("src" in cmd and "11.0.4.48/32" in cmd for cmd in filters))
        roots = [cmd for cmd in p["setup"] if "qdisc" in cmd and "prio" in cmd]
        self.assertEqual(len(roots), 2)
        for cmd in roots:
            self.assertEqual(cmd[cmd.index("priomap") + 1:], ["0"] * 16)

    def test_firewall_teardown_matches_additions(self):
        p = self.plan()
        for cmd in p["setup"]:
            if cmd[0] == "iptables":
                remove = ["-D" if part == "-A" else part for part in cmd]
                self.assertIn(remove, p["teardown"])
        self.assertEqual(p["subnet"], "10.212.13.0/30")

    def test_invalid_inputs_are_rejected(self):
        self.plan()
        for args in (("../bad", 32, 29682), ("valid", -1, 29682), ("valid", 32, 22)):
            with self.assertRaises(ValueError):
                build_plan(*args)

    def test_jitter_does_not_intentionally_reorder_packets(self):
        p = self.plan()
        fifos = [cmd for cmd in p["setup"] if "pfifo" in cmd]
        self.assertEqual(len(fifos), 2)
        for cmd in fifos:
            self.assertEqual(cmd[cmd.index("parent") + 1], "30:1")


if __name__ == "__main__":
    unittest.main()
