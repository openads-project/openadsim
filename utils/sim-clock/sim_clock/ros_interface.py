"""ROS side of sim-clock: /simulation/tick, /simulation/clock_status and the gate.

Gate protocol (standard message types only):
  /simulation/tick       std_msgs/Header   stamp = sim time of the new step, frame_id = step index
  /simulation/step_done  std_msgs/Header   stamp = sim time a participant has finished, frame_id = its id
  GATE_TOPICS            any type with a header; a message with header.stamp >= the step's stamp
                         counts as that topic having finished the step. Glob patterns (*, ?, [])
                         and regular expressions (starting with ^) add every matching topic as soon
                         as it is published.
  name@P                 (participants and topics) a member that publishes at most every P sim
                         seconds; it only holds a step when its newest stamp is P or more behind
                         (see gate.Waiter).
"""

import logging
import threading
import time
from typing import Dict, List, Optional

from builtin_interfaces.msg import Time
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from rosgraph_msgs.msg import Clock
from std_msgs.msg import Header

from .config import is_topic_pattern, topic_matches
from .gate import Waiter

LOGGER = logging.getLogger("sim-clock")

RELIABLE = QoSProfile(depth=10, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.RELIABLE)
BEST_EFFORT = QoSProfile(depth=10, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.BEST_EFFORT)


def to_ns(stamp) -> int:
    return stamp.sec * 1_000_000_000 + stamp.nanosec


