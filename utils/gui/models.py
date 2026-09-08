"""Domain models and metadata for simulation configuration.

This module intentionally contains structure/defaults/ENV mapping and UI metadata.
Domain dependency checks are implemented in `checks.py`.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, ClassVar, Optional

from pydantic import BaseModel, Field
from utils import (
    discover_files_with_suffix,
    filter_files_by_import_bundle,
    filter_xosc_files_by_map,
    first_import_bundle,
    import_bundle_directory,
)


class Vehicle(str, Enum):
    KARL = "karl"
    SHUTTLE = "shuttle"


class PrebuiltMap(str, Enum):
    ALDENHOVEN = "aldenhoven"
    CAMPUS = "campus"
    TOWN01 = "town01"
    TOWN04 = "town04"
    TOWN05 = "town05"
    TOWN10 = "Town10HD_Opt"


class Rmw(str, Enum):
    ZENOH = "zenoh"
    FASTRTPS = "fastrtps"


class SimulationProfile(str, Enum):
    CARLA = "carla"
    SUMO = "sumo"


class TrafficProfile(str, Enum):
    DISABLED = "no-traffic"
    ENABLED = "traffic"


class TestingProfile(str, Enum):
    DISABLED = "no-testing"
    MANUAL = "manual-testing"
    AUTOMATED = "automated-testing"


class PerceptionProfile(str, Enum):
    DISABLED = "no-perception"
    ENABLED = "perception"


class PlanningProfile(str, Enum):
    CLASSIC = "planning"


GLOBAL_SENSOR_FILE = "global-sensors.json"


def env_export(env_name: str):
    def decorator(func: Callable[..., Any]):
        setattr(func, "__env_name__", env_name)
        return func
    return decorator


@dataclass(frozen=True)
class PrebuiltMapDefaults:
    map_name: str
    description: str
    default_spawn_point: str


PREBUILT_MAP_DEFAULTS: dict[PrebuiltMap, PrebuiltMapDefaults] = {
    PrebuiltMap.ALDENHOVEN: PrebuiltMapDefaults(
        map_name="aldenhoven",
        description="Aldenhoven Testing Center",
        default_spawn_point="-1.0,-154.0,1.0,238.0",
    ),
    PrebuiltMap.CAMPUS: PrebuiltMapDefaults(
        map_name="campus",
        description="Aachen Campus Melaten",
        default_spawn_point="50.787510,6.045938,260.0,90.0,wgs84",
    ),
    PrebuiltMap.TOWN10: PrebuiltMapDefaults(
        map_name="Town10HD_Opt",
        description="CARLA Town10",
        default_spawn_point="-18.4,-130.2,0.2,180.0",
    ),
}

CARLA_PREBUILT_MAPS: tuple[PrebuiltMap, ...] = (
    PrebuiltMap.ALDENHOVEN,
    PrebuiltMap.CAMPUS,
    PrebuiltMap.TOWN10,
)

SUMO_PREBUILT_MAPS: tuple[PrebuiltMap, ...] = (
    PrebuiltMap.CAMPUS,
)

CARLA_VEHICLES: tuple[Vehicle, ...] = (
    Vehicle.KARL,
    Vehicle.SHUTTLE,
)

VEHICLE_SENSOR_OPTIONS: dict[Vehicle, list[str]] = {
    Vehicle.KARL: [
        "karl-minimal.json",
        "karl-basic.json",
        "karl-all.json",
    ],
    Vehicle.SHUTTLE: [
        "shuttle-minimal.json",
        "shuttle-basic.json",
        "shuttle-all.json",
    ],
}


def is_vehicle_sensor_entry(entry: str) -> bool:
    normalized = entry.strip().lower()
    return normalized.startswith("karl-") or normalized.startswith("shuttle-")


@dataclass(frozen=True)
class UiField:
    path: str
    env_name: str
    label: str
    description: str
    control: str
    options: tuple[str, ...] = ()
    disabled: bool = False
    none_as_empty: bool = False
    empty_option_label: str = ""
    strict_options: bool = False


@dataclass(frozen=True)
class UiSection:
    title: str
    description: str
    fields: tuple[UiField, ...]


class VehicleConfig(BaseModel):
    ENV_EXCLUDE: ClassVar[set[str]] = {
        "global_sensors_enabled",
        "vehicle_sensor_file",
        "custom_sensor_file",
    }

    vehicle: Vehicle = Vehicle.KARL
    global_sensors_enabled: bool = True

    # Exactly one vehicle sensor file from VEHICLE_SENSOR_OPTIONS.
    vehicle_sensor_file: str = "karl-minimal.json"

    # Optional additional sensor config appended to SENSORS.
    custom_sensor_file: str = ""

    def available_vehicle_sensor_files(self) -> list[str]:
        return VEHICLE_SENSOR_OPTIONS[self.vehicle]

    @staticmethod
    def default_vehicle_sensor_file(vehicle: Vehicle) -> str:
        available = VEHICLE_SENSOR_OPTIONS.get(vehicle, [])
        return available[0] if available else ""

    @staticmethod
    def minimal_vehicle_sensor_file(vehicle: Vehicle) -> str:
        return f"{vehicle.value}-minimal.json"

    @staticmethod
    def default_perception_vehicle_sensor_file(vehicle: Vehicle) -> str:
        minimal_sensor_file = VehicleConfig.minimal_vehicle_sensor_file(vehicle)
        available = VEHICLE_SENSOR_OPTIONS.get(vehicle, [])
        for sensor_file in available:
            if sensor_file != minimal_sensor_file:
                return sensor_file
        return available[0] if available else ""

    @staticmethod
    def _extract_vehicle_sensor_file(raw_value: str) -> str:
        parts = [entry.strip() for entry in raw_value.split(",") if entry.strip()]
        if not parts:
            return ""

        for entry in parts:
            if is_vehicle_sensor_entry(entry):
                return entry

        return parts[0]

    def _normalize_custom_sensor_file(self):
        self.custom_sensor_file = self.custom_sensor_file.strip()
        if self.custom_sensor_file in {GLOBAL_SENSOR_FILE, self.vehicle_sensor_file}:
            self.custom_sensor_file = ""

    def normalize(self):
        vehicle_sensor_file = self._extract_vehicle_sensor_file(self.vehicle_sensor_file)
        if not vehicle_sensor_file:
            vehicle_sensor_file = self.default_vehicle_sensor_file(self.vehicle)

        self.vehicle_sensor_file = vehicle_sensor_file
        self._normalize_custom_sensor_file()

    def align_to_vehicle_capabilities(self):
        vehicle_sensor_file = self._extract_vehicle_sensor_file(self.vehicle_sensor_file)
        if not vehicle_sensor_file or vehicle_sensor_file not in self.available_vehicle_sensor_files():
            vehicle_sensor_file = self.default_vehicle_sensor_file(self.vehicle)

        self.vehicle_sensor_file = vehicle_sensor_file
        self._normalize_custom_sensor_file()

    def ensure_perception_sensor_file(self):
        vehicle_sensor_file = self._extract_vehicle_sensor_file(self.vehicle_sensor_file)
        minimal_sensor_file = self.minimal_vehicle_sensor_file(self.vehicle)
        if not vehicle_sensor_file or vehicle_sensor_file == minimal_sensor_file:
            vehicle_sensor_file = self.default_perception_vehicle_sensor_file(self.vehicle)

        self.vehicle_sensor_file = vehicle_sensor_file
        self._normalize_custom_sensor_file()

    def build_sensors_env(self) -> str:
        parts: list[str] = []
        if self.global_sensors_enabled:
            parts.append(GLOBAL_SENSOR_FILE)

        if self.vehicle_sensor_file.strip():
            parts.append(self.vehicle_sensor_file.strip())
        if self.custom_sensor_file.strip():
            parts.append(self.custom_sensor_file.strip())

        deduplicated: list[str] = []
        for entry in parts:
            if entry not in deduplicated:
                deduplicated.append(entry)

        return ",".join(deduplicated)

    @env_export("SENSORS")
    def export_sensors(self) -> str:
        return self.build_sensors_env()

class MapConfig(BaseModel):
    ENV_EXCLUDE: ClassVar[set[str]] = {"prebuilt_map", "map_name"}

    prebuilt_map: Optional[PrebuiltMap] = PrebuiltMap.CAMPUS

    map_name: str = ""
    custom_opendrive: str = ""
    custom_lanelet: str = ""

    spawn_point: str = PREBUILT_MAP_DEFAULTS[PrebuiltMap.CAMPUS].default_spawn_point
    origin_lat: Optional[float] = None
    origin_lon: Optional[float] = None

    @property
    def uses_custom_opendrive(self) -> bool:
        return bool(self.custom_opendrive.strip())

    @property
    def uses_custom_lanelet(self) -> bool:
        return bool(self.custom_lanelet.strip())

    @property
    def effective_lanelet_reload(self) -> bool:
        return not self.uses_custom_opendrive and not self.uses_custom_lanelet

    @env_export("LANELET_RELOAD")
    def export_lanelet_reload(self) -> str:
        return "true" if self.effective_lanelet_reload else "false"

    @env_export("MAP")
    def export_map(self) -> str:
        return self.map_name

    @property
    def origin_is_editable(self) -> bool:
        return self.uses_custom_lanelet or self.uses_custom_opendrive

    def normalize(self):
        self.custom_opendrive = self.custom_opendrive.strip()
        self.custom_lanelet = self.custom_lanelet.strip()
        self.map_name = self.map_name.strip()
        self.spawn_point = self.spawn_point.strip()

        if self.prebuilt_map is None:
            self.map_name = ""
            if not self.spawn_point:
                self.spawn_point = ""
            return

        defaults = PREBUILT_MAP_DEFAULTS[self.prebuilt_map]
        if not self.map_name:
            self.map_name = defaults.map_name
        if not self.spawn_point:
            self.spawn_point = defaults.default_spawn_point

    def apply_ui_constraints(self, trigger_path: str):
        prebuilt_default_spawns = {
            defaults.default_spawn_point for defaults in PREBUILT_MAP_DEFAULTS.values()
        }

        if trigger_path == "map.prebuilt_map":
            if self.prebuilt_map is None:
                self.map_name = ""
                if self.spawn_point in prebuilt_default_spawns:
                    self.spawn_point = ""
                return
            self.custom_opendrive = ""
            self.custom_lanelet = ""
            defaults = PREBUILT_MAP_DEFAULTS[self.prebuilt_map]
            self.map_name = defaults.map_name
            self.spawn_point = defaults.default_spawn_point

        if trigger_path == "map.custom_opendrive":
            if self.uses_custom_opendrive and self.prebuilt_map is None:
                if self.spawn_point in prebuilt_default_spawns:
                    self.spawn_point = ""
                self.map_name = ""

    def align_to_simulation(self, simulation: SimulationProfile):
        if simulation == SimulationProfile.SUMO:
            if self.prebuilt_map not in SUMO_PREBUILT_MAPS:
                self.prebuilt_map = PrebuiltMap.CAMPUS
                self.apply_ui_constraints("map.prebuilt_map")
            self.custom_opendrive = ""
            if self.prebuilt_map is not None:
                self.map_name = PREBUILT_MAP_DEFAULTS[self.prebuilt_map].map_name
            return

        if self.uses_custom_opendrive:
            self.prebuilt_map = None
            self.map_name = ""
            return

        if self.prebuilt_map is None:
            return

        if self.prebuilt_map not in CARLA_PREBUILT_MAPS:
            self.prebuilt_map = PrebuiltMap.CAMPUS
            self.apply_ui_constraints("map.prebuilt_map")

        self.map_name = PREBUILT_MAP_DEFAULTS[self.prebuilt_map].map_name

    @classmethod
    def infer_prebuilt_map(cls, map_name: str) -> Optional[PrebuiltMap]:
        town = map_name.lower().strip()
        if not town:
            return None

        for prebuilt_map in PrebuiltMap:
            if town == prebuilt_map.value.lower():
                return prebuilt_map

        if "aldenhoven" in town:
            return PrebuiltMap.ALDENHOVEN
        if "campus" in town:
            return PrebuiltMap.CAMPUS
        if "town01" in town:
            return PrebuiltMap.TOWN01
        if "town04" in town:
            return PrebuiltMap.TOWN04
        if "town05" in town:
            return PrebuiltMap.TOWN05
        if "town10hd" in town:
            return PrebuiltMap.TOWN10
        return None


class ScenarioConfig(BaseModel):
    scenario_file: str = ""

    def normalize(self):
        self.scenario_file = self.scenario_file.strip()


class AdditionalOptionsConfig(BaseModel):
    ENV_EXCLUDE: ClassVar[set[str]] = {
        "rmw",
        "simulation",
        "example_overlay",
        "traffic_profile",
        "testing_profile",
        "perception_profile",
        "planning_profile",
    }

    use_sim_time: bool = True
    ros_tracing: bool = False
    rmw: Rmw = Rmw.ZENOH
    simulation: SimulationProfile = SimulationProfile.CARLA
    example_overlay: str = ""
    traffic_profile: TrafficProfile = TrafficProfile.ENABLED
    testing_profile: TestingProfile = TestingProfile.MANUAL
    perception_profile: PerceptionProfile = PerceptionProfile.DISABLED
    planning_profile: PlanningProfile = PlanningProfile.CLASSIC

    def normalize(self):
        self.use_sim_time = True
        self.rmw = Rmw.ZENOH
        self.example_overlay = self.example_overlay.strip()

    def _example_prefix(self) -> str:
        example = self.example_overlay.strip()
        if not example:
            return ""
        if example.startswith("examples/"):
            return example
        return f"examples/{example}"

    @env_export("COMPOSE_PROFILES")
    def export_compose_profiles(self) -> str:
        parts = [self.simulation.value]
        if self.simulation == SimulationProfile.CARLA:
            parts.extend(
                [
                    self.traffic_profile.value,
                    self.testing_profile.value,
                ]
            )
        parts.extend([self.perception_profile.value, self.planning_profile.value])
        return ",".join(parts)

    @env_export("COMPOSE_FILE")
    def export_compose_file(self) -> str:
        example_prefix = self._example_prefix()
        if not example_prefix:
            return ""
        return f"docker-compose.yml:{example_prefix}/docker-compose.override.yml"


class SimulationConfig(BaseModel):
    vehicle: VehicleConfig = Field(default_factory=VehicleConfig)
    map: MapConfig = Field(default_factory=MapConfig)
    scenario: ScenarioConfig = Field(default_factory=ScenarioConfig)
    additional: AdditionalOptionsConfig = Field(default_factory=AdditionalOptionsConfig)

    ENV_LAYOUT: ClassVar[list[tuple[str, list[str]]]] = [
        (
            "### Runtime Profiles",
            ["COMPOSE_PROFILES", "COMPOSE_FILE"],
        ),
        ("### Vehicle Configuration", ["VEHICLE", "SENSORS"]),
        (
            "### Map Configuration",
            [
                "MAP",
                "CUSTOM_OPENDRIVE",
                "CUSTOM_LANELET",
                "SPAWN_POINT",
                "ORIGIN_LAT",
                "ORIGIN_LON",
                "LANELET_RELOAD",
            ],
        ),
        ("### Scenario Configuration", ["SCENARIO_FILE"]),
        ("### Additional Options", ["USE_SIM_TIME", "ROS_TRACING"]),
    ]
    OPTIONAL_EMPTY_ENV_KEYS: ClassVar[set[str]] = {"COMPOSE_FILE"}

    def model_post_init(self, __context: Any):
        self.apply_defaults()

    def apply_defaults(self):
        self.additional.normalize()
        if self.additional.simulation == SimulationProfile.SUMO:
            self.vehicle.vehicle = Vehicle.KARL
            self.vehicle.global_sensors_enabled = False
            self.vehicle.vehicle_sensor_file = ""
            self.vehicle.custom_sensor_file = ""
            self.scenario.scenario_file = ""
            self.additional.perception_profile = PerceptionProfile.DISABLED
            self.additional.planning_profile = PlanningProfile.CLASSIC
        else:
            self.vehicle.normalize()
            if self.additional.perception_profile == PerceptionProfile.ENABLED:
                self.vehicle.ensure_perception_sensor_file()
        self.map.normalize()
        self.map.align_to_simulation(self.additional.simulation)
        self.scenario.normalize()

    def apply_ui_constraints(self, trigger_path: str):
        if trigger_path == "vehicle.vehicle":
            self.vehicle.align_to_vehicle_capabilities()

        if trigger_path in {"map.prebuilt_map", "map.custom_opendrive"}:
            self.map.apply_ui_constraints(trigger_path)

        bundle_fields = {
            "map.custom_opendrive": (self.map, "custom_opendrive"),
            "map.custom_lanelet": (self.map, "custom_lanelet"),
            "scenario.scenario_file": (self.scenario, "scenario_file"),
        }
        if trigger_path not in bundle_fields:
            return
        selected_model, selected_attribute = bundle_fields[trigger_path]
        selected_value = str(getattr(selected_model, selected_attribute)).strip()
        if not selected_value:
            return
        selected_bundle = import_bundle_directory(selected_value)
        for path, (model, attribute) in bundle_fields.items():
            if path == trigger_path:
                continue
            value = str(getattr(model, attribute)).strip()
            if not value:
                continue
            value_bundle = import_bundle_directory(value)
            if (
                selected_bundle is not None
                and value_bundle != selected_bundle
            ) or (
                selected_bundle is None
                and value_bundle is not None
            ):
                setattr(model, attribute, "")

    def normalized_copy(self) -> "SimulationConfig":
        config = SimulationConfig(**self.model_dump())
        config.apply_defaults()
        return config

    def get_value(self, path: str) -> Any:
        obj: Any = self
        for part in path.split("."):
            obj = getattr(obj, part)
        return obj

    def _coerce_value(self, path: str, value: Any) -> Any:
        current_value = self.get_value(path)

        if path == "map.prebuilt_map":
            if value is None:
                return None
            if isinstance(value, str) and not value.strip():
                return None
            if isinstance(value, PrebuiltMap):
                return value
            return PrebuiltMap(value)

        if path in {"map.origin_lat", "map.origin_lon"}:
            if value is None:
                return None
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    return None
                return float(stripped)
            return float(value)

        if isinstance(current_value, Enum):
            enum_cls = type(current_value)
            if isinstance(value, enum_cls):
                return value
            return enum_cls(value)

        if isinstance(current_value, bool):
            return bool(value)

        if isinstance(current_value, float):
            return float(value)

        if isinstance(current_value, list):
            if not isinstance(value, list):
                return [str(value)]
            return [str(entry) for entry in value]

        return value

    def set_value(
        self,
        path: str,
        value: Any,
        *,
        repo_root: Optional[Path] = None,
    ):
        parts = path.split(".")
        target: Any = self
        for part in parts[:-1]:
            target = getattr(target, part)

        coerced = self._coerce_value(path, value)
        setattr(target, parts[-1], coerced)

        # Selecting a prebuilt map must disable custom OpenDRIVE mode.
        if path == "map.prebuilt_map" and self.map.prebuilt_map is not None:
            self.map.custom_opendrive = ""
            self.map.custom_lanelet = ""

        self.apply_defaults()
        self.apply_ui_constraints(path)

        bundle_fields = {
            "map.custom_opendrive": (self.map, "custom_opendrive", ".xodr"),
            "map.custom_lanelet": (self.map, "custom_lanelet", ".osm"),
            "scenario.scenario_file": (self.scenario, "scenario_file", ".xosc"),
        }
        selected_bundle = (
            import_bundle_directory(str(self.get_value(path)))
            if path in bundle_fields
            else None
        )
        if repo_root is not None and selected_bundle is not None:
            bundle_directory = repo_root / selected_bundle
            for model, attribute, suffix in bundle_fields.values():
                matches = sorted(bundle_directory.glob(f"*{suffix}"))
                selected_file = (
                    matches[0].relative_to(repo_root).as_posix()
                    if len(matches) == 1
                    else ""
                )
                setattr(model, attribute, selected_file)
            self.apply_defaults()

        if (
            repo_root is not None
            and path in {"map.prebuilt_map", "map.custom_opendrive"}
            and self.scenario.scenario_file.strip()
        ):
            scenario_map = self.map.custom_opendrive.strip() or self.map.export_map()
            matching_scenarios = filter_xosc_files_by_map(
                repo_root,
                [self.scenario.scenario_file.strip()],
                scenario_map,
            )
            if not matching_scenarios:
                self.scenario.scenario_file = ""

    @staticmethod
    def _parse_bool(value: str, default: bool = False) -> bool:
        if value is None:
            return default
        normalized = value.strip().lower()
        if not normalized:
            return default
        return normalized in {"1", "true", "yes", "on"}

    @staticmethod
    def parse_env_file(filepath: Path | str) -> dict[str, str]:
        filepath = Path(filepath)
        env_vars: dict[str, str] = {}
        if not filepath.exists():
            return env_vars

        with open(filepath, "r", encoding="utf-8") as file:
            for line in file:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                env_vars[key.strip()] = value.strip()

        return env_vars

    @staticmethod
    def _parse_optional_float(value: Optional[str]) -> Optional[float]:
        if value is None:
            return None

        stripped = value.strip()
        if not stripped:
            return None

        return float(stripped)

    @staticmethod
    def _infer_example_overlay_from_compose(
        compose_file_raw: str,
    ) -> str:
        def _split_parts(parts_raw: str) -> list[str]:
            if "," in parts_raw:
                return [part.strip() for part in parts_raw.split(",") if part.strip()]
            if ":" in parts_raw:
                return [part.strip() for part in parts_raw.split(":") if part.strip()]
            return [parts_raw.strip()] if parts_raw.strip() else []

        def _extract(parts_raw: str, suffix: str) -> str:
            parts = _split_parts(parts_raw)
            for part in parts:
                normalized = part.replace("\\", "/")
                if not normalized.startswith("examples/"):
                    continue
                if not normalized.endswith(suffix):
                    continue
                prefix = "examples/"
                name = normalized[len(prefix) : -len(suffix)].strip("/")
                if name:
                    return name
            return ""

        return _extract(compose_file_raw, "/docker-compose.override.yml")

    @classmethod
    def from_env_dict(cls, env_vars: dict[str, str]) -> "SimulationConfig":
        vehicle = Vehicle(env_vars.get("VEHICLE", Vehicle.KARL.value))

        sensors_env = env_vars.get(
            "SENSORS",
            f"{GLOBAL_SENSOR_FILE},{VehicleConfig.default_vehicle_sensor_file(Vehicle.KARL)}",
        )
        sensor_entries = [
            entry
            for entry in (item.strip() for item in sensors_env.split(","))
            if entry
        ]
        global_sensors_enabled = GLOBAL_SENSOR_FILE in sensor_entries
        vehicle_sensor_entries = [entry for entry in sensor_entries if is_vehicle_sensor_entry(entry)]
        non_vehicle_sensor_entries = [
            entry for entry in sensor_entries
            if entry not in vehicle_sensor_entries and entry != GLOBAL_SENSOR_FILE
        ]
        vehicle_sensor_file = (
            vehicle_sensor_entries[0]
            if vehicle_sensor_entries
            else VehicleConfig.default_vehicle_sensor_file(vehicle)
        )
        custom_sensor_file = non_vehicle_sensor_entries[0] if non_vehicle_sensor_entries else ""

        vehicle_config = VehicleConfig(
            vehicle=vehicle,
            global_sensors_enabled=global_sensors_enabled,
            vehicle_sensor_file=vehicle_sensor_file,
            custom_sensor_file=custom_sensor_file,
        )

        custom_opendrive = env_vars.get("CUSTOM_OPENDRIVE", "")
        map_name = env_vars.get("MAP", "")
        prebuilt_map = MapConfig.infer_prebuilt_map(map_name) if map_name.strip() else None

        spawn_point_raw = env_vars.get("SPAWN_POINT")
        default_spawn_point = (
            ""
            if prebuilt_map is None
            else PREBUILT_MAP_DEFAULTS[prebuilt_map].default_spawn_point
        )
        spawn_point = (
            spawn_point_raw
            if spawn_point_raw is not None
            else default_spawn_point
        )

        map_config = MapConfig(
            prebuilt_map=prebuilt_map,
            map_name=map_name,
            custom_opendrive=custom_opendrive,
            custom_lanelet=env_vars.get("CUSTOM_LANELET", ""),
            spawn_point=spawn_point,
            origin_lat=cls._parse_optional_float(env_vars.get("ORIGIN_LAT")),
            origin_lon=cls._parse_optional_float(env_vars.get("ORIGIN_LON")),
        )

        scenario = ScenarioConfig(scenario_file=env_vars.get("SCENARIO_FILE", ""))

        compose_profiles = [
            profile.strip()
            for profile in env_vars.get("COMPOSE_PROFILES", "").split(",")
            if profile.strip()
        ]

        simulation_profile = next(
            (
                profile
                for profile in compose_profiles
                if profile in {item.value for item in SimulationProfile}
            ),
            "",
        )
        if simulation_profile:
            simulation = SimulationProfile(simulation_profile)
        else:
            simulation = SimulationProfile.CARLA

        traffic_profile = TrafficProfile.ENABLED
        testing_profile = TestingProfile.MANUAL
        perception_profile = PerceptionProfile.DISABLED
        planning_profile = PlanningProfile.CLASSIC

        traffic_values = {item.value for item in TrafficProfile}
        testing_values = {item.value for item in TestingProfile}

        selected_traffic_profile = next(
            (profile for profile in compose_profiles if profile in traffic_values),
            "",
        )
        if selected_traffic_profile:
            traffic_profile = TrafficProfile(selected_traffic_profile)
        elif "traffic-testing" in compose_profiles:
            traffic_profile = TrafficProfile.ENABLED
        elif any(
            profile in compose_profiles
            for profile in ("no-testing", "manual-testing", "automated-testing")
        ):
            traffic_profile = TrafficProfile.DISABLED

        for profile in compose_profiles:
            if profile in testing_values:
                testing_profile = TestingProfile(profile)
                continue
            if profile in {item.value for item in PerceptionProfile}:
                perception_profile = PerceptionProfile(profile)
                continue
            if profile in {item.value for item in PlanningProfile}:
                planning_profile = PlanningProfile(profile)
                continue

        additional = AdditionalOptionsConfig(
            use_sim_time=cls._parse_bool(
                env_vars.get("USE_SIM_TIME", "true"),
                default=True,
            ),
            ros_tracing=cls._parse_bool(
                env_vars.get("ROS_TRACING", "false"),
                default=False,
            ),
            simulation=simulation,
            example_overlay=cls._infer_example_overlay_from_compose(
                env_vars.get("COMPOSE_FILE", env_vars.get("COMPOSE_FILES", "")),
            ),
            traffic_profile=traffic_profile,
            testing_profile=testing_profile,
            perception_profile=perception_profile,
            planning_profile=planning_profile,
        )

        return cls(
            vehicle=vehicle_config,
            map=map_config,
            scenario=scenario,
            additional=additional,
        )

    @classmethod
    def from_env_file(cls, filepath: Path | str) -> Optional["SimulationConfig"]:
        env_vars = cls.parse_env_file(filepath)
        if not env_vars:
            return None
        return cls.from_env_dict(env_vars)

    @staticmethod
    def _to_env_string(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, Enum):
            return str(value.value)
        return str(value)

    @staticmethod
    def _collect_env_values(
        model: BaseModel,
        excluded_env_names: Optional[set[str]] = None,
    ) -> dict[str, str]:
        env_values: dict[str, str] = {}
        excluded = getattr(model.__class__, "ENV_EXCLUDE", set())

        for field_name in model.__class__.model_fields:
            value = getattr(model, field_name)
            if isinstance(value, BaseModel):
                nested = SimulationConfig._collect_env_values(value, excluded_env_names)
                for env_name, env_value in nested.items():
                    if env_name in env_values:
                        raise ValueError(f"Duplicate env name detected: {env_name}")
                    env_values[env_name] = env_value
                continue

            if field_name in excluded:
                continue

            env_name = field_name.upper()
            if excluded_env_names and env_name in excluded_env_names:
                continue
            if env_name in env_values:
                raise ValueError(f"Duplicate env name detected: {env_name}")
            env_values[env_name] = SimulationConfig._to_env_string(value)

        for _, member in model.__class__.__dict__.items():
            env_name = getattr(member, "__env_name__", None)
            if not env_name:
                continue
            if excluded_env_names and env_name in excluded_env_names:
                continue
            if env_name in env_values:
                raise ValueError(f"Duplicate env name detected: {env_name}")
            env_values[env_name] = SimulationConfig._to_env_string(getattr(model, member.__name__)())

        return env_values

    def to_env_dict(self) -> dict[str, str]:
        return self._collect_env_values(self)

    def to_env_lines(self) -> list[str]:
        env_dict = self.to_env_dict()
        lines: list[str] = []

        for index, (header, keys) in enumerate(self.ENV_LAYOUT):
            section_lines: list[str] = []
            for key in keys:
                if key not in env_dict:
                    continue
                if key in self.OPTIONAL_EMPTY_ENV_KEYS and not env_dict.get(key, "").strip():
                    continue
                section_lines.append(f"{key}={env_dict[key]}\n")

            if not section_lines:
                continue

            lines.append(f"{header}\n")
            lines.extend(section_lines)

            if index < len(self.ENV_LAYOUT) - 1:
                lines.append("\n")

        return lines

    def write_env_file(self, filepath: Path):
        with open(filepath, "w", encoding="utf-8") as file:
            file.writelines(self.to_env_lines())

    def get_ui_sections(self, repo_root: Path) -> list[UiSection]:
        current_scenario = self.scenario.scenario_file.strip()
        current_lanelet = self.map.custom_lanelet.strip()
        current_opendrive = self.map.custom_opendrive.strip()
        active_import_bundle = first_import_bundle(
            current_opendrive,
            current_lanelet,
            current_scenario,
        )
        scenario_map = self.map.custom_opendrive.strip() or self.map.export_map()
        scenario_options = discover_files_with_suffix(repo_root, ".xosc")
        lanelet_options = discover_files_with_suffix(repo_root, ".osm")
        opendrive_options = discover_files_with_suffix(repo_root, ".xodr")
        if active_import_bundle:
            scenario_options = filter_files_by_import_bundle(
                scenario_options,
                active_import_bundle,
            )
            lanelet_options = filter_files_by_import_bundle(
                lanelet_options,
                active_import_bundle,
            )
            opendrive_options = filter_files_by_import_bundle(
                opendrive_options,
                active_import_bundle,
            )
            if import_bundle_directory(current_opendrive) == active_import_bundle:
                scenario_options = filter_xosc_files_by_map(
                    repo_root,
                    scenario_options,
                    current_opendrive,
                )
        else:
            scenario_options = filter_xosc_files_by_map(
                repo_root,
                scenario_options,
                scenario_map,
            )
        sumo_selected = self.additional.simulation == SimulationProfile.SUMO
        scenario_enabled = (
            not sumo_selected
            and self.additional.testing_profile != TestingProfile.DISABLED
        )
        if (
            current_scenario
            and current_scenario not in scenario_options
            and not scenario_map.strip()
            and not active_import_bundle
        ):
            scenario_options = [current_scenario] + scenario_options

        if (
            current_lanelet
            and current_lanelet not in lanelet_options
            and (
                not active_import_bundle
                or import_bundle_directory(current_lanelet) == active_import_bundle
            )
        ):
            lanelet_options = [current_lanelet] + lanelet_options

        if (
            current_opendrive
            and current_opendrive not in opendrive_options
            and (
                not active_import_bundle
                or import_bundle_directory(current_opendrive) == active_import_bundle
            )
        ):
            opendrive_options = [current_opendrive] + opendrive_options

        lanelet_select_options = [""] + lanelet_options
        opendrive_select_options = [""] + opendrive_options
        map_select_options = (
            [prebuilt_map.value for prebuilt_map in SUMO_PREBUILT_MAPS]
            if sumo_selected
            else [""] + [prebuilt_map.value for prebuilt_map in CARLA_PREBUILT_MAPS]
        )
        map_section_description = (
            "SUMO map selection."
            if sumo_selected
            else "Definition of a prebuilt or custom map."
        )
        bundle_lock_description = (
            f" Import bundle locked to {active_import_bundle}; scenario and map "
            "dropdowns only show files from this directory."
            if active_import_bundle
            else ""
        )
        map_section_description += bundle_lock_description
        map_source_description = (
            "Choose a SUMO map. Available maps: campus."
            "CUSTOM_LANELET can still be used optionally."
            if sumo_selected
            else (
                "Choose a prebuilt map or 'Custom OpenDRIVE'. Switching to prebuilt clears "
                "CUSTOM_OPENDRIVE and CUSTOM_LANELET."
            )
        )
        scenario_control = (
            "select_or_text"
            if scenario_options or active_import_bundle
            else "text"
        )
        vehicle_options = (
            (Vehicle.KARL.value,)
            if sumo_selected
            else tuple(vehicle.value for vehicle in CARLA_VEHICLES)
        )
        vehicle_sensor_options = (
            ("",)
            if sumo_selected
            else tuple(self.vehicle.available_vehicle_sensor_files())
        )
        scenario_select_options = [""] if sumo_selected else [""] + scenario_options

        vehicle_section = UiSection(
            title="Vehicle",
            description="Vehicle and sensor setup.",
            fields=(
                UiField(
                    path="vehicle.vehicle",
                    env_name="VEHICLE",
                    label="Vehicle Type",
                    description="Select the research vehicle.",
                    control="select",
                    options=vehicle_options,
                ),
                UiField(
                    path="vehicle.global_sensors_enabled",
                    env_name="SENSORS",
                    label="Enable Global Sensors",
                    description="Adds sensors for global objects or traffic lights.",
                    control="bool",
                    disabled=sumo_selected,
                ),
                UiField(
                    path="vehicle.vehicle_sensor_file",
                    env_name="SENSORS",
                    label="Vehicle Sensor File",
                    description="Select a predefined vehicle specific sensor file.",
                    control="select",
                    options=vehicle_sensor_options,
                    disabled=sumo_selected,
                ),
                UiField(
                    path="vehicle.custom_sensor_file",
                    env_name="SENSORS",
                    label="Custom Sensor File",
                    description="Optional additional sensor files.",
                    control="text",
                    disabled=sumo_selected,
                ),
            ),
        )

        map_section = UiSection(
            title="Map",
            description=map_section_description,
            fields=(
                UiField(
                    path="map.prebuilt_map",
                    env_name="MAP",
                    label="Map",
                    description=map_source_description,
                    control="select",
                    options=tuple(map_select_options),
                    none_as_empty=not sumo_selected,
                    empty_option_label="" if sumo_selected else "Custom OpenDRIVE",
                ),
                UiField(
                    path="map.custom_opendrive",
                    env_name="CUSTOM_OPENDRIVE",
                    label="Custom OpenDRIVE File",
                    description=(
                        "Path to .xodr file. Requires map source 'Custom OpenDRIVE'; "
                        "CUSTOM_LANELET and matching scenarios must use the same directory."
                    ),
                    control="select_or_text",
                    options=tuple(opendrive_select_options),
                    disabled=sumo_selected or self.map.prebuilt_map is not None,
                    strict_options=bool(active_import_bundle),
                ),
                UiField(
                    path="map.custom_lanelet",
                    env_name="CUSTOM_LANELET",
                    label="Custom Lanelet2 File",
                    description=(
                        "Optional for prebuilt maps; required for 'Custom OpenDRIVE' "
                        "and must share its scenario directory."
                    ),
                    control="select_or_text",
                    options=tuple(lanelet_select_options),
                    strict_options=bool(active_import_bundle),
                ),
                UiField(
                    path="map.origin_lat",
                    env_name="ORIGIN_LAT",
                    label="Origin Latitude",
                    description="Optional. Leave empty to keep ORIGIN_LAT unset.",
                    control="text",
                    disabled=not self.map.origin_is_editable,
                ),
                UiField(
                    path="map.origin_lon",
                    env_name="ORIGIN_LON",
                    label="Origin Longitude",
                    description="Optional. Leave empty to keep ORIGIN_LON unset.",
                    control="text",
                    disabled=not self.map.origin_is_editable,
                ),
                UiField(
                    path="map.effective_lanelet_reload",
                    env_name="LANELET_RELOAD",
                    label="Lanelet2 Reload (derived)",
                    description="Disabled when custom Lanelet2 map is used.",
                    control="bool",
                    disabled=True,
                ),
            ),
        )

        scenario_section = UiSection(
            title="Scenario",
            description=(
                "Scenario file used for manual or automated testing."
                + bundle_lock_description
            ),
            fields=(
                UiField(
                    path="map.spawn_point",
                    env_name="SPAWN_POINT",
                    label="Spawn Point",
                    description=(
                        "x,y,z,yaw or x,y,z,roll,pitch,yaw OR lat,lon,alt,yaw,wgs84 or "
                        "lat,lon,alt,roll,pitch,yaw,wgs84. "
                        "Prebuilt map defaults are applied automatically when changing the map."
                    ),
                    control="text",
                ),
                UiField(
                    path="scenario.scenario_file",
                    env_name="SCENARIO_FILE",
                    label="Scenario File",
                    description="Auto-discovered from all .xosc files in repository. Enabled for manual or automated scenario execution only.",
                    control=scenario_control,
                    options=tuple(scenario_select_options),
                    empty_option_label="No scenario selected",
                    disabled=not scenario_enabled,
                    strict_options=bool(active_import_bundle),
                ),
            ),
        )

        profiles_section = UiSection(
            title="Profiles",
            description="",
            fields=(
                UiField(
                    path="additional.simulation",
                    env_name="COMPOSE_PROFILES",
                    label="Simulation",
                    description="Selected simulation core.",
                    control="select",
                    options=(SimulationProfile.CARLA.value, SimulationProfile.SUMO.value),
                ),
                UiField(
                    path="additional.traffic_profile",
                    env_name="COMPOSE_PROFILES",
                    label="Traffic Profile",
                    description="Enable or disable generated background traffic. CARLA only.",
                    control="select",
                    options=tuple(profile.value for profile in TrafficProfile),
                    disabled=self.additional.simulation != SimulationProfile.CARLA,
                ),
                UiField(
                    path="additional.testing_profile",
                    env_name="COMPOSE_PROFILES",
                    label="Testing Profile",
                    description="Disable scenario execution or select manual or automated testing. CARLA only.",
                    control="select",
                    options=tuple(profile.value for profile in TestingProfile),
                    disabled=self.additional.simulation != SimulationProfile.CARLA,
                ),
                UiField(
                    path="additional.perception_profile",
                    env_name="COMPOSE_PROFILES",
                    label="Perception Profile",
                    description="Selected compose perception profile. Fixed to no-perception for SUMO.",
                    control="select",
                    options=tuple(profile.value for profile in PerceptionProfile),
                    disabled=self.additional.simulation != SimulationProfile.CARLA,
                ),
                UiField(
                    path="additional.planning_profile",
                    env_name="COMPOSE_PROFILES",
                    label="Planning Profile",
                    description="Selected compose planning profile.",
                    control="select",
                    options=tuple(profile.value for profile in PlanningProfile),
                    disabled=self.additional.simulation != SimulationProfile.CARLA,
                ),
                *(
                    (
                        UiField(
                            path="additional.example_overlay",
                            env_name="COMPOSE_FILE",
                            label="Example Overlay",
                            description="Set by presets.",
                            control="text",
                            disabled=True,
                        ),
                    )
                    if self.additional.example_overlay.strip()
                    else ()
                ),
            ),
        )

        additional_section = UiSection(
            title="Additional Options",
            description="Runtime options.",
            fields=(
                UiField(
                    path="additional.use_sim_time",
                    env_name="USE_SIM_TIME",
                    label="Use Simulation Time",
                    description="Fixed to true for this setup.",
                    control="bool",
                    disabled=True,
                ),
                UiField(
                    path="additional.ros_tracing",
                    env_name="ROS_TRACING",
                    label="Enable ROS Tracing",
                    description="Enable tracing for performance analysis.",
                    control="bool",
                ),
            ),
        )

        return [profiles_section, vehicle_section, map_section, scenario_section, additional_section]
