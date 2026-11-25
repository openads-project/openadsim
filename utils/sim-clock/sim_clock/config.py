"""Configuration of sim-clock, read from environment variables."""

import fnmatch
import os
import re
from dataclasses import dataclass, field
from typing import Mapping, Optional

MODES = ("realtime", "gated", "fast")
BACKENDS = ("carla", "traci")
RT_FACTOR_CHOICES = (0.25, 0.5, 0.75, 1.0)
ON_TIMEOUT = ("continue", "abort")
AUTO_MIN_WARMUP_S = 10.0
AUTO_DEFAULT_WARMUP_S = 60.0   # default warm-up of SIM_RT_FACTOR=auto; other modes: none


class ConfigError(ValueError):
    """Raised for an invalid sim-clock configuration."""


def is_topic_pattern(entry: str) -> bool:
    """GATE_TOPICS entries starting with ^ are regular expressions, entries with *, ? or [ globs."""
    return entry.startswith("^") or any(c in entry for c in "*?[")


def topic_matches(pattern: str, topic: str) -> bool:
    if pattern.startswith("^"):
        return re.search(pattern, topic) is not None
    return fnmatch.fnmatchcase(topic, pattern)


def _get(env: Mapping[str, str], name: str, default: str) -> str:
    """Returns the variable, treating an empty value like an unset one."""
    value = env.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _float(env, name, default, minimum=None):
    raw = _get(env, name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _list(env, name):
    return [item.strip() for item in _get(env, name, "").split(",") if item.strip()]


def parse_member(entry: str) -> tuple:
    """A gate member `name` or `name@period`. Returns (name, period in sim seconds), period 0.0
    meaning an output is expected every step. ROS names cannot contain @, so it is unambiguous."""
    name, sep, raw = entry.rpartition("@")
    if not sep:
        return entry, 0.0
    try:
        period = float(raw)
    except ValueError as exc:
        raise ConfigError(f"gate member {entry!r}: the period after @ must be a number of sim seconds") from exc
    if period <= 0 or not name.strip():
        raise ConfigError(f"gate member {entry!r}: needs a name and a period > 0 after @")
    return name.strip(), period


def _members(env, name, periods):
    """Names of a gate member list; their periods are added to `periods`."""
    names = []
    for entry in _list(env, name):
        member, period = parse_member(entry)
        names.append(member)
        periods[member] = period
    return names


@dataclass
class Config:
    mode: Optional[str]                 # None: sim-clock is disabled
    backend: str = "carla"
    rt_factor: Optional[float] = None   # None: auto
    warmup_s: float = 0.0              # free-running sim seconds before the main phase
    step_length: float = 0.05           # sim seconds per step (CARLA fixed delta, or SUMO advance per call)
    carla_host: str = "carla-server"
    carla_port: int = 2000
    traci_host: str = "sumo-interface"
    traci_port: int = 8813
    traci_order: int = 1
    gate_participants: list = field(default_factory=list)
    gate_topics: list = field(default_factory=list)
    # name (participant, topic or topic pattern) -> longest expected sim time between two outputs;
    # 0.0: an output every step
    gate_periods: dict = field(default_factory=dict)
    gate_timeout_s: float = 10.0
    gate_on_timeout: str = "continue"
    gate_join_timeout_s: float = 300.0
    valid_margin: float = 0.98          # a window is valid if its factor >= margin * X
    valid_fraction: float = 0.99        # a run is valid if this fraction of windows is valid
    output_directory: str = ""
    stop_after_sim_s: float = 0.0       # main phase: sim seconds, 0: run until stopped

    @property
    def enabled(self) -> bool:
        return self.mode is not None


def load(env: Mapping[str, str] = os.environ) -> Config:
    mode = _get(env, "SIM_TIME_MODE", "").lower() or None
    if mode is None:
        return Config(mode=None)
    if mode not in MODES:
        raise ConfigError(f"SIM_TIME_MODE must be one of {', '.join(MODES)} or empty, got {mode!r}")

    backend = _get(env, "SIM_CLOCK_BACKEND", "carla").lower()
    if backend not in BACKENDS:
        raise ConfigError(f"SIM_CLOCK_BACKEND must be one of {', '.join(BACKENDS)}, got {backend!r}")

    raw_factor = _get(env, "SIM_RT_FACTOR", "auto").lower()
    if raw_factor == "auto":
        rt_factor = None
    else:
        rt_factor = _float(env, "SIM_RT_FACTOR", 1.0)
        if rt_factor not in RT_FACTOR_CHOICES:
            raise ConfigError(
                f"SIM_RT_FACTOR must be auto or one of {', '.join(map(str, RT_FACTOR_CHOICES))}, "
                f"got {raw_factor!r}")

    on_timeout = _get(env, "GATE_ON_TIMEOUT", "continue").lower()
    if on_timeout not in ON_TIMEOUT:
        raise ConfigError(f"GATE_ON_TIMEOUT must be one of {', '.join(ON_TIMEOUT)}, got {on_timeout!r}")

    gate_periods = {}
    config = Config(
        mode=mode,
        backend=backend,
        rt_factor=rt_factor,
        # only SIM_RT_FACTOR=auto needs a warm-up (it measures the factor there); explicit values apply to every mode
        warmup_s=_float(env, "WARMUP_S", AUTO_DEFAULT_WARMUP_S if mode == "realtime" and rt_factor is None else 0.0,
                        minimum=0.0),
        step_length=_float(env, "STEP_LENGTH", 0.05, minimum=0.001),
        carla_host=_get(env, "CARLA_HOST", "carla-server"),
        carla_port=int(_float(env, "CARLA_PORT", 2000)),
        traci_host=_get(env, "TRACI_HOST", "sumo-interface"),
        traci_port=int(_float(env, "TRACI_PORT", 8813)),
        traci_order=int(_float(env, "TRACI_ORDER", 1, minimum=1)),
        gate_participants=_members(env, "GATE_PARTICIPANTS", gate_periods),
        gate_topics=_members(env, "GATE_TOPICS", gate_periods),
        gate_periods=gate_periods,
        gate_timeout_s=_float(env, "GATE_TIMEOUT", 10.0, minimum=0.0),
        gate_on_timeout=on_timeout,
        gate_join_timeout_s=_float(env, "GATE_JOIN_TIMEOUT", 300.0, minimum=0.0),
        output_directory=_get(env, "SIM_CLOCK_OUTPUT_DIRECTORY", ""),
        stop_after_sim_s=_float(env, "SIM_CLOCK_STOP_AFTER_SIM_S", 0.0, minimum=0.0),
    )
    if backend == "carla" and _get(env, "CARLA_BRIDGE_PASSIVE", "false").lower() not in ("1", "true", "yes"):
        raise ConfigError("SIM_TIME_MODE with CARLA alone needs CARLA_BRIDGE_PASSIVE=true, otherwise "
                          "carla-ros-bridge ticks the world as well")
    if mode == "realtime" and rt_factor is None and config.warmup_s < AUTO_MIN_WARMUP_S:
        raise ConfigError(f"SIM_RT_FACTOR=auto measures the achievable factor during the warm-up: "
                          f"WARMUP_S must be at least {AUTO_MIN_WARMUP_S:g}, or set SIM_RT_FACTOR")
    if mode == "gated" and not (config.gate_participants or config.gate_topics):
        raise ConfigError("SIM_TIME_MODE=gated needs GATE_PARTICIPANTS or GATE_TOPICS")
    return config
