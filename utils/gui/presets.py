"""Predefined preset configurations for common scenarios."""

from models import (
    AdditionalOptionsConfig,
    MapConfig,
    PrebuiltMap,
    PerceptionProfile,
    ScenarioConfig,
    SimulationConfig,
    Vehicle,
    VehicleConfig,
    TrafficProfile,
    TestingProfile,
)

DEFAULT_PRESET_NAME = "prototyping (default)"

PRESET_NAMES = {
    DEFAULT_PRESET_NAME,
    "scenario-execution",
    "cooperative-perception",
}


PRESETS = {
    DEFAULT_PRESET_NAME: SimulationConfig(),
    "scenario-execution": SimulationConfig(
        vehicle=VehicleConfig(
            vehicle=Vehicle.KARL,
            global_sensors_enabled=True,
            vehicle_sensor_file="karl-basic.json",
        ),
        map=MapConfig(
            prebuilt_map=None,
            custom_opendrive="carla-simulation/scenarios/simple-scenario/synthetic_curve_cut_in.xodr",
            custom_lanelet="carla-simulation/scenarios/simple-scenario/synthetic_curve_cut_in.osm",
            spawn_point="100.0,100.0,-100.0,0.0",
        ),
        scenario=ScenarioConfig(scenario_file="carla-simulation/scenarios/simple-scenario/synthetic_curve_cut_in.xosc"),
        additional=AdditionalOptionsConfig(
            traffic_profile=TrafficProfile.DISABLED,
            testing_profile=TestingProfile.AUTOMATED,
        ),
    ),
    "cooperative-perception": SimulationConfig(
        vehicle=VehicleConfig(
            vehicle=Vehicle.KARL,
            global_sensors_enabled=True,
            vehicle_sensor_file="karl-basic.json",
            custom_sensor_file="rita-minimal.json",
        ),
        map=MapConfig(
            prebuilt_map=PrebuiltMap.CAMPUS,
            spawn_point="50.779551,6.050371,256.0,-8.0,wgs84",
        ),
        additional=AdditionalOptionsConfig(
            example_overlay="cooperative-perception",
            traffic_profile=TrafficProfile.ENABLED,
            testing_profile=TestingProfile.DISABLED,
            perception_profile=PerceptionProfile.ENABLED,
        ),
    ),
}


def get_preset_names() -> list[str]:
    return list(PRESETS.keys())


def get_preset(name: str) -> SimulationConfig:
    return PRESETS[name].normalized_copy()


def is_preset_name(name: str) -> bool:
    return name in PRESET_NAMES


def match_preset_name(config: SimulationConfig) -> str:
    normalized = config.normalized_copy().model_dump()
    for name, preset_config in PRESETS.items():
        if normalized == preset_config.normalized_copy().model_dump():
            return name
    return ""
