import unittest

from sim_clock.gate import Waiter

STEP = 50_000_000  # 0.05 s


def stamps(n):
    return [i * STEP for i in range(n)]


class GateTest(unittest.TestCase):

    def test_every_step(self):
        waiter = Waiter("objects")
        self.assertFalse(waiter.done(0))            # nothing reported yet
        waiter.update(STEP)
        self.assertTrue(waiter.done(STEP))
        self.assertFalse(waiter.done(2 * STEP))
        waiter.update(2 * STEP - 400_000)           # stamp rounded by another publisher
        self.assertTrue(waiter.done(2 * STEP))
        waiter.update(STEP)                         # an older stamp never moves it back
        self.assertTrue(waiter.done(2 * STEP))

    def test_periodic_member_holds_only_due_steps(self):
        """LiDAR with sensor_tick 0.1 at 0.05 s steps: publishes on every other step."""
        for phase in (0, 1):                        # it may publish on even or odd steps
            waiter = Waiter("lidar", 0.1)
            held = []
            for index, stamp in enumerate(stamps(20)):
                publishes = index % 2 == phase
                if not waiter.done(stamp):
                    held.append(index)
                    if waiter.latest_ns is not None:    # before its first output: the join wait
                        self.assertTrue(publishes, f"phase {phase}: step {index} held without an output")
                if publishes:
                    waiter.update(stamp)
                if waiter.latest_ns is not None:
                    self.assertTrue(waiter.done(stamp))     # never held once the output is in
            # after the first output, exactly the steps with an output are held
            self.assertEqual(held[1:], [i for i in range(held[1], 20) if i % 2 == phase])

    def test_periodic_member_that_falls_behind_is_held(self):
        waiter = Waiter("planner", 0.1)
        waiter.update(0)
        self.assertTrue(waiter.done(STEP))          # 0.05 s behind: fine
        self.assertFalse(waiter.done(2 * STEP))     # 0.1 s behind: a whole period, wait
        waiter.update(2 * STEP - 400_000)
        self.assertTrue(waiter.done(2 * STEP))

    def test_period_of_one_step_is_every_step(self):
        periodic, every = Waiter("a", 0.05), Waiter("b")
        for stamp in stamps(10):
            self.assertEqual(periodic.done(stamp), every.done(stamp))
            periodic.update(stamp)
            every.update(stamp)
            self.assertEqual(periodic.done(stamp), every.done(stamp))


if __name__ == "__main__":
    unittest.main()
