import unittest

from issue12_netns import NetworkPlan, build_plan, validate_run_id


class Issue12NamespacePlanTests(unittest.TestCase):
    def test_only_namespace_veth_receives_netem(self):
        plan = build_plan("smoke12", 39, 29582)
        commands = [" ".join(command) for command in plan.setup]
        self.assertIn("netem delay 39ms 5ms loss 0.1%", "\n".join(commands))
        self.assertTrue(any("ip netns exec t12-smoke12 tc qdisc add dev n12smoke12" in c for c in commands))
        self.assertFalse(any("tc qdisc" in c and "dev eth0" in c for c in commands))
        self.assertTrue(any("-s 10.212.12.2/32 -o eth0" in c for c in commands))
        self.assertTrue(any("--dport 29582" in c and "DNAT" in c for c in commands))

    def test_rejects_invalid_names_ports_and_delay(self):
        for value in ("", "../../etc", "abcdefghijklmnop", "bad space"):
            with self.assertRaises(ValueError):
                validate_run_id(value)
        for delay, port in ((-1, 29582), (39, 80), (39, 65536)):
            with self.assertRaises(ValueError):
                build_plan("smoke12", delay, port)

    def test_teardown_reverses_owned_actions(self):
        plan = build_plan("smoke12", 39, 29582)
        self.assertIsInstance(plan, NetworkPlan)
        self.assertEqual(plan.setup[0], ["ip", "netns", "add", "t12-smoke12"])
        self.assertEqual(plan.teardown[-1], ["ip", "netns", "del", "t12-smoke12"])
        self.assertTrue(any(c[:4] == ["iptables", "-t", "nat", "-D"] for c in plan.teardown))
        self.assertEqual(len(set(map(tuple, plan.setup))), len(plan.setup))


if __name__ == "__main__":
    unittest.main()
