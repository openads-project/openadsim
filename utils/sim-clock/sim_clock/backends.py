"""Backends that advance the simulation by one step.

Each backend returns the stamp of the new step in the time base of the ROS messages, so that
acknowledgements and output stamps of the system under test can be compared with it."""

import logging
import time
from typing import Optional

LOGGER = logging.getLogger("sim-clock")


class StepResult:
    __slots__ = ("stamp", "index", "frames", "reset")

    def __init__(self, stamp: float, index: int, frames: Optional[int] = None, reset: bool = False):
        self.stamp = stamp      # sim time of the new step in the ROS time base [s]
        self.index = index      # step counter (CARLA frame or SUMO step)
        self.frames = frames    # CARLA frames per step since the last lockstep check (1.0 = lockstep), if known
        self.reset = reset      # the world was reloaded: the stamps started again


class CarlaBackend:
    """Ticks a CARLA world in synchronous mode. The ROS bridge must be passive."""

    def __init__(self, host: str, port: int, step_length: float, timeout: float = 30.0):
        import carla  # pylint: disable=import-outside-toplevel
        self._client = carla.Client(host, port)
        self._client.set_timeout(timeout)
        self._world = self._client.get_world()
        self._step_length = step_length
        self._check_no_other_tick_owner()
        self._apply_settings()
        self._last_frame = None
        self._last_elapsed = None
        self.resets = 0

    def _apply_settings(self):
        settings = self._world.get_settings()
        settings.synchronous_mode = True
        settings.fixed_delta_seconds = self._step_length
        self._world.apply_settings(settings)
        LOGGER.info("CARLA %s: synchronous mode, fixed delta %.3f s",
                    self._world.get_map().name, self._step_length)

    def _check_no_other_tick_owner(self, wait_s: float = 2.0):
        """In synchronous mode the world must not advance without us; otherwise another client
        (typically an active ROS bridge) also ticks it."""
        if not self._world.get_settings().synchronous_mode:
            return
        frame = self._world.get_snapshot().frame
        time.sleep(wait_s)
        advanced = self._world.get_snapshot().frame - frame
        if advanced > 0:
            raise RuntimeError(
                f"The CARLA world advanced {advanced} frames in {wait_s:.0f} s without sim-clock: "
                "another client ticks it. Start carla-ros-bridge with PASSIVE=true.")

    def step(self) -> StepResult:
        frame = self._world.tick()
        timestamp = self._world.get_snapshot().timestamp
        reset = (abs(timestamp.delta_seconds - self._step_length) > 1e-6
                 or (self._last_elapsed is not None and timestamp.elapsed_seconds < self._last_elapsed))
        if reset:
            # A map (re)load, by carla-ros-bridge at its start or by a scenario, starts a new episode:
            # its elapsed time starts again near 0 and it may be back in asynchronous mode.
            self.resets += 1
            LOGGER.warning("CARLA world reloaded (step %.4f s, elapsed %.3f s): applying synchronous settings again",
                           timestamp.delta_seconds, timestamp.elapsed_seconds)
            self._world = self._client.get_world()
            self._apply_settings()
            self._last_frame = None
            frame = self._world.tick()
            timestamp = self._world.get_snapshot().timestamp
        frames = None if self._last_frame is None else frame - self._last_frame
        self._last_frame = frame
        self._last_elapsed = timestamp.elapsed_seconds
        return StepResult(timestamp.elapsed_seconds, frame, frames, reset)

    def close(self):
        """Leaves the world in asynchronous mode, so it keeps running without a tick master."""
        try:
            settings = self._world.get_settings()
            settings.synchronous_mode = False
            settings.fixed_delta_seconds = None
            self._world.apply_settings(settings)
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.warning("Could not reset CARLA to asynchronous mode: %s", exc)


class TraciBackend:
    """Steps SUMO as an additional TraCI client of sumo_interface's TraCI server.

    The ROS stamp base is sumo_interface's /clock: the caller measures its offset to SUMO time
    once (set_stamp_offset)."""

    def __init__(self, host: str, port: int, order: int, step_length: float, connect_timeout_s: float = 600.0):
        import traci  # pylint: disable=import-outside-toplevel
        LOGGER.info("Connecting to SUMO at %s:%d as TraCI client %d", host, port, order)
        traci.init(host=host, port=port, numRetries=max(1, int(connect_timeout_s)), label="sim-clock")
        self._conn = traci.getConnection("sim-clock")
        self._conn.setOrder(order)
        delta = self._conn.simulation.getDeltaT()
        # advance per call: whole SUMO steps, at least one
        self._steps_per_call = max(1, round(step_length / delta))
        self._advance = self._steps_per_call * delta
        LOGGER.info("SUMO step %.3f s, sim-clock advances %d step(s) = %.3f s per call",
                    delta, self._steps_per_call, self._advance)
        self._index = 0
        self._offset = 0.0

    @property
    def sumo_time(self) -> float:
        return self._conn.simulation.getTime()

    def set_stamp_offset(self, offset: float):
        self._offset = offset

    def step(self) -> StepResult:
        self._conn.simulationStep(round(self.sumo_time + self._advance, 6))
        self._index += self._steps_per_call
        return StepResult(self.sumo_time + self._offset, self._index)

    def close(self):
        try:
            self._conn.close()
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.warning("Closing the TraCI connection failed: %s", exc)
