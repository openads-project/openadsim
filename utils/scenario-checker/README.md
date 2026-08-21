# OpenADSim Scenario Checker

Validate an existing OpenSCENARIO file without modifying it:

```bash
python3 utils/scenario-checker/scenario_checker.py validate scenario.xosc
```

Validate all scenarios for one active map, as used by manual scenario selection:

```bash
python3 utils/scenario-checker/scenario_checker.py validate-folder \
  carla-simulation/scenarios --expected-map campus
```

Runtime containers use one environment-aware call before starting ScenarioRunner:

```bash
python3 /scenario_checker.py validate-runtime /carla-simulation/scenarios/example.xosc
```

The command reads `MAP`, `CUSTOM_OPENDRIVE`, and `CUSTOM_LANELET` and resolves
repository-relative map paths below the mounted scenario root. Folder-based
manual testing uses `validate-runtime /carla-simulation/scenarios`. The checker
detects files and directories automatically. For custom maps, the configured
`.xodr`, `.osm`, and every matching `.xosc` must be in the same directory. The
`LogicFile` value must be the exact `.xodr` filename without a directory.

Import and normalize a self-contained scenario bundle:

```bash
python3 utils/scenario-checker/scenario_checker.py import scenario.xosc \
  --output-root carla-simulation/scenarios/custom-imports \
  --name my-scenario \
  --opendrive map.xodr \
  --lanelet map.osm
```

An uploaded OpenDRIVE file overrides `RoadNetwork/LogicFile`. Without an
OpenDRIVE upload, `LogicFile` must reference a supported prebuilt CARLA map.
Custom maps always require explicit OpenDRIVE and Lanelet2 uploads during
import; files merely adjacent to the source upload are not selected implicitly.

Every imported custom bundle stores the resulting `.xosc`, `.xodr`, and `.osm`
files together in its import directory. Prebuilt CARLA maps need only the
`.xosc` file.

Imports normalize OpenSCENARIO 1.x files to the ScenarioRunner-supported 1.1
format. This removes 1.2 `VariableDeclarations` and converts the changed Event
priority and Cartesian-distance values. Actor-level `ObjectController` elements
and all existing ego `ControllerAction` elements are replaced by exactly one ROS
controller action; unused catalog locations are cleared. Other external
`CatalogReference` elements are rejected because catalog files are not part of
the self-contained three-file bundle. Unsupported major versions are rejected.
