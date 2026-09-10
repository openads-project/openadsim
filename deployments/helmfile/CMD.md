# Helmfile commands

Run these commands from the OpenADSim repository root.

Regenerate the supported Helmfile release universe. `COMPOSE_PROFILES` from
`.env` determines which releases are enabled by default. GUI and SUMO are not
exported yet:

```sh
./utils/compose-to-k8s/compose_to_helmfile.py \
  --output-dir deployments/helmfile \
  docker-compose.yml
```

Export only the profiles currently active through `COMPOSE_PROFILES`:

```sh
./utils/compose-to-k8s/compose_to_helmfile.py \
  --active-profiles-only \
  --output-dir deployments/helmfile \
  docker-compose.yml
```

Generate the same complete release universe, but enable only `perception` by
default:

```sh
./utils/compose-to-k8s/compose_to_helmfile.py \
  --profile perception \
  --output-dir deployments/helmfile \
  docker-compose.yml
```

Deploy or update the complete Helmfile:

```sh
helmfile --file deployments/helmfile/helmfile.yaml sync
```

Helmfile does not read `.env` itself. Load it into the current shell before
deployment when its `COMPOSE_PROFILES` value should control the enabled
releases:

```sh
set -a
. ./.env
set +a
helmfile --file deployments/helmfile/helmfile.yaml sync
```

Alternatively, export only the profile selection for one deployment:

```sh
export COMPOSE_PROFILES=carla,traffic,manual-testing,no-perception,planning
helmfile --file deployments/helmfile/helmfile.yaml sync
```

Deploy or update only the RViz component:

```sh
export COMPOSE_PROFILES=carla
helmfile --file deployments/helmfile/helmfile.yaml \
  --selector name=zenoh-router \
  --selector name=rviz-carla \
  sync
```
