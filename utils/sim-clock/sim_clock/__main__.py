"""Entry point: python3 -m sim_clock (configuration from the environment)."""

import json
import logging
import os
import signal
import sys
import threading
import time

from .config import ConfigError, load
from .pacing import Pacer, WindowStats, choose_factor, percentile

__version__ = "0.1.0"

LOGGER = logging.getLogger("sim-clock")
EXIT_CONFIG, EXIT_GATE_ABORT, EXIT_ERROR = 2, 3, 1
EPS_S = 1e-6    # sim seconds are sums of step lengths


def make_backend(cfg):
    from .backends import CarlaBackend, TraciBackend  # pylint: disable=import-outside-toplevel
    if cfg.backend == "carla":
        return CarlaBackend(cfg.carla_host, cfg.carla_port, cfg.step_length)
    return TraciBackend(cfg.traci_host, cfg.traci_port, cfg.traci_order, cfg.step_length)


def stats_ms(values):
    if not values:
        return {"n": 0}
    return {"n": len(values), "p50_ms": round(1000 * percentile(values, 0.5), 1),
            "p95_ms": round(1000 * percentile(values, 0.95), 1), "max_ms": round(1000 * max(values), 1)}


class Run:
    """State and bookkeeping of one sim-clock run."""

    def __init__(self, cfg, node, backend, stop=None):
        self.cfg, self.node, self.backend = cfg, node, backend
        self.stop = stop or threading.Event()
        self.windows = WindowStats()
        self.factor = cfg.rt_factor
        self.factor_sustained = None
        self.auto_measured = []
        self.phase = "warmup"
        self.steps = 0
        self.late_steps = 0
        self.lockstep = []          # CARLA frames per step, from the backend's checks
        self.sim_s = 0.0            # sim seconds since the first step, continuous across CARLA world reloads
        self.last_stamp = None
        self.started_wall = time.time()
        self.abort_reason = None
        self._last_status = 0.0

    # -- bookkeeping --------------------------------------------------------------------------

    def record(self, result):
        if self.last_stamp is not None:
            # a reloaded CARLA world starts its stamps again near 0
            self.sim_s += self.cfg.step_length if result.reset else result.stamp - self.last_stamp
        if result.reset:
            self.node.reset_gate()      # reports of the old world must not finish steps of the new one
        self.steps += 1
        self.last_stamp = result.stamp
        if result.frames is not None:
            self.lockstep.append(result.frames)
        window = self.windows.add(time.perf_counter(), self.sim_s, after_warmup=self.phase == "main")
        self.node.publish_tick(result.stamp, result.index)
        now = time.perf_counter()
        if now - self._last_status >= 1.0:
            self._last_status = now
            self.publish_status(window)

    def recent_factor(self, n=10):
        recent = self.windows.recent_factors(n)
        return sum(recent) / len(recent) if recent else None

    def publish_status(self, window=None):
        recent = self.recent_factor()
        values = {
            "mode": self.cfg.mode, "phase": self.phase, "backend": self.cfg.backend,
            "target_factor": self.factor if self.factor is not None else "auto",
            "factor_1s": round(window.factor, 3) if window else "",
            "factor_10s": round(recent, 3) if recent is not None else "",
            "steps": self.steps, "late_steps": self.late_steps,
        }
        for name, waiter in self.node.members():
            values[f"gate.{name}.p50_ms"] = stats_ms(waiter.durations[-200:]).get("p50_ms", "")
            values[f"gate.{name}.timeouts"] = waiter.timeouts
        level = 0
        if self.phase == "main" and self.cfg.mode == "realtime" and window is not None \
                and window.factor < self.cfg.valid_margin * self.factor:
            level = 1  # WARN: this window does not reach the target factor
        self.node.publish_status(level, f"{self.cfg.mode}/{self.phase}", values, self.last_stamp)

    def summary(self):
        data = {
            "sim_clock_version": __version__, "mode": self.cfg.mode, "backend": self.cfg.backend,
            "step_length_s": self.cfg.step_length, "warmup_s": self.cfg.warmup_s,
            "rt_factor": self.factor, "rt_factor_requested": self.cfg.rt_factor if self.cfg.rt_factor else "auto",
            "steps": self.steps, "late_steps": self.late_steps,
            "sim_s": round(self.sim_s, 3),
            "wall_s": round(time.time() - self.started_wall, 3),
            "abort_reason": self.abort_reason,
        }
        if self.cfg.rt_factor is None and self.cfg.mode == "realtime":
            data["auto"] = {"measured_factor_p05": percentile(self.auto_measured, 0.05),
                            "measured_windows": len(self.auto_measured), "sustained": self.factor_sustained}
        if self.cfg.mode == "realtime" and self.factor:
            data["validity"] = self.windows.validity(self.factor, self.cfg.valid_margin, self.cfg.valid_fraction)
        main = [w for w in self.windows.windows if w.after_warmup]
        if main:
            data["achieved_factor_main"] = {
                "mean": round(sum(w.sim_s for w in main) / sum(w.wall_s for w in main), 4),
                "p05": round(percentile([w.factor for w in main], 0.05), 4),
            }
        data["gate"] = {name: dict(stats_ms(w.durations), timeouts=w.timeouts) for name, w in self.node.members()}
        if self.lockstep:
            data["carla_frames_per_step"] = {"min": round(min(self.lockstep), 4),
                                             "max": round(max(self.lockstep), 4), "checks": len(self.lockstep)}
        if hasattr(self.backend, "resets"):
            data["carla_world_resets"] = self.backend.resets
        return data

    # -- phases -------------------------------------------------------------------------------

    def warmup(self):
        """Runs free (no pacing, no gate) for warmup_s sim seconds; measures the achievable factor."""
        if self.cfg.warmup_s <= 0:
            LOGGER.info("No warm-up")
            return
        LOGGER.info("Warm-up: %.0f sim seconds without pacing or gate", self.cfg.warmup_s)
        start = None
        while not self.stop.is_set():
            self.record(self.backend.step())
            start = self.sim_s if start is None else start
            if self.sim_s - start >= self.cfg.warmup_s - EPS_S:
                break
        # the later half of the warm-up, when the traffic has been inserted
        n = max(1, len(self.windows.windows) // 2)
        self.auto_measured = self.windows.recent_factors(n)

    def choose_factor(self):
        if self.cfg.mode != "realtime":
            return
        if self.cfg.rt_factor is not None:
            self.factor = self.cfg.rt_factor
            return
        self.factor, self.factor_sustained = choose_factor(self.auto_measured, self.cfg.valid_margin)
        p05 = percentile(self.auto_measured, 0.05)
        LOGGER.info("Auto real-time factor: %.2f (free-running factor p05 %.3f over %d windows)%s",
                    self.factor, p05 or 0, len(self.auto_measured),
                    "" if self.factor_sustained else " - NOT sustained, the run will not be valid")

    def join(self):
        """Gated: steps once per wall second, without gate, until every member has reported once.
        Members only report after an input, i.e. after a step, so waiting without stepping could
        never finish (e.g. without warm-up); stepping slowly keeps a slow-starting member from
        missing much."""
        self.phase = "join"
        self.publish_status()   # announce the phase now: e.g. a recorder can start on it, before it joins the gate
        deadline = time.perf_counter() + self.cfg.gate_join_timeout_s
        missing = self.node.wait_joined(0.0)
        if missing:
            LOGGER.info("Waiting for gate members to report once: %s", missing)
        while missing and not self.stop.is_set() and time.perf_counter() < deadline:
            self.record(self.backend.step())
            missing = self.node.wait_joined(min(1.0, max(0.0, deadline - time.perf_counter())))
        if missing:
            LOGGER.warning("Gate members without any report after %.0f s: %s", self.cfg.gate_join_timeout_s, missing)

    def main(self):
        cfg = self.cfg
        if cfg.mode == "gated":
            self.join()
        self.phase = "main"
        self.windows.restart()
        LOGGER.info("Main phase: %s%s", cfg.mode, f" at factor {self.factor}" if cfg.mode == "realtime" else "")
        self.publish_status()
        pacer = Pacer(cfg.step_length, self.factor) if cfg.mode == "realtime" else None
        last_log = time.perf_counter()
        # the first step of a run is at sim_s 0, so without an earlier step the main phase starts one step before it
        main_start = self.sim_s if self.steps else -cfg.step_length
        while not self.stop.is_set():
            if cfg.stop_after_sim_s and self.steps and self.sim_s - main_start >= cfg.stop_after_sim_s - EPS_S:
                LOGGER.info("Main phase: %.1f sim seconds done", cfg.stop_after_sim_s)
                break
            if pacer is not None and not pacer.wait():
                self.late_steps += 1
                if self.late_steps <= 20 or self.late_steps % 100 == 0:
                    LOGGER.info("Late step %d at sim %.2f s", self.late_steps, self.sim_s)
            result = self.backend.step()
            self.record(result)
            if cfg.mode == "gated":
                missing = self.node.wait_step(result.stamp, cfg.gate_timeout_s)
                if missing:
                    LOGGER.warning("Gate timeout (%.1f s) at %.3f s: %s", cfg.gate_timeout_s, result.stamp, missing)
                    if cfg.gate_on_timeout == "abort":
                        self.abort_reason = f"gate timeout: {missing}"
                        return EXIT_GATE_ABORT
            if time.perf_counter() - last_log >= 10.0:
                last_log = time.perf_counter()
                LOGGER.info("steps=%d factor(10s)=%.3f late=%d", self.steps, self.recent_factor() or 0,
                            self.late_steps)
        return 0


def write_summary(cfg, data):
    text = json.dumps(data, indent=2)
    LOGGER.info("Summary:\n%s", text)
    if cfg.output_directory:
        os.makedirs(cfg.output_directory, exist_ok=True)
        path = os.path.join(cfg.output_directory, "clock_summary.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        LOGGER.info("Wrote %s", path)


def idle(stop, reason):
    """Stays up until the stack is stopped. An exiting container would end a whole
    `docker compose up --abort-on-container-exit` (scenario-execution.py)."""
    LOGGER.info("%s; idling until the stack is stopped", reason)
    while not stop.wait(1.0):
        pass


def run_clock(cfg, stop) -> int:
    import rclpy  # pylint: disable=import-outside-toplevel
    from rclpy.executors import SingleThreadedExecutor  # pylint: disable=import-outside-toplevel
    from rclpy.signals import SignalHandlerOptions  # pylint: disable=import-outside-toplevel
    from .ros_interface import SimClockNode  # pylint: disable=import-outside-toplevel

    # main() handles SIGTERM/SIGINT: rclpy's own handlers would shut the context down before the summary
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    gated = cfg.mode == "gated"
    node = SimClockNode(cfg.gate_participants if gated else [], cfg.gate_topics if gated else [],
                        use_clock=cfg.backend == "traci", periods=cfg.gate_periods)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()

    backend = None
    run = None
    code = 0
    try:
        backend = make_backend(cfg)
        run = Run(cfg, node, backend, stop)
        if cfg.backend == "traci":
            # SUMO alone: the ROS stamp base is sumo_interface's /clock
            before = node.clock_ns
            backend.step()
            clock_ns = node.wait_clock_after(before)
            if clock_ns is None:
                LOGGER.warning("No /clock after a SUMO step; stamps use SUMO time")
            else:
                offset = clock_ns / 1e9 - backend.sumo_time
                backend.set_stamp_offset(offset)
                LOGGER.info("ROS stamp base: /clock = SUMO time + %.3f s", offset)
        run.warmup()
        run.choose_factor()
        code = run.main()
    except Exception as exc:  # pylint: disable=broad-except
        LOGGER.error("sim-clock failed: %s", exc, exc_info=True)
        code = EXIT_ERROR
        if run is not None:
            run.abort_reason = str(exc)
    finally:
        if run is not None:
            write_summary(cfg, run.summary())
        if backend is not None:
            backend.close()
        # join the spin thread before shutting down, otherwise its native thread aborts the process
        executor.shutdown()
        spin_thread.join(5.0)
        node.destroy_node()
        rclpy.try_shutdown()
    return code


def main() -> int:
    logging.basicConfig(format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                        level=logging.INFO, datefmt="%H:%M:%S")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    try:
        cfg = load()
    except ConfigError as exc:
        LOGGER.error("%s", exc)
        return EXIT_CONFIG
    if not cfg.enabled:
        idle(stop, "SIM_TIME_MODE is empty: sim-clock is disabled, the simulators keep their own timing")
        return 0
    code = run_clock(cfg, stop)
    if code == 0 and not stop.is_set():
        idle(stop, "Run finished, the simulators keep their own timing")
    return code


if __name__ == "__main__":
    sys.exit(main())
