"""Run with a fake simulator and a fake gate (no ROS)."""
import unittest

from sim_clock.__main__ import Run
from sim_clock.backends import StepResult
from sim_clock.config import load

STEP = 0.05


class FakeBackend:
    def __init__(self):
        self.steps = 0

    def step(self):
        self.steps += 1
        return StepResult(round(self.steps * STEP, 6), self.steps)


class FakeNode:
    """A gate whose member reports once `reports_after` steps have happened."""

    def __init__(self, backend, reports_after):
        self.backend, self.reports_after = backend, reports_after
        self.waiters = {}
        self.resets = 0
        self.ticks, self.phases, self.gated_steps = [], [], []

    def publish_tick(self, stamp, index):
        self.ticks.append(stamp)

    def publish_status(self, level, message, values, stamp=None):
        self.phases.append(values["phase"])

    def members(self):
        return list(self.waiters.items())

    def reset_gate(self):
        self.resets += 1

    def wait_joined(self, timeout):
        return [] if self.backend.steps >= self.reports_after else ["member"]

    def wait_step(self, stamp, timeout):
        self.gated_steps.append(stamp)
        return []


def run(env, reports_after=0):
    cfg = load(dict({"CARLA_BRIDGE_PASSIVE": "true", "SIM_CLOCK_STOP_AFTER_SIM_S": "1"}, **env))
    backend = FakeBackend()
    node = FakeNode(backend, reports_after)
    r = Run(cfg, node, backend)
    r.warmup()
    r.choose_factor()
    r.main()
    return backend, node, r


class RunTest(unittest.TestCase):

    def test_gated_without_warmup_is_gated_from_the_first_step(self):
        backend, node, r = run({"SIM_TIME_MODE": "gated", "GATE_TOPICS": "/x"})
        self.assertEqual(r.cfg.warmup_s, 0.0)
        self.assertEqual(len(node.gated_steps), backend.steps)      # every step was gated
        self.assertAlmostEqual(r.last_stamp - node.gated_steps[0], 1.0 - STEP, places=6)

    def test_join_steps_until_the_members_reported(self):
        backend, node, r = run({"SIM_TIME_MODE": "gated", "GATE_TOPICS": "/x"}, reports_after=3)
        self.assertIn("join", node.phases)
        self.assertEqual(backend.steps - len(node.gated_steps), 3)  # three ungated join steps, then gated
        self.assertEqual(node.phases[-1], "main")

    def test_fast_without_warmup_stops_after_sim_seconds(self):
        backend, node, r = run({"SIM_TIME_MODE": "fast"})
        self.assertEqual(node.gated_steps, [])
        self.assertAlmostEqual(r.last_stamp, 1.0, places=6)

    def test_explicit_warmup_runs_before_the_gate(self):
        backend, node, r = run({"SIM_TIME_MODE": "gated", "GATE_TOPICS": "/x", "WARMUP_S": "0.5"})
        self.assertEqual(backend.steps - len(node.gated_steps), 11)  # 0.05 .. 0.55 s free, then gated


    def test_world_reload_keeps_sim_time_and_resets_the_gate(self):
        class ReloadingBackend(FakeBackend):
            def step(self):
                self.steps += 1
                if self.steps == 5:     # a new episode: stamps start again
                    return StepResult(STEP, self.steps, reset=True)
                stamp = self.steps if self.steps < 5 else self.steps - 4
                return StepResult(round(stamp * STEP, 6), self.steps)

        cfg = load({"CARLA_BRIDGE_PASSIVE": "true", "SIM_TIME_MODE": "fast", "SIM_CLOCK_STOP_AFTER_SIM_S": "1"})
        backend = ReloadingBackend()
        node = FakeNode(backend, 0)
        r = Run(cfg, node, backend)
        r.main()
        self.assertEqual(node.resets, 1)
        self.assertEqual(backend.steps, 20)             # 1 sim second although the stamps restarted
        self.assertAlmostEqual(r.sim_s, 1.0 - STEP, places=6)
        self.assertTrue(all(w.sim_s > 0 for w in r.windows.windows))


if __name__ == "__main__":
    unittest.main()
