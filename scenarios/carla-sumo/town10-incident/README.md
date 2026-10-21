Traffic incident on Town10: an incident car and an ambulance behind construction cones and accident warning signs, observed by three mRISUs (`mrisu1`, `mrisu2`, `mrisu3`).

```
COMPOSE_PROFILES=carla-sumo,no-traffic,no-testing,no-perception,planning
COMPOSE_FILE=docker-compose.yml:carla-sumo-simulation/docker-compose.override.yml
SENSORS=global-sensors.json,karl-minimal.json
COSIM_SCENARIO=town10-incident
MAP=Town10HD_Opt
SPAWN_POINT=-11.4,-127.2,0.2,180.0
SUMO_CFG=
```
