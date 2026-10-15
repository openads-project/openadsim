Road works zone on Town04: barriers, beacons, warning signs, construction material and a wheel loader.

```
COMPOSE_PROFILES=carla-sumo,no-traffic,no-testing,no-perception,planning
COMPOSE_FILE=docker-compose.yml:carla-sumo-simulation/docker-compose.override.yml
SENSORS=global-sensors.json,karl-minimal.json
COSIM_SCENARIO=town04-roadworkzone
MAP=Town04_Opt
SPAWN_POINT=62.0,190.0,2.5,90.0
SUMO_CFG=/sumo-config/town04_rwz.sumocfg
```
