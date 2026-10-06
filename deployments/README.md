# Helmfile deployment

This directory contains the OpenADSim-specific inputs and manually maintained additions for the generic [Helmfile generator](https://github.com/openads-project/openads-helm/tree/main/utils/helmfile-generator).

## Layout

- `helmfile.yaml` is the entrypoint. It loads the manual add-ons before the generated services.
- `addons/` contains the middleware bridge NetworkPolicy and is never overwritten by the generator.
- `chart-map.yaml` contains OpenADSim-specific chart references and conversion rules.
- `generated/` is replaceable generator output. It is ignored by Git and must not be committed.

## CI

The [`Helmfile` workflow](../.github/workflows/helmfile.yml) calls the reusable workflow from `openads-helm`. It generates `deployments/generated`, validates the root Helmfile with `helmfile build` and `helmfile template`, and packages the complete `deployments` directory.

Successful runs provide a seven-day Actions artifact. Builds from the default branch and semantic-version tags are additionally published as an OCI artifact.

## Local generation and validation

Use a sibling checkout of `openads-helm`, or point `OPENADS_HELM_DIR` at another checkout:

```sh
export OPENADS_HELM_DIR=../openads-helm

python3 "${OPENADS_HELM_DIR}/utils/helmfile-generator/helmfile_generator.py" \
  docker-compose.yml \
  --env-file .env \
  --chart-map deployments/chart-map.yaml \
  --output-dir deployments/generated
```

Load the runtime environment before validating or deploying. `OPENADSIM_PATH` is required by services that use Kubernetes `hostPath` volumes. For deployment, it must point to the absolute checkout path on the selected Kubernetes node.

```sh
set -a
. ./.env
set +a
export OPENADSIM_PATH="$(pwd)"

helmfile --file deployments/helmfile.yaml build
helmfile --file deployments/helmfile.yaml template --concurrency 1
```

`DEPLOYMENT_PREFIX` is optional for a single stack. Set it when multiple stacks share a namespace:

```sh
export DEPLOYMENT_PREFIX=sim1
helmfile --file deployments/helmfile.yaml --namespace dev sync
```

## Temporary workaround: test a local OpenADService chart

`prepare_local_openadservice.py` rebuilds downloaded parent charts against a local `openadservice` base chart. This is a temporary development workaround and requires `deployments/generated` to exist first.

With the standard sibling repository layout, run:

```sh
python3 deployments/prepare_local_openadservice.py
```

For another checkout, pass `--base-chart /absolute/path/to/openads-helm/charts/openadservice`. The script prints a temporary root Helmfile below `deployments/generated/.runtime/`; use that path for `helmfile template`, `sync`, or `destroy`. Regenerate the overlay after changing the generated Helmfile, a parent chart reference, or the local base chart.
