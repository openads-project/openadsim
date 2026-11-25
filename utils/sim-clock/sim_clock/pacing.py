"""Wall-clock pacing at a real-time factor, window statistics, and the auto choice of the factor."""

import math
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .config import RT_FACTOR_CHOICES


class Pacer:
    """Schedules steps at `step_length / factor` wall seconds.

    A step that is late restarts the schedule from now, so the simulation never catches up with a
    burst of fast steps; late steps are counted instead."""

    def __init__(self, step_length: float, factor: float, clock: Callable[[], float] = time.perf_counter,
                 sleep: Callable[[float], None] = time.sleep):
        if factor <= 0:
            raise ValueError("factor must be > 0")
        self.period = step_length / factor
        self._clock = clock
        self._sleep = sleep
        self._next: Optional[float] = None

    def wait(self) -> bool:
        """Blocks until the next step is due. Returns False if the step is late."""
        now = self._clock()
        if self._next is None:
            self._next = now
            return True
        self._next += self.period
        delay = self._next - now
        if delay > 0:
            self._sleep(delay)
            return True
        self._next = now
        return False


@dataclass
class Window:
    start_wall: float
    sim_s: float
    wall_s: float
    after_warmup: bool

    @property
    def factor(self) -> float:
        return self.sim_s / self.wall_s if self.wall_s > 0 else math.inf


@dataclass
class WindowStats:
    """Achieved real-time factor in wall-clock windows (default 1 s)."""

    window_s: float = 1.0
    windows: List[Window] = field(default_factory=list)
    _start_wall: Optional[float] = None
    _start_sim: Optional[float] = None

    def add(self, wall: float, sim: float, after_warmup: bool) -> Optional[Window]:
        """Records a step at (wall, sim). Returns the closed window, if one was closed."""
        if self._start_wall is None:
            self._start_wall, self._start_sim = wall, sim
            return None
        if wall - self._start_wall < self.window_s:
            return None
        window = Window(self._start_wall, sim - self._start_sim, wall - self._start_wall, after_warmup)
        self.windows.append(window)
        self._start_wall, self._start_sim = wall, sim
        return window

    def restart(self):
        """Starts a new window with the next step, e.g. when the phase changes, so that no window
        mixes two phases."""
        self._start_wall = self._start_sim = None

    def recent_factors(self, n: int) -> List[float]:
        return [w.factor for w in self.windows[-n:]]

    def validity(self, target: float, margin: float, fraction: float) -> dict:
        """Fraction of windows after the warm-up whose factor reached margin * target."""
        relevant = [w for w in self.windows if w.after_warmup]
        valid = [w for w in relevant if w.factor >= margin * target]
        share = len(valid) / len(relevant) if relevant else None
        return {
            "windows_after_warmup": len(relevant),
            "windows_valid": len(valid),
            "valid_share": round(share, 4) if share is not None else None,
            "required_share": fraction,
            "threshold_factor": round(margin * target, 4),
            "valid": bool(relevant) and share >= fraction,
        }


def percentile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(math.floor(q * (len(ordered) - 1)))))
    return ordered[index]


def choose_factor(measured: List[float], margin: float, choices=RT_FACTOR_CHOICES) -> tuple:
    """Largest factor X of `choices` that the measured free-running factors sustain.

    The 5th percentile of the measured window factors has to reach X / margin, so that paced runs
    keep at least margin * X. Returns (X, sustained). Without a sustained choice the smallest
    choice is returned with sustained=False."""
    p05 = percentile(measured, 0.05)
    if p05 is None:
        return min(choices), False
    for factor in sorted(choices, reverse=True):
        if p05 * margin >= factor:
            return factor, True
    return min(choices), False
