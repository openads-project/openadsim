import unittest

from sim_clock.pacing import Pacer, WindowStats, choose_factor, percentile


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class PacerTest(unittest.TestCase):

    def test_period_scales_with_factor(self):
        clock = FakeClock()
        pacer = Pacer(0.05, 0.25, clock.clock, clock.sleep)
        self.assertAlmostEqual(pacer.period, 0.2)
        pacer.wait()                      # first step starts the schedule
        for _ in range(3):
            self.assertTrue(pacer.wait())
        self.assertAlmostEqual(clock.now, 0.6)
        self.assertEqual(pacer.late_steps, 0)

    def test_late_step_restarts_schedule_without_burst(self):
        clock = FakeClock()
        pacer = Pacer(0.05, 1.0, clock.clock, clock.sleep)
        pacer.wait()
        clock.now += 0.5                  # a step took 0.5 s instead of 0.05 s
        self.assertFalse(pacer.wait())    # late, no sleep
        self.assertEqual(pacer.late_steps, 1)
        start = clock.now
        self.assertTrue(pacer.wait())     # the next step is one period later, not immediately
        self.assertAlmostEqual(clock.now - start, 0.05)

    def test_invalid_factor(self):
        with self.assertRaises(ValueError):
            Pacer(0.05, 0.0)


class WindowStatsTest(unittest.TestCase):

    def test_windows_and_validity(self):
        stats = WindowStats(window_s=1.0)
        sim = 0.0
        wall = 0.0
        stats.add(wall, sim, after_warmup=False)
        # 3 warm-up windows at factor 0.1, then 10 main windows at 0.25, one of them at 0.2
        for i in range(13):
            factor = 0.1 if i < 3 else (0.2 if i == 7 else 0.25)
            wall += 1.0
            sim += factor
            stats.add(wall, sim, after_warmup=i >= 3)
        self.assertEqual(len(stats.windows), 13)
        result = stats.validity(0.25, margin=0.98, fraction=0.9)
        self.assertEqual(result["windows_after_warmup"], 10)
        self.assertEqual(result["windows_valid"], 9)
        self.assertTrue(result["valid"])
        self.assertFalse(stats.validity(0.25, margin=0.98, fraction=0.99)["valid"])

    def test_restart_separates_phases(self):
        stats = WindowStats(window_s=1.0)
        stats.add(0.0, 0.0, after_warmup=False)
        stats.add(0.5, 1.0, after_warmup=False)    # fast warm-up
        stats.restart()
        stats.add(0.5, 1.0, after_warmup=True)     # starts the first main window
        stats.add(1.5, 2.0, after_warmup=True)
        self.assertEqual(len(stats.windows), 1)
        self.assertAlmostEqual(stats.windows[0].factor, 1.0)

    def test_no_windows_is_not_valid(self):
        self.assertFalse(WindowStats().validity(1.0, 0.98, 0.99)["valid"])


class ChooseFactorTest(unittest.TestCase):

    def test_largest_sustained_choice(self):
        # measured on this machine: Town10HD CARLA-SUMO about 0.39-0.43
        self.assertEqual(choose_factor([0.43, 0.41, 0.39, 0.42] * 10, 0.98), (0.25, True))
        self.assertEqual(choose_factor([1.3] * 20, 0.98), (1.0, True))
        self.assertEqual(choose_factor([0.8] * 20, 0.98), (0.75, True))
        self.assertEqual(choose_factor([0.6] * 20, 0.98), (0.5, True))

    def test_margin_is_respected(self):
        # 0.505 * 0.98 = 0.495 < 0.5, so 0.5 is not sustained
        self.assertEqual(choose_factor([0.505] * 20, 0.98), (0.25, True))

    def test_nothing_sustained(self):
        self.assertEqual(choose_factor([0.18, 0.2, 0.24] * 10, 0.98), (0.25, False))
        self.assertEqual(choose_factor([], 0.98), (0.25, False))

    def test_percentile(self):
        self.assertEqual(percentile([5, 1, 3, 2, 4], 0.0), 1)
        self.assertEqual(percentile([5, 1, 3, 2, 4], 0.5), 3)
        self.assertIsNone(percentile([], 0.5))


if __name__ == "__main__":
    unittest.main()
