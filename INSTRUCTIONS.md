# Feedback on the RoadRunner OpenSCENARIO export

The following adaptations are required to run `roadrunner_original.xosc` in OpenADSim:

- Reference the installed map as `aldenhoven`.
- Identify the ego actor as `ego_vehicle` (normalized by the importer).
- Add initial `TeleportAction`s for both actors at the first trajectory vertex (same `x/y/z/h/p/r`; the importer raises `z` by 0.1 m itself).
- Move routing actions from `Init` into a **new, separate Story** that starts with the simulation (see below).
- Replace zero-distance position triggers with a practical tolerance, e.g. `distance <= 1.0 m`.
- Leave the existing `Story` (its Acts, triggers and `SpeedAction`s) unchanged.

## Routing Story

Use a separate `Story` placed before the existing one, starting at `SimulationTime > 0`. Do not add routes to existing Acts.

## Spatial trajectories

Convert each timeless `FollowTrajectoryAction` into an `AssignRouteAction`: one `<Waypoint routeStrategy="shortest">` per `Vertex`, same `WorldPosition`. ScenarioRunner supports `FollowTrajectoryAction` only with a `time` attribute on every `Vertex`, and timestamps would conflict with the scenario's `SpeedAction`s. That is why `AssignRouteAction` is used.

## Controllers

- Ego: do not add a `ControllerAction`. The importer assigns `ros_vehicle_control_route_action.py`.
- Cyclist: choose one of the following options:
  - Add no `ControllerAction`. The default `NpcVehicleControl` is used (CARLA physics; route points are projected onto the road).
  - Add a `ControllerAction` in `Init` with `<Property name="module" value="simple_vehicle_control"/>` (follows path and speed more precisely; reduced vehicle physics).

## Known limitation

`SpeedActionDynamics` is not supported by the current ScenarioRunner integration. Target speeds are applied immediately instead of following the requested dynamics.