def from_seconds(seconds: float) -> Time:
    ns = int(round(seconds * 1e9))
    return Time(sec=ns // 1_000_000_000, nanosec=ns % 1_000_000_000)


class SimClockNode(Node):

    def __init__(self, participants: List[str], gate_topics: List[str], use_clock: bool,
                 periods: Optional[Dict[str, float]] = None):
        super().__init__("sim_clock")
        self._cv = threading.Condition()
        self._tick_pub = self.create_publisher(Header, "/simulation/tick", RELIABLE)
        self._status_pub = self.create_publisher(DiagnosticArray, "/simulation/clock_status", RELIABLE)
        self._periods = dict(periods or {})
        self.waiters: Dict[str, Waiter] = {name: Waiter(name, self._periods.get(name, 0.0))
                                           for name in participants}
        self._patterns = {t: False for t in gate_topics if is_topic_pattern(t)}  # pattern -> matched
        for topic in gate_topics:
            if topic not in self._patterns:
                self.waiters[topic] = Waiter(topic, self._periods.get(topic, 0.0))
        self._participants = set(participants)
        self._pending_ns: Optional[int] = None
        self._pending_wall: Optional[float] = None
        self._recorded = set()
        self._waiting = set()   # members the current step waits for (not finished when it started)
        self.create_subscription(Header, "/simulation/step_done", self._on_step_done, RELIABLE)
        self._unresolved = [t for t in gate_topics if t not in self._patterns]
        self._rejected = set()  # topics whose type cannot be loaded or has no header
        if gate_topics:
            self.create_timer(1.0, self._resolve_gate_topics)
        self.clock_ns: Optional[int] = None
        if use_clock:
            self.create_subscription(Clock, "/clock", self._on_clock, BEST_EFFORT)

    # -- inputs -------------------------------------------------------------------------------

    def _on_clock(self, msg):
        with self._cv:
            self.clock_ns = to_ns(msg.clock)
            self._cv.notify_all()

    def _on_step_done(self, msg: Header):
        if msg.frame_id in self._participants:
            self._finished(msg.frame_id, to_ns(msg.stamp))

    def _resolve_gate_topics(self):
        """Subscribes to gate topics as soon as their type is known from the graph; topics matching
        a pattern become gate members when they appear."""
        if not self._unresolved and not self._patterns:
            return
        types = dict(self.get_topic_names_and_types())
        for topic in [t for t in self._unresolved if t in types]:
            self._unresolved.remove(topic)
            self._subscribe_gate_topic(topic, types[topic][0])
        for topic in sorted(types):
            matching = [p for p in self._patterns if topic_matches(p, topic)]
            if not matching or topic in self._rejected:
                continue
            if topic not in self.waiters:
                with self._cv:
                    self.waiters[topic] = Waiter(topic, self._periods.get(matching[0], 0.0))
                if not self._subscribe_gate_topic(topic, types[topic][0]):
                    with self._cv:
                        del self.waiters[topic]
                    continue
            with self._cv:
                for pattern in matching:    # every pattern that matches it has joined
                    self._patterns[pattern] = True
                self._cv.notify_all()

    def _subscribe_gate_topic(self, topic, type_name) -> bool:
        """Runs on the executor thread: an exception here would end the spin, so a topic whose
        type cannot be loaded or has no header is rejected and logged once."""
        from rosidl_runtime_py.utilities import get_message  # pylint: disable=import-outside-toplevel
        try:
            msg_type = get_message(type_name)
            if "header" not in msg_type.get_fields_and_field_types():
                raise ValueError("the type has no header, so its messages cannot be attributed to steps")
            self.create_subscription(msg_type, topic, lambda msg, t=topic: self._finished(t, to_ns(msg.header.stamp)),
                                     BEST_EFFORT)
        except Exception as exc:  # pylint: disable=broad-except
            self._rejected.add(topic)
            LOGGER.error("Gate topic %s (%s) rejected: %s", topic, type_name, exc)
            return False
        LOGGER.info("Gate topic %s (%s)", topic, type_name)
        return True

    def _finished(self, name: str, stamp_ns: int):
        with self._cv:
            waiter = self.waiters[name]
            waiter.update(stamp_ns)
            if (self._pending_ns is not None and name in self._waiting and name not in self._recorded
                    and waiter.done(self._pending_ns)):
                waiter.durations.append(time.perf_counter() - self._pending_wall)
                self._recorded.add(name)
            self._cv.notify_all()

    # -- outputs ------------------------------------------------------------------------------

    def publish_tick(self, stamp: float, index: int):
        header = Header()
        header.stamp = from_seconds(stamp)
        header.frame_id = str(index)
        self._tick_pub.publish(header)

    def publish_status(self, level: int, message: str, values: Dict[str, object], stamp: Optional[float] = None):
        """stamp: sim time of the latest step (the ROS time base of the simulation)."""
        status = DiagnosticStatus(level=bytes([level]), name="sim-clock", message=message, hardware_id="")
        status.values = [KeyValue(key=k, value=str(v)) for k, v in values.items()]
        array = DiagnosticArray()
        array.header.stamp = from_seconds(stamp) if stamp is not None else self.get_clock().now().to_msg()
        array.status = [status]
        self._status_pub.publish(array)

    # -- gate ---------------------------------------------------------------------------------

    def members(self):
        """Snapshot of (name, waiter): the executor thread adds topics that match a pattern."""
        with self._cv:
            return list(self.waiters.items())

    def reset_gate(self):
        """After a world reload the stamps start again near 0: reports of the old world must not
        finish steps of the new one."""
        with self._cv:
            for waiter in self.waiters.values():
                waiter.latest_ns = None

    def wait_joined(self, timeout: float) -> List[str]:
        """Waits until every gate member has reported at least once. Returns the missing ones."""
        with self._cv:
            self._cv.wait_for(lambda: all(self._patterns.values())
                              and all(w.latest_ns is not None for w in self.waiters.values()), timeout)
            return ([n for n, w in self.waiters.items() if w.latest_ns is None]
                    + [p for p, matched in self._patterns.items() if not matched])

    def wait_step(self, stamp: float, timeout: float) -> List[str]:
        """Waits until every gate member has finished the step with this stamp.
        Returns the members that timed out (empty on success)."""
        stamp_ns = int(round(stamp * 1e9))
        with self._cv:
            self._pending_ns, self._pending_wall = stamp_ns, time.perf_counter()
            self._recorded = set()
            self._waiting = set()
            for name, waiter in self.waiters.items():
                if not waiter.done(stamp_ns):
                    self._waiting.add(name)
                elif waiter.period_ns == 0:             # finished before the tick was published
                    waiter.durations.append(0.0)
                    self._recorded.add(name)
                # a periodic member that is not due on this step records no wait
            self._cv.wait_for(lambda: all(w.done(stamp_ns) for w in self.waiters.values()), timeout)
            missing = [n for n, w in self.waiters.items() if not w.done(stamp_ns)]
            for name in missing:
                self.waiters[name].timeouts += 1
            self._pending_ns = None
            return missing

    def wait_clock_after(self, previous_ns: Optional[int], timeout: float = 10.0) -> Optional[int]:
        with self._cv:
            self._cv.wait_for(lambda: self.clock_ns is not None and self.clock_ns != previous_ns, timeout)
            return self.clock_ns
