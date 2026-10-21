Scenarios for the CARLA-SUMO co-simulation (`carla-sumo` profile). Each folder contains one scenario:

- `objects.json`: the map-specific CARLA objects.
- `scenario.sumocfg` with `net/` and `rou/` (optional): the scenario's SUMO traffic.

A scenario is selected by its folder name with `COSIM_SCENARIO`. Its objects are spawned in addition to `SENSORS`, e.g.:

```
SENSORS=global-sensors.json,karl-minimal.json
COSIM_SCENARIO=town10-incident
```

The blueprints used by the objects are part of the shared library in `carla-simulation/carla_spawn_objects/blueprints/`.
