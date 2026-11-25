"""When a gate member has finished a step (no ROS imports, so it can be tested on its own)."""

from typing import List, Optional

TOLERANCE_NS = 1_000_000  # 1 ms: stamps of other publishers may be rounded differently


class Waiter:
    """One gate member (participant or topic): newest finished stamp and per-step wait durations.

    period_s 0 (default): the member has to finish every step, i.e. its newest stamp has to reach
    the step's stamp.
    
    period_s P > 0: the member produces an output at most every P sim seconds (e.g. a LiDAR with 
    sensor_tick 0.1, or a 10 Hz node). It has finished the step at stamp t when its newest stamp 
    is less than P behind t. Steps on which it is not expected to publish then pass without waiting, 
    whatever its phase, and it can never fall a whole period behind."""

    def __init__(self, name: str, period_s: float = 0.0):
        self.name = name
        self.period_ns = int(round(period_s * 1e9))
        self.latest_ns: Optional[int] = None
        self.durations: List[float] = []
        self.timeouts = 0

    def update(self, stamp_ns: int):
        if self.latest_ns is None or stamp_ns > self.latest_ns:
            self.latest_ns = stamp_ns

    def done(self, stamp_ns: int) -> bool:
        if self.latest_ns is None:
            return False
        if self.period_ns == 0:
            return self.latest_ns >= stamp_ns - TOLERANCE_NS
        return self.latest_ns >= stamp_ns - self.period_ns + TOLERANCE_NS
