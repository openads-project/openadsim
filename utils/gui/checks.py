"""Domain dependency checks for simulation configuration."""

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Optional

from models import (
    GLOBAL_SENSOR_FILE,
    PerceptionProfile,
    PlanningProfile,
    PREBUILT_MAP_DEFAULTS,
    SUMO_PREBUILT_MAPS,
    SimulationProfile,
    TestingProfile,
    SimulationConfig,
    Vehicle,
    is_vehicle_sensor_entry,
)


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str
    level: str = "error"


@dataclass
class ValidationResult:
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


@dataclass
class EnvValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    config: Optional[SimulationConfig] = None
    raw_values: dict[str, str] = field(default_factory=dict)


_SCENARIO_CHECKER_MODULE = None


def _load_scenario_checker(repo_root: Path):
    global _SCENARIO_CHECKER_MODULE
    if _SCENARIO_CHECKER_MODULE is not None:
        return _SCENARIO_CHECKER_MODULE
    checker_path = repo_root / "utils/scenario-checker/scenario_checker.py"
    if not checker_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        "openadsim_scenario_checker",
        checker_path,
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _SCENARIO_CHECKER_MODULE = module
    return module


def _repo_path(repo_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def _is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def _validate_spawn_point(spawn_point: str) -> Optional[str]:
    parts = [part.strip() for part in spawn_point.split(",") if part.strip()]

    if len(parts) in {5, 7} and parts[-1].lower() == "wgs84":
        numeric_parts = parts[:-1]
        if all(_is_float(part) for part in numeric_parts):
            return None
        return "SPAWN_POINT in WGS84 format must use numeric values before 'wgs84'"

    if len(parts) in {4, 6}:
        if all(_is_float(part) for part in parts):
            return None
        return "SPAWN_POINT in simulation-map format must contain numeric values"

    return (
        "SPAWN_POINT format invalid. Use x,y,z,yaw, x,y,z,roll,pitch,yaw, "
        "lat,lon,alt,yaw,wgs84 or lat,lon,alt,roll,pitch,yaw,wgs84"
    )


def _vehicle_sensor_entries_from_env(sensors_env: str) -> list[str]:
    entries = [entry.strip() for entry in sensors_env.split(",") if entry.strip()]
    return [entry for entry in entries if is_vehicle_sensor_entry(entry)]


def validate_config(config: SimulationConfig, repo_root: Optional[Path] = None) -> ValidationResult:
    result = ValidationResult()
    sumo_selected = config.additional.simulation == SimulationProfile.SUMO
    sumo_map_values = ", ".join(prebuilt_map.value for prebuilt_map in SUMO_PREBUILT_MAPS)

    # Vehicle dependencies
    prefix = f"{config.vehicle.vehicle.value}-"
    vehicle_sensor_file = config.vehicle.vehicle_sensor_file.strip()
    if not sumo_selected and not vehicle_sensor_file:
        result.errors.append(
            ValidationIssue(
                path="vehicle.vehicle_sensor_file",
                message="A vehicle sensor config must be selected",
            )
        )
    elif not sumo_selected and not vehicle_sensor_file.startswith(prefix):
        result.errors.append(
            ValidationIssue(
                path="vehicle.vehicle_sensor_file",
                message=(
                    f"{config.vehicle.vehicle.value} requires sensor config with prefix '{prefix}'"
                ),
            )
        )

    if not sumo_selected and not config.vehicle.global_sensors_enabled:
        result.warnings.append(
            ValidationIssue(
                path="vehicle.global_sensors_enabled",
                message="global sensors are disabled (default is enabled)",
                level="warning",
            )
        )

    # Map dependencies
    expected_town = ""

    if sumo_selected and config.map.uses_custom_opendrive:
        result.errors.append(
            ValidationIssue(
                path="map.custom_opendrive",
                message="CUSTOM_OPENDRIVE is currently not supported for SUMO",
            )
        )

    if sumo_selected and config.map.prebuilt_map not in SUMO_PREBUILT_MAPS:
        result.errors.append(
            ValidationIssue(
                path="map.prebuilt_map",
                message=f"SUMO MAP must be one of: {sumo_map_values}",
            )
        )

    if config.map.uses_custom_opendrive:
        if config.map.prebuilt_map is not None:
            result.errors.append(
                ValidationIssue(
                    path="map.prebuilt_map",
                    message=(
                        "Set map source to 'Custom OpenDRIVE' (empty prebuilt map) "
                        "when CUSTOM_OPENDRIVE is set"
                    ),
                )
            )

        if not config.map.custom_lanelet.strip():
            result.errors.append(
                ValidationIssue(
                    path="map.custom_lanelet",
                    message="CUSTOM_LANELET is required when CUSTOM_OPENDRIVE is set",
                )
            )

        if repo_root is not None:
            custom_opendrive = _repo_path(
                repo_root,
                config.map.custom_opendrive.strip(),
            ).resolve()
            custom_lanelet_value = config.map.custom_lanelet.strip()
            custom_lanelet = (
                _repo_path(repo_root, custom_lanelet_value).resolve()
                if custom_lanelet_value
                else None
            )
            if custom_opendrive.suffix.lower() != ".xodr":
                result.errors.append(
                    ValidationIssue(
                        path="map.custom_opendrive",
                        message="CUSTOM_OPENDRIVE must end in .xodr",
                    )
                )
            if not custom_opendrive.is_file():
                result.errors.append(
                    ValidationIssue(
                        path="map.custom_opendrive",
                        message=f"CUSTOM_OPENDRIVE does not exist: {custom_opendrive}",
                    )
                )
            if custom_lanelet is not None and not custom_lanelet.is_file():
                result.errors.append(
                    ValidationIssue(
                        path="map.custom_lanelet",
                        message=f"CUSTOM_LANELET does not exist: {custom_lanelet}",
                    )
                )
            if custom_lanelet is not None and custom_lanelet.suffix.lower() != ".osm":
                result.errors.append(
                    ValidationIssue(
                        path="map.custom_lanelet",
                        message="CUSTOM_LANELET must end in .osm",
                    )
                )
            if (
                custom_lanelet is not None
                and custom_opendrive.parent != custom_lanelet.parent
            ):
                result.errors.append(
                    ValidationIssue(
                        path="map.custom_lanelet",
                        message=(
                            "CUSTOM_OPENDRIVE and CUSTOM_LANELET must be in the "
                            "same scenario directory"
                        ),
                    )
                )

        if config.map.prebuilt_map is None and config.map.map_name.strip():
            result.errors.append(
                ValidationIssue(
                    path="map.map_name",
                    message="MAP must be empty when CUSTOM_OPENDRIVE is set",
                )
            )
    else:
        if config.map.prebuilt_map is None:
            result.errors.append(
                ValidationIssue(
                    path="map.prebuilt_map",
                    message="Select a prebuilt map or provide CUSTOM_OPENDRIVE in Custom OpenDRIVE mode",
                )
            )
        else:
            expected_town = PREBUILT_MAP_DEFAULTS[config.map.prebuilt_map].map_name
        if config.map.prebuilt_map is not None and config.map.map_name.strip() != expected_town:
            result.warnings.append(
                ValidationIssue(
                    path="map.map_name",
                    message=(
                        f"Prebuilt map {config.map.prebuilt_map.value} expects MAP={expected_town}"
                    ),
                    level="warning",
                )
            )

    origin_lat_set = config.map.origin_lat is not None
    origin_lon_set = config.map.origin_lon is not None
    if origin_lat_set != origin_lon_set:
        result.warnings.append(
            ValidationIssue(
                path="map.origin_lat",
                message="Set both ORIGIN_LAT and ORIGIN_LON together, or leave both empty",
                level="warning",
            )
        )

    spawn_point = config.map.spawn_point.strip()
    if not spawn_point:
        if not config.map.uses_custom_opendrive:
            result.errors.append(
                ValidationIssue(
                    path="map.spawn_point",
                    message="SPAWN_POINT must be set for prebuilt maps",
                )
            )
    else:
        spawn_error = _validate_spawn_point(spawn_point)
        if spawn_error:
            result.errors.append(
                ValidationIssue(
                    path="map.spawn_point",
                    message=spawn_error,
                )
            )

    # Scenario checks
    scenario_required = (
        not sumo_selected
        and config.additional.testing_profile == TestingProfile.AUTOMATED
    )

    if scenario_required and not config.scenario.scenario_file.strip():
        result.errors.append(
            ValidationIssue(
                path="scenario.scenario_file",
                message="SCENARIO_FILE must be set for automated testing",
            )
        )

    if config.scenario.scenario_file.strip() and not config.scenario.scenario_file.endswith(".xosc"):
        result.warnings.append(
            ValidationIssue(
                path="scenario.scenario_file",
                message="SCENARIO_FILE should point to an .xosc file",
                level="warning",
            )
        )

    if config.scenario.scenario_file.strip() and repo_root is not None:
        scenario_file = _repo_path(repo_root, config.scenario.scenario_file.strip())
        if not scenario_file.is_file():
            result.errors.append(
                ValidationIssue(
                    path="scenario.scenario_file",
                    message=f"SCENARIO_FILE does not exist: {scenario_file}",
                )
            )
        else:
            checker = _load_scenario_checker(repo_root)
            if checker is not None:
                custom_opendrive = config.map.custom_opendrive.strip()
                custom_lanelet = config.map.custom_lanelet.strip()
                scenario_report = checker.validate_scenario(
                    scenario_file,
                    repo_root=repo_root,
                    opendrive=(
                        _repo_path(repo_root, custom_opendrive)
                        if custom_opendrive
                        else None
                    ),
                    lanelet=(
                        _repo_path(repo_root, custom_lanelet)
                        if custom_opendrive and custom_lanelet
                        else None
                    ),
                    expected_map=("" if custom_opendrive else config.map.export_map()),
                    expected_opendrive=(
                        _repo_path(repo_root, custom_opendrive)
                        if custom_opendrive
                        else None
                    ),
                    expected_lanelet=(
                        _repo_path(repo_root, custom_lanelet)
                        if custom_opendrive and custom_lanelet
                        else None
                    ),
                )
                for message in scenario_report.errors:
                    result.errors.append(
                        ValidationIssue(
                            path="scenario.scenario_file",
                            message=message,
                        )
                    )
                for message in scenario_report.warnings:
                    result.warnings.append(
                        ValidationIssue(
                            path="scenario.scenario_file",
                            message=message,
                            level="warning",
                        )
                    )

    # Additional options
    if sumo_selected and config.additional.perception_profile != PerceptionProfile.DISABLED:
        result.errors.append(
            ValidationIssue(
                path="additional.perception_profile",
                message="Perception profile must be no-perception for SUMO",
            )
        )

    if sumo_selected and config.additional.planning_profile != PlanningProfile.CLASSIC:
        result.errors.append(
            ValidationIssue(
                path="additional.planning_profile",
                message="Planning profile must be planning for SUMO",
            )
        )

    if not config.additional.use_sim_time:
        result.errors.append(
            ValidationIssue(
                path="additional.use_sim_time",
                message="USE_SIM_TIME must stay true in this setup",
            )
        )

    return result


def validate_env_file(filepath: Path, repo_root: Optional[Path] = None) -> EnvValidationResult:
    env_vars = SimulationConfig.parse_env_file(filepath)
    if not env_vars:
        return EnvValidationResult(
            is_valid=False,
            errors=["No ENV variables found in .env file"],
            raw_values=env_vars,
        )

    try:
        config = SimulationConfig.from_env_dict(env_vars)
    except Exception as exc:
        return EnvValidationResult(
            is_valid=False,
            errors=[str(exc)],
            raw_values=env_vars,
        )

    raw_vehicle_sensors = _vehicle_sensor_entries_from_env(env_vars.get("SENSORS", ""))
    extra_raw_sensor_error = len(raw_vehicle_sensors) > 1

    result = validate_config(config, repo_root=repo_root)
    errors = [f"{issue.path}: {issue.message}" for issue in result.errors]

    if extra_raw_sensor_error:
        errors.append("vehicle.vehicle_sensor_file: Only one vehicle sensor config is allowed")

    return EnvValidationResult(
        is_valid=not errors,
        errors=errors,
        warnings=[f"{issue.path}: {issue.message}" for issue in result.warnings],
        config=config,
        raw_values=env_vars,
    )
