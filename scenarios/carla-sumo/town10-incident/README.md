Traffic incident on Town10: an incident car and an ambulance behind construction cones and accident warning signs, observed by three mRISUs (`mrisu1`, `mrisu2`, `mrisu3`).

```
COMPOSE_PROFILES=carla-sumo,no-environment,no-testing,no-perception,planning
COMPOSE_FILE=docker-compose.yml:carla-sumo-simulation/docker-compose.override.yml
SENSORS=global-sensors.json,karl-minimal.json
COSIM_SCENARIO=town10-incident
TRAFFIC=none
MAP=Town10HD_Opt
SPAWN_POINT=-113.5,20.0,0.2,-90.7
SUMO_CFG=
CARLA_START_TIME=-1
```
