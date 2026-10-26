"""Predefined preset configurations for common scenarios."""

from models import (
    COSIM_SCENARIO_MAPS,
    AdditionalOptionsConfig,
    CosimConfig,
    MapConfig,
    PrebuiltMap,
    PerceptionProfile,
    ScenarioConfig,
    SimulationConfig,
    SimulationProfile,
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
    "carla-sumo-town10-incident",
    "carla-sumo-town04-roadworkzone",
    "carla-sumo-town03-tunnel",
}


def _cosim_preset(scenario: str, spawn_point: str, sumo_cfg: str = "") -> SimulationConfig:
    """Co-simulation preset with the settings from scenarios/carla-sumo/<scenario>/README.md."""
    prebuilt_map = COSIM_SCENARIO_MAPS[scenario]
    return SimulationConfig(
        vehicle=VehicleConfig(
            vehicle=Vehicle.KARL,
            global_sensors_enabled=True,
            vehicle_sensor_file="karl-minimal.json",
        ),
        map=MapConfig(prebuilt_map=prebuilt_map, spawn_point=spawn_point),
        cosim=CosimConfig(cosim_scenario=scenario, sumo_cfg=sumo_cfg),
        additional=AdditionalOptionsConfig(simulation=SimulationProfile.CARLA_SUMO),
    )


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
            custom_opendrive="scenarios/simple-scenario/synthetic_curve_cut_in.xodr",
            custom_lanelet="scenarios/simple-scenario/synthetic_curve_cut_in.osm",
            spawn_point="100.0,100.0,-100.0,0.0",
        ),
        scenario=ScenarioConfig(scenario_file="scenarios/simple-scenario/synthetic_curve_cut_in.xosc"),
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
    "carla-sumo-town10-incident": _cosim_preset("town10-incident", "-11.4,-127.2,0.2,180.0"),
    "carla-sumo-town04-roadworkzone": _cosim_preset("town04-roadworkzone", "62.0,190.0,2.5,90.0"),
    "carla-sumo-town03-tunnel": _cosim_preset("town03-tunnel", "34.0,4.5,0.2,-179.1"),
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
