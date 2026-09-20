# Helmfile commands

Run these commands from the OpenADSim repository root.

Regenerate the supported Helmfile release universe. Host environment values
are evaluated when Helmfile runs and are not embedded during generation. GUI
and SUMO are not exported yet:

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

## Test a local OpenADService base chart

Regenerate the Helmfile and service values first if Compose or the converter
changed. Changes to the local base chart alone do not require this step:

```sh
python3 utils/compose-to-k8s/compose_to_helmfile.py \
  --output-dir deployments/helmfile \
  docker-compose.yml
```

Prepare temporary copies of all parent charts with the local OpenADService
dependency. The script downloads the published parent charts, uses the local
Zenoh parent chart, and writes an overlay under the ignored
`deployments/helmfile/.runtime` directory. It prints the overlay path:

```sh
python3 deployments/helmfile/prepare_local_openadservice.py \
  --base-chart /work/geller/openads-project/openads-helm/charts/openadservice
```

`--base-chart` is optional for this repository layout. Use `--helmfile` to
prepare a different source Helmfile. Profile selection does not limit which
parent charts the script downloads; it controls releases when Helmfile runs.

Load deployment settings and use the printed overlay path. With a prefix,
set `CARLA_HOST` explicitly to the prefixed service name: its default is
`carla-server`, while `dependsOn` waits for the prefixed local service.

```sh
set -a
. ./.env
set +a
export COMPOSE_PROFILES=carla,traffic
export DEPLOYMENT_PREFIX=sim1
export CARLA_HOST=sim1-carla-server
helmfile --file deployments/helmfile/.runtime/openadservice-1.2.0/helmfile.yaml template
helmfile --file deployments/helmfile/.runtime/openadservice-1.2.0/helmfile.yaml sync
```

Rerun preparation after changing the source Helmfile, the local base chart,
or a parent chart reference. Changes only to service values take effect on the
next `template` or `sync`. Preparation alone does not deploy anything.
