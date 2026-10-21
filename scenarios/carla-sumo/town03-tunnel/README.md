Tunnel on Town03, observed by three mRISUs (`mrisu1`, `mrisu2`, `mrisu3`) placed 15 m apart inside the tunnel.

```
COMPOSE_PROFILES=carla-sumo,no-traffic,no-testing,no-perception,planning
COMPOSE_FILE=docker-compose.yml:carla-sumo-simulation/docker-compose.override.yml
SENSORS=global-sensors.json,karl-minimal.json
COSIM_SCENARIO=town03-tunnel
MAP=Town03_Opt
SPAWN_POINT=34.0,4.5,0.2,-179.1
SUMO_CFG=/sumo-config/town03.sumocfg
```
