# OpenADSim Scenario Checker

This tool checks OpenSCENARIO files against OpenADS-specific requirements for
use in OpenADSim with CARLA and ScenarioRunner. It is not a general-purpose
OpenSCENARIO validator and does not establish full compliance with the
OpenSCENARIO standard.

The `validate`, `validate-folder`, and `validate-runtime` commands only check
files; they do not modify them. The `import` command creates a new bundle under
`--output-root` with a modified, normalized scenario copy, leaving the source
files unchanged.

Check an existing OpenSCENARIO file against these requirements without modifying it:

```bash
python3 utils/scenario-checker/scenario_checker.py validate scenario.xosc
```

Custom maps must always be supplied explicitly with `--opendrive` and
`--lanelet`, even when the files are next to the scenario and `LogicFile`
references the `.xodr`. No map files are discovered automatically.

```bash
python3 utils/scenario-checker/scenario_checker.py validate scenario.xosc \
  --opendrive map.xodr --lanelet map.osm
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

Create a self-contained scenario bundle with a normalized scenario copy:

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

During `import`, the output scenario copy is normalized from OpenSCENARIO 1.x
to the ScenarioRunner-supported 1.1 format. The following changes apply only to
that copy, not to the source file or any validation command.
Normalization removes 1.2 `VariableDeclarations` and converts the changed Event
priority and Cartesian-distance values. Actor-level `ObjectController` elements
and all existing ego `ControllerAction` elements are replaced by exactly one ROS
controller action; unused catalog locations are cleared. Other external
`CatalogReference` elements are rejected because catalog files are not part of
the self-contained three-file bundle. Unsupported major versions are rejected.
