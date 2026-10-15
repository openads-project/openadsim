Scenarios for the CARLA-SUMO co-simulation (`carla-sumo` profile). Each folder contains the map-specific CARLA objects (`objects.json`) of one scenario. The SUMO traffic of a scenario is selected with `SUMO_CFG`.

A scenario is selected by its folder name with `COSIM_SCENARIO`. Its objects are spawned in addition to `SENSORS`, e.g.:

```
SENSORS=global-sensors.json,karl-minimal.json
COSIM_SCENARIO=town10-incident
```

The blueprints used by the objects are part of the shared library in `carla-simulation/carla_spawn_objects/blueprints/`.
