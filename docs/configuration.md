# Configuration

This overview is a compact reference for all available Docker Compose profiles and `.env` variables. The basic workflow for configuring and running simulations, along with concrete examples, is described in [Getting Started](./getting-started.md).

The recommended way to edit the configuration is the Configuration GUI. It exposes the same variables listed below, applies presets, validates the configuration, and writes valid changes back to `.env`:

```bash
xhost +local:
docker compose up -d gui
```

When X11 access is available, the Configuration GUI opens automatically. Otherwise, open [http://localhost:8501](http://localhost:8501) manually.

## Environment Variables

Environment variables are key-value settings that configure the Docker Compose setup. Some of them are also passed into the containers, but their main role in this repository is to select profiles, maps, vehicles, scenarios, overrides, and optional runtime behavior without editing the Compose files directly.

All variables below can be set through the Configuration GUI or manually in `.env`.

| Variable | Purpose / Impact | Typical Values |
| ------ | ------ | ------ |
| `COMPOSE_PROFILES` | Selected functional groups for simulation, traffic, testing, perception, and planning. | `carla,traffic,manual-testing,no-perception,planning`, `sumo,no-perception,planning` |
| `COMPOSE_FILE` | Compose file chain. Examples override the standard `docker-compose.yml` with a more specialized file. | `docker-compose.yml:examples/cooperative-perception/docker-compose.override.yml` |
| `VEHICLE` | Vehicle profile for sensor frames (URDF), parameters, and controller tuning. Mostly relevant for CARLA. | `karl`, `shuttle` |
| `SENSORS` | Comma-separated list of CARLA sensor layout files to be used when spawning objects. SUMO does not use sensor layouts. | `global-sensors.json,karl-minimal.json`,`rita-minimal.json` |
| `MAP` | Selected prebuilt map. Leave empty when using custom OpenDRIVE mode. | CARLA: `aldenhoven`, `campus`, `Town10HD_Opt`; SUMO: `campus` |
| `SPAWN_POINT` | Ego spawn point, given as `x,y,z,heading` in simulation map coordinates, or as `lat,lon,altitude,heading` when suffixed with `wgs84`. Required for prebuilt maps. | `50.787510,6.045938,260.0,90.0,wgs84`, `-1.0,-154.0,1.0,238.0` |
| `CUSTOM_OPENDRIVE` | Custom *OpenDRIVE* (`.xodr`) map for CARLA standalone map mode. Not supported for SUMO. | `custom.xodr` |
| `CUSTOM_LANELET` | Matching Lanelet2 `.osm` map for route planning and map server. Required with custom OpenDRIVE. | `custom.osm` |
| `ORIGIN_LAT` | GPS origin latitude for a custom lanelet map frame. Set together with `ORIGIN_LON` or leave both empty. | `50.782329` |
| `ORIGIN_LON` | GPS origin longitude for a custom lanelet map frame. Set together with `ORIGIN_LAT` or leave both empty. | `6.070377` |
| `LANELET_RELOAD` | `true` derives the Lanelet2 map from the simulation map; `false` uses `CUSTOM_LANELET`. Set automatically by the Configuration GUI. | `true`, `false` |
| `SCENARIO_FILE` | *OpenSCENARIO* file executed directly using the CARLA scenario runner within the `automated-testing` profile. The Configuration GUI filters available scenarios for the selected map. For sequential runs of multiple scenarios, use the [multi-scenario execution script](./example-scenario-execution.md#execute-multiple-scenarios). | `carla-simulation/scenarios/scenario-generator/campus_following.xosc` |
| `USE_SIM_TIME` | Enables ROS simulated time across the stack. Must stay `true` in this setup. | `true` |
| `ROS_TRACING` | Enables ROS 2 tracing for supported services. | `false`, `true` |

## Service Profiles

`COMPOSE_PROFILES` selects functional groups from the included Docker Compose files. Depending on the selected value, services are enabled or omitted. Pick one compatible value per group.

| Group | Profiles | Compatibility / Effect |
| ------ | ------ | ------ |
| Simulation | `carla`, `sumo` | Selects the simulation backend. CARLA supports the full stack; SUMO is lightweight and more restricted. |
| Traffic | `no-traffic`, `traffic` | Selects if background traffic in CARLA is enabled. SUMO always generates random traffic. |
| Testing | `no-testing`, `manual-testing`, `automated-testing` | CARLA only. `no-testing` starts no scenario runner, `manual-testing` controls scenarios manually through RViz, `automated-testing` starts the `SCENARIO_FILE` directly. |
| Perception | `no-perception`, `perception` | `perception` is CARLA only. SUMO must use `no-perception`. |
| Planning | `planning` | CARLA and SUMO support `planning`. |

CARLA uses one profile from both the Traffic and Testing groups. Their defaults are `traffic` and `manual-testing`. SUMO uses neither group.

Add `bag-recording` when ROS bag capture is required.

## Service Overview

The following overview lists the Docker Compose services involved in OpenADSim and shows which profiles activate them. Services marked *always* have no profile assigned and therefore start with every configuration.

<details>
<summary><strong>CARLA Simulation</strong></summary>

| Service | Profiles | Role |
| ------- | -------- | ---- |
| `carla-server` | `carla` | Offscreen CARLA backend with custom `DefaultEngine.ini` |
| `ros-middleware-bridge` | `carla` | DDS-to-Zenoh bridge for CARLA ROS topics |
| `carla-ros-bridge` | `carla` | ROS 2 bridge; loads `MAP` or `CUSTOM_OPENDRIVE` |
| `carla-spawn-objects` | `carla` | Spawns ego vehicle and sensors from `SENSORS`; optional `SPAWN_POINT` |
| `carla-converter` | `carla` | Generates stack-relevant ego, object, and map topics |
| `carla-simulation-adapter` | `carla` | Transforms CARLA data and coordinate frames for OpenADStack; optional Lanelet2 map selection via `LANELET_RELOAD` |
| `carla-ackermann-control` | `carla` | Applies Ackermann control commands to the ego vehicle |
| `carla-manual-control` | `carla` | Manual keyboard control of the vehicle |
| `carla-control-active-bridge` | `carla` | Reports whether stack control is active on `/control/active` by inverting the manual override state |
| `carla-environment` | `traffic` | Defines random traffic and environment variation |
| `carla-scenario-runner-ros-manual-testing` | `manual-testing` | Interactive scenario execution through RViz |
| `carla-scenario-runner-ros-automated-testing` | `automated-testing` | Direct scenario execution from `SCENARIO_FILE` |

</details>

<details>
<summary><strong>SUMO Simulation</strong></summary>

| Service | Profiles | Role |
| ------- | -------- | ---- |
| `sumo-interface` | `sumo` | Generates `EgoData`, `ObjectList`, and map information from SUMO |
| `sumo-simulation-adapter` | `sumo` | Transforms SUMO data and coordinate frames for OpenADStack; optional Lanelet2 map selection via `LANELET_RELOAD` |
| `sumo-control-active-bridge` | `sumo` | Reports whether stack control is active on `/control/active` by inverting the manual override state |

</details>

### OpenADStack

OpenADStack services, their roles, and interfaces are documented in the [OpenADStack functional architecture](https://github.com/openads-project/openadstack/blob/main/docs/functional-architecture.md).

<details>
<summary><strong>Monitoring and Tooling</strong></summary>

| Service | Profiles | Role |
| ------- | -------- | ---- |
| `monitoring.rviz-carla` | `carla` | RViz visualization with the CARLA display configuration |
| `monitoring.rviz-sumo` | `sumo` | RViz visualization with the SUMO display configuration |
| `monitoring.bag-recorder` | `bag-recording` | Optionally records ROS bags after the route topic becomes available |
| `gui` | `gui` | Web-based Configuration GUI on port 8501 |

</details>
