# Integrating an OpenADService

This guide explains how to add a custom OpenADService to OpenADSim. It covers how to connect the module through existing [OpenADS interfaces](https://openads-project.github.io/openadsuite/tools.html#interfaces-and-data-models) and run it as an additional Docker Compose service alongside the simulation and [OpenADStack](https://openads-project.github.io/openadstack/openadstack.html).

For developing and packaging the module itself, follow [OpenADSuite Development](https://openads-project.github.io/openadsuite/openadsuite.html). The steps below show how to verify the required interfaces and artifacts and connect the module to OpenADSim.

## 1. Check the Interfaces

Before adding a new service, define which data it subscribes to and which data it publishes. Prefer existing [OpenADS interface packages](https://openads-project.github.io/openadsuite/tools.html#interfaces-and-data-models) such as [`perception_interfaces`](https://github.com/ika-rwth-aachen/perception_interfaces) and [`planning_interfaces`](https://github.com/ika-rwth-aachen/planning_interfaces).

If you are unsure, inspect the available ROS topics in a running simulation:

```bash
docker compose exec monitoring.rviz-carla bash
ros2 topic list
ros2 topic echo /localization/ego_state_estimation/ego_data
```

## 2. Check Available Package Artifacts

When adding a new OpenADService, two generated artifacts are relevant for the OpenADSim integration:

- a Docker image in a package/container registry
- optionally, a Docker Compose file published as an OCI artifact

The [OpenADSuite Development](https://openads-project.github.io/openadsuite/openadsuite.html) workflow can publish both artifacts directly to the GitHub package registry of your module repository. The command below uses `openads_demo_service` as a concrete example of this workflow. For an actual integration, replace the demo artifacts in the following sections with those of your component.

```bash
docker compose -f oci://ghcr.io/openads-project/openads_demo_service/compose:v2.0.0 up -d
```

For simulation-based development, the next step is to include the module as a service in the OpenADSim Compose setup so it can provide or consume data from the running simulation and stack.

## 3. Add the Service to OpenADSim

Create a new subfolder for your use case or integration and add a dedicated `docker-compose.yml`. Reuse the shared [Docker service templates](https://github.com/openads-project/openadstack/blob/main/utils/compose/README.md) from OpenADStack.

The demo module used here subscribes to [`EgoData`](https://github.com/ika-rwth-aachen/perception_interfaces/blob/main/perception_msgs/msg/EgoData.msg) messages and logs them, which makes it a minimal integration example.

If your module publishes an OCI artifact, include it and override only the OpenADSim-specific settings:

```yaml
include:
  # Published module Compose artifact, for example from the module release pipeline.
  - oci://ghcr.io/openads-project/openads_demo_service/compose:v2.0.0

services:
  openads-demo-service:
    profiles:
      - planning
    extends:
      file: ../../openadstack/openadstack/utils/compose/docker-compose.template.yml
      service: ros2-service
    environment:
      INPUT_TOPIC: /localization/ego_state_estimation/ego_data
```

Set a profile if the service should only run in specific configurations, for example `planning` or `perception`. Omit the `profiles` key only if the service should always start with the base stack.

Include the new Compose file from a higher-level Compose file, or select it through `COMPOSE_FILE`, so that it becomes part of the active setup.

### Optional: Move the Service into OpenADStack

If the service proves useful, integrate it into OpenADStack so that other setups can reuse it. The OpenADSim Compose file can then extend the OpenADStack service and only override simulation-specific settings such as topics, parameters, profiles, or mounted config files.

```yaml
services:
  planning.my-service:
    profiles:
      - planning
    extends:
      file: ../../openadstack/my-namespace/my-service/docker-compose.yml
      service: my-namespace.my-service
    environment:
      EGO_DATA_TOPIC: /localization/ego_state_estimation/ego_data
```

See the [OpenADStack integration guide](https://openads-project.github.io/openadstack/service-integration.html) for details.

## 4. Start the Simulation

Restart the simulation to apply the changes:

```bash
docker compose up -d
```

Inspect the effective service list if the service does not start:

```bash
docker compose config --services
```

## 5. Iterate and Debug

During development, iterate on the module until it integrates cleanly with the simulation and the other stack components.

Useful checks:

```bash
docker compose logs -f openads-demo-service
docker compose exec openads-demo-service bash
ros2 node info /openads_demo_service
```

For source-level development, debugging, and release workflows, follow the [OpenADSuite Development](https://openads-project.github.io/openadsuite/openadsuite.html) guide.
