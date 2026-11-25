"""Backends that advance the simulation by one step.

Each backend returns the stamp of the new step in the time base of the ROS messages, so that
acknowledgements and output stamps of the system under test can be compared with it."""

import logging
import threading
import time
from typing import Optional

LOGGER = logging.getLogger("sim-clock")


class StepResult:
    __slots__ = ("stamp", "index", "frames")

    def __init__(self, stamp: float, index: int, frames: Optional[int] = None):
        self.stamp = stamp      # sim time of the new step in the ROS time base [s]
        self.index = index      # step counter (CARLA frame or SUMO step)
        self.frames = frames    # CARLA frames per step since the last lockstep check (1.0 = lockstep), if known


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
        snapshot = self._world.get_snapshot()
        if abs(snapshot.timestamp.delta_seconds - self._step_length) > 1e-6:
            # A map (re)load, e.g. by carla-ros-bridge at its start, resets the world to
            # asynchronous mode: take the new world and apply the settings again.
            self.resets += 1
            LOGGER.warning("CARLA step was %.4f s instead of %.3f s (world reloaded or settings "
                           "changed): applying synchronous settings again",
                           snapshot.timestamp.delta_seconds, self._step_length)
            self._world = self._client.get_world()
            self._apply_settings()
            self._last_frame = None
            frame = self._world.tick()
            snapshot = self._world.get_snapshot()
        frames = None if self._last_frame is None else frame - self._last_frame
        self._last_frame = frame
        return StepResult(snapshot.timestamp.elapsed_seconds, frame, frames)

    def close(self):
        """Leaves the world in asynchronous mode, so it keeps running without a tick master."""
        try:
            settings = self._world.get_settings()
            settings.synchronous_mode = False
            settings.fixed_delta_seconds = None
            self._world.apply_settings(settings)
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.warning("Could not reset CARLA to asynchronous mode: %s", exc)


class TraciBackend:  # pylint: disable=too-many-instance-attributes
    """Steps SUMO as an additional TraCI client.

    CARLA-SUMO: the coordinator's SUMO waits for this client (sumo_num_clients=2), so the
    coordinator only syncs and ticks CARLA when sim-clock steps. SUMO alone: sumo_interface's
    TraCI server waits for this client.

    The ROS stamp base: with stamp_source=carla the offset between CARLA's elapsed time and SUMO
    time is read from the CARLA world (CARLA-SUMO); with stamp_source=clock it is read from /clock
    by the caller (set_stamp_offset)."""

    def __init__(self, host: str, port: int, order: int, step_length: float,
                 carla_host: Optional[str] = None, carla_port: int = 2000, connect_timeout_s: float = 600.0):
        import traci  # pylint: disable=import-outside-toplevel
        self._traci = traci
        retries = max(1, int(connect_timeout_s))
        LOGGER.info("Connecting to SUMO at %s:%d as TraCI client %d", host, port, order)
        traci.init(host=host, port=port, numRetries=retries, label="sim-clock")
        self._conn = traci.getConnection("sim-clock")
        self._conn.setOrder(order)
        self._delta = self._conn.simulation.getDeltaT()
        # advance per call: whole SUMO steps, at least one
        self._steps_per_call = max(1, round(step_length / self._delta))
        self._advance = self._steps_per_call * self._delta
        LOGGER.info("SUMO step %.3f s, sim-clock advances %d step(s) = %.3f s per call",
                    self._delta, self._steps_per_call, self._advance)
        self._index = 0
        self._offset: Optional[float] = None
        self._world = None
        self._latest = None             # (frame, elapsed) of the newest CARLA frame
        self._frame_cv = threading.Condition()
        self._check_every = 200         # steps between lockstep checks
        self._check_frame = None
        if carla_host:
            import carla  # pylint: disable=import-outside-toplevel
            client = carla.Client(carla_host, carla_port)
            client.set_timeout(30.0)
            self._world = client.get_world()
            self._tick_id = self._world.on_tick(self._on_carla_tick)

    def _on_carla_tick(self, snapshot):
        with self._frame_cv:
            self._latest = (snapshot.frame, snapshot.timestamp.elapsed_seconds)
            self._frame_cv.notify_all()

    def _carla_frame_after(self, frame_before, timeout=10.0):
        """The coordinator ticks CARLA right after the SUMO step. Waits for a frame newer than
        frame_before; the callback records it even if the tick happened before this call."""
        with self._frame_cv:
            ok = self._frame_cv.wait_for(
                lambda: self._latest is not None and (frame_before is None or self._latest[0] > frame_before),
                timeout=timeout)
            return self._latest if ok else None

    @property
    def sumo_time(self) -> float:
        return self._conn.simulation.getTime()

    def set_stamp_offset(self, offset: float):
        self._offset = offset

    def step(self) -> StepResult:
        check = self._world is not None and (self._offset is None or self._index % self._check_every == 0)
        with self._frame_cv:
            frame_before = self._latest[0] if self._latest else None
        target = self.sumo_time + self._advance
        self._conn.simulationStep(round(target, 6))
        self._index += self._steps_per_call
        sumo_time = self.sumo_time
        frames = None
        if check:
            # measure the stamp offset once, and check lockstep every _check_every steps
            latest = self._carla_frame_after(frame_before)
            if latest is None:
                LOGGER.warning("No CARLA frame within 10 s after a SUMO step")
            else:
                frame, elapsed = latest
                offset = elapsed - sumo_time
                if self._offset is None:
                    self._offset = offset
                    LOGGER.info("ROS stamp base: CARLA elapsed = SUMO time + %.3f s", offset)
                elif abs(offset - self._offset) > 0.5 * self._delta:
                    LOGGER.warning("CARLA drifted against SUMO by %.3f s: another client ticks "
                                   "CARLA (carla-ros-bridge must be passive)", offset - self._offset)
                if self._check_frame is not None:
                    frames = (frame - self._check_frame) / self._check_every
                self._check_frame = frame
        return StepResult(sumo_time + (self._offset or 0.0), self._index, frames)

    def close(self):
        if self._world is not None:
            try:
                self._world.remove_on_tick(self._tick_id)
            except Exception:  # pylint: disable=broad-except
                pass
        try:
            self._conn.close()
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.warning("Closing the TraCI connection failed: %s", exc)
