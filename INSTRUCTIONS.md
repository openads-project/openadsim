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

A timeless `FollowTrajectoryAction` is valid OpenSCENARIO 1.1 and would be preferred. It must use `TrajectoryRef`. The current ScenarioRunner parser still requires a `time` attribute on every `Vertex`. Until this is fixed, use a timeless `AssignRouteAction` with `Waypoint routeStrategy="shortest"`. Artificial timestamps would conflict with the scenario's `SpeedAction`s.

## Controllers

- Ego: `ros_vehicle_control_route_action.py`. (added automatically by importer)
- Cyclist option 1: omit `ControllerAction` to use the default `NpcVehicleControl`
  (CARLA physics enabled; route points are projected onto the road).
- Cyclist option 2: assign `simple_vehicle_control`
  (more deterministic path and speed tracking; reduced vehicle physics).

## Known limitation

`SpeedActionDynamics` is not supported by the current ScenarioRunner integration. Target speeds are applied immediately instead of following the requested dynamics.
