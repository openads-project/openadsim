#!/usr/bin/env python3

"""UE5 environment setup for CARLA traffic and optional weather."""

import argparse
import logging
import math
import signal
import time

import carla
from numpy import random


FOLLOWING_DISTANCE = 2.5
HYBRID_RADIUS = 70.0
FIXED_DELTA_SECONDS = 0.05
DEFAULT_NUMBER_OF_VEHICLES = 30
DEFAULT_NUMBER_OF_WALKERS = 10
WALKER_SPAWN_Z_OFFSET = 2.0
PEDESTRIANS_RUNNING = 0.0
PEDESTRIANS_CROSSING = 0.0
BACKGROUND_VEHICLE_SPAWN_CLEARANCE_M = 20.0
EGO_VEHICLE_SPAWN_CLEARANCE_M = 75.0
NEAR_EGO_SPAWN_RADIUS_M = 200.0
NEAR_EGO_VEHICLE_TARGET_FRACTION = 0.7
WALKER_NAV_LOCATION_ATTEMPTS = 10
CONNECT_RETRY_SECONDS = 2.0
CLIENT_TIMEOUT_SECONDS = 10.0
EGO_ROLE_NAME = "ego_vehicle"
BACKGROUND_ROLE_NAME = "autopilot"

STATUS_INTERVAL = 5.0
STUCK_SPEED_MPS = 0.2
STUCK_DISTANCE_M = 1.0
STUCK_SECONDS = 45.0


def install_signal_handlers():
    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="carla-server", help="CARLA server host")
    parser.add_argument("-p", "--port", default=2000, type=int, help="CARLA server port")
    parser.add_argument("--tm-port", default=8000, type=int, help="Traffic Manager port")
    parser.add_argument(
        "-n",
        "--number-of-vehicles",
        default=DEFAULT_NUMBER_OF_VEHICLES,
        type=int,
    )
    parser.add_argument(
        "-w",
        "--number-of-walkers",
        default=DEFAULT_NUMBER_OF_WALKERS,
        type=int,
    )
    parser.add_argument("--filterv", default="vehicle.*", help="Vehicle blueprint filter")
    parser.add_argument("--filterw", default="walker.pedestrian.*", help="Walker blueprint filter")
    parser.add_argument("--synch", action="store_true", help="Enable synchronous mode")
    parser.add_argument("--hybrid", action="store_true", help="Enable TM hybrid physics")
    parser.add_argument("--respawn", action="store_true", help="Respawn dormant vehicles")
    parser.add_argument(
        "--wait-for-ego",
        dest="wait_for_ego",
        action="store_true",
        default=True,
        help="Wait for an actor with role_name=ego_vehicle before spawning traffic",
    )
    parser.add_argument(
        "--no-wait-for-ego",
        dest="wait_for_ego",
        action="store_false",
        help="Spawn traffic immediately without waiting for ego_vehicle",
    )
    parser.add_argument(
        "--weather",
        metavar="PRESET",
        help='Weather preset name, e.g. "ClearNoon" or "WetCloudyNoon"',
    )
    return parser.parse_args()


def connect_to_carla(args):
    client = carla.Client(args.host, args.port)
    client.set_timeout(CLIENT_TIMEOUT_SECONDS)

    while True:
        try:
            client.get_server_version()
            logging.info("Connected to CARLA at %s:%s", args.host, args.port)
            return client
        except RuntimeError as error:
            logging.warning(
                "Waiting for CARLA server at %s:%s: %s",
                args.host,
                args.port,
                error,
            )
            time.sleep(CONNECT_RETRY_SECONDS)


def wait_for_ego_vehicle(client):
    """Return the current world after its ego vehicle appears."""
    while True:
        try:
            # The ROS bridge may replace the world while loading the configured map.
            world = client.get_world()
            ego = find_ego_vehicle(world)
            if ego is not None:
                logging.info("Found ego vehicle with actor id %s", ego.id)
                return world
        except RuntimeError as error:
            logging.warning("Waiting for a stable CARLA world: %s", error)
        else:
            logging.info("Waiting for ego vehicle with role_name=%s", EGO_ROLE_NAME)

        # Avoid wait_for_tick() on a world that may belong to the old episode.
        time.sleep(CONNECT_RETRY_SECONDS)


def find_ego_vehicle(world):
    for actor in world.get_actors().filter("vehicle.*"):
        if is_ego_vehicle(actor):
            return actor
    return None


def is_ego_vehicle(actor):
    return (
        actor.attributes.get("role_name") == EGO_ROLE_NAME
        or actor.attributes.get("id") == EGO_ROLE_NAME
    )


def get_blueprints(world, pattern):
    blueprints = world.get_blueprint_library().filter(pattern)
    if not blueprints:
        raise ValueError("No blueprints found for filter: %s" % pattern)
    return sorted(blueprints, key=lambda blueprint: blueprint.id)


def apply_weather(world, preset_name):
    if not preset_name:
        return None

    if not hasattr(carla.WeatherParameters, preset_name):
        presets = [
            name
            for name in dir(carla.WeatherParameters)
            if not name.startswith("_") and name[0].isupper()
        ]
        raise ValueError(
            "Unknown weather preset '%s'. Available presets: %s"
            % (preset_name, ", ".join(sorted(presets)))
        )

    previous_weather = world.get_weather()
    world.set_weather(getattr(carla.WeatherParameters, preset_name))
    logging.info("Weather preset set to %s", preset_name)
    return previous_weather


def configure_simulation(world, traffic_manager, args):
    traffic_manager.set_global_distance_to_leading_vehicle(FOLLOWING_DISTANCE)

    if args.respawn:
        traffic_manager.set_respawn_dormant_vehicles(True)

    if args.hybrid:
        traffic_manager.set_hybrid_physics_mode(True)
        traffic_manager.set_hybrid_physics_radius(HYBRID_RADIUS)

    original_settings = world.get_settings()
    changed_settings = False
    synchronous_master = False

    if args.synch:
        traffic_manager.set_synchronous_mode(True)
        settings = world.get_settings()
        if not settings.synchronous_mode:
            synchronous_master = True
            settings.synchronous_mode = True
            settings.fixed_delta_seconds = FIXED_DELTA_SECONDS
            world.apply_settings(settings)
            changed_settings = True
    else:
        logging.warning("Running in asynchronous mode; traffic can be less stable")

    return original_settings, changed_settings, synchronous_master


def restore_simulation(world, original_settings, changed_settings, previous_weather):
    if previous_weather is not None:
        world.set_weather(previous_weather)

    if not changed_settings:
        return

    settings = world.get_settings()
    settings.synchronous_mode = original_settings.synchronous_mode
    settings.fixed_delta_seconds = original_settings.fixed_delta_seconds
    world.apply_settings(settings)


def spawn_vehicles(client, world, traffic_manager, blueprints, count, synchronous_master):
    if count <= 0:
        return []

    spawn_points = select_vehicle_spawn_points(world, count)
    if count > len(spawn_points):
        logging.warning(
            "Requested %d vehicles, but only found %d free spawn points",
            count,
            len(spawn_points),
        )
        count = len(spawn_points)

    batch = []
    for transform in spawn_points[:count]:
        blueprint = random.choice(blueprints)
        set_random_attribute(blueprint, "color")
        set_random_attribute(blueprint, "driver_id")
        blueprint.set_attribute("role_name", BACKGROUND_ROLE_NAME)

        batch.append(
            carla.command.SpawnActor(blueprint, transform).then(
                carla.command.SetAutopilot(
                    carla.command.FutureActor, True, traffic_manager.get_port()
                )
            )
        )

    return apply_spawn_batch(client, batch, synchronous_master)


def select_vehicle_spawn_points(world, count):
    spawn_points = list(world.get_map().get_spawn_points())
    random.shuffle(spawn_points)

    vehicles = list(world.get_actors().filter("vehicle.*"))
    ego = next((actor for actor in vehicles if is_ego_vehicle(actor)), None)
    ego_location = ego.get_location() if ego is not None else None
    occupied_locations = [
        actor.get_location() for actor in vehicles if actor is not ego
    ]
    background_vehicles = [
        actor
        for actor in vehicles
        if actor is not ego
        and actor.attributes.get("role_name") == BACKGROUND_ROLE_NAME
    ]
    selected_spawn_points = []

    if ego_location is None:
        add_clear_vehicle_spawn_points(
            spawn_points,
            count,
            occupied_locations,
            selected_spawn_points,
        )
        return selected_spawn_points

    eligible_spawn_points = [
        spawn_point
        for spawn_point in spawn_points
        if spawn_point.location.distance(ego_location)
        >= EGO_VEHICLE_SPAWN_CLEARANCE_M
    ]
    near_spawn_points = [
        spawn_point
        for spawn_point in eligible_spawn_points
        if spawn_point.location.distance(ego_location) <= NEAR_EGO_SPAWN_RADIUS_M
    ]
    distant_spawn_points = [
        spawn_point
        for spawn_point in eligible_spawn_points
        if spawn_point.location.distance(ego_location) > NEAR_EGO_SPAWN_RADIUS_M
    ]

    current_nearby = sum(
        actor.get_location().distance(ego_location) <= NEAR_EGO_SPAWN_RADIUS_M
        for actor in background_vehicles
    )
    desired_nearby = round(
        (len(background_vehicles) + count) * NEAR_EGO_VEHICLE_TARGET_FRACTION
    )
    near_target = min(
        count,
        max(0, desired_nearby - current_nearby),
    )
    add_clear_vehicle_spawn_points(
        near_spawn_points,
        near_target,
        occupied_locations,
        selected_spawn_points,
    )
    add_clear_vehicle_spawn_points(
        distant_spawn_points,
        count,
        occupied_locations,
        selected_spawn_points,
    )

    if len(selected_spawn_points) < count:
        add_clear_vehicle_spawn_points(
            near_spawn_points,
            count,
            occupied_locations,
            selected_spawn_points,
        )

    selected_nearby = sum(
        spawn_point.location.distance(ego_location) <= NEAR_EGO_SPAWN_RADIUS_M
        for spawn_point in selected_spawn_points
    )
    logging.info(
        "Selected %d/%d vehicle spawn points within %.0f-%.0f m of the ego "
        "(current=%d target=%d)",
        selected_nearby,
        len(selected_spawn_points),
        EGO_VEHICLE_SPAWN_CLEARANCE_M,
        NEAR_EGO_SPAWN_RADIUS_M,
        current_nearby,
        desired_nearby,
    )
    return selected_spawn_points


def add_clear_vehicle_spawn_points(
    candidates,
    target_count,
    occupied_locations,
    selected_spawn_points,
):
    selected_locations = [
        spawn_point.location for spawn_point in selected_spawn_points
    ]

    for spawn_point in candidates:
        if len(selected_spawn_points) >= target_count:
            break

        if not is_clear_location(
            spawn_point.location,
            occupied_locations + selected_locations,
            BACKGROUND_VEHICLE_SPAWN_CLEARANCE_M,
        ):
            continue
        selected_spawn_points.append(spawn_point)
        selected_locations.append(spawn_point.location)


def is_clear_location(location, occupied_locations, clearance_m):
    return all(
        location.distance(occupied_location) >= clearance_m
        for occupied_location in occupied_locations
    )


def set_random_attribute(blueprint, name):
    if blueprint.has_attribute(name):
        values = blueprint.get_attribute(name).recommended_values
        blueprint.set_attribute(name, random.choice(values))


def apply_spawn_batch(client, batch, do_tick=True):
    actor_ids = []
    for response in client.apply_batch_sync(batch, do_tick):
        if response.error:
            logging.error(response.error)
        else:
            actor_ids.append(response.actor_id)
    return actor_ids


def spawn_walkers(client, world, blueprints, count, args, synchronous_master):
    if count <= 0:
        return []

    world.set_pedestrians_cross_factor(PEDESTRIANS_CROSSING)

    spawn_points = find_walker_spawn_points(world, count)
    if len(spawn_points) < count:
        logging.warning(
            "Only found %d/%d walker navigation spawn locations. "
            "If this stays at 0, the current map likely has no pedestrian navmesh.",
            len(spawn_points),
            count,
        )

    batch = []
    walker_speeds = []
    for spawn_point in spawn_points:
        blueprint = random.choice(blueprints)

        if blueprint.has_attribute("is_invincible"):
            blueprint.set_attribute("is_invincible", "false")

        walker_speeds.append(get_walker_speed(blueprint))
        batch.append(carla.command.SpawnActor(blueprint, spawn_point))

    spawned_walkers = []
    for index, response in enumerate(client.apply_batch_sync(batch, True)):
        if response.error:
            logging.error(response.error)
        else:
            spawned_walkers.append(
                {"walker": response.actor_id, "speed": walker_speeds[index]}
            )

    records = spawn_walker_controllers(client, world, spawned_walkers)
    start_walker_controllers(world, records, args, synchronous_master)
    return records


def find_walker_spawn_points(world, count):
    spawn_points = []
    attempts = max(count * WALKER_NAV_LOCATION_ATTEMPTS, count)

    for _ in range(attempts):
        if len(spawn_points) >= count:
            break

        location = world.get_random_location_from_navigation()
        if location is not None:
            location.z += WALKER_SPAWN_Z_OFFSET
            spawn_points.append(carla.Transform(location))

    return spawn_points


def get_walker_speed(blueprint):
    if not blueprint.has_attribute("speed"):
        return 0.0

    speeds = blueprint.get_attribute("speed").recommended_values
    if random.random() > PEDESTRIANS_RUNNING:
        return float(speeds[1])
    return float(speeds[2])


def spawn_walker_controllers(client, world, spawned_walkers):
    controller_blueprint = world.get_blueprint_library().find("controller.ai.walker")
    batch = [
        carla.command.SpawnActor(
            controller_blueprint, carla.Transform(), record["walker"]
        )
        for record in spawned_walkers
    ]

    records = []
    uncontrolled_walkers = []
    for index, response in enumerate(client.apply_batch_sync(batch, True)):
        walker = spawned_walkers[index]
        if response.error:
            logging.error(response.error)
            uncontrolled_walkers.append(walker["walker"])
        else:
            records.append(
                {
                    "walker": walker["walker"],
                    "controller": response.actor_id,
                    "speed": walker["speed"],
                }
            )

    destroy_actor_ids(client, uncontrolled_walkers)
    return records


def start_walker_controllers(world, walker_records, args, synchronous_master):
    if not walker_records:
        return

    if args.synch and synchronous_master:
        world.tick()
    else:
        world.wait_for_tick()

    speed_by_controller = {
        record["controller"]: record["speed"] for record in walker_records
    }
    controllers = world.get_actors(list(speed_by_controller.keys()))

    for controller in controllers:
        controller.start()
        target = world.get_random_location_from_navigation()
        if target is not None:
            controller.go_to_location(target)
        controller.set_max_speed(speed_by_controller[controller.id])


def supervise_environment(
    client,
    world,
    traffic_manager,
    args,
    vehicle_blueprints,
    walker_blueprints,
    vehicles,
    vehicle_sensors,
    collided_vehicles,
    walker_records,
    synchronous_master,
):
    vehicle_state = build_vehicle_state(world, vehicles, time.monotonic())
    next_status = time.monotonic()

    while True:
        tick_once(world, args, synchronous_master)

        now = time.monotonic()
        if now < next_status:
            continue
        next_status = now + STATUS_INTERVAL

        previous_vehicles = set(vehicles)
        vehicles[:] = live_actor_ids(world, vehicles)
        missing_vehicle_ids = previous_vehicles - set(vehicles)
        remove_vehicle_sensors(client, vehicle_sensors, missing_vehicle_ids)

        walker_records[:] = live_walker_records(client, world, walker_records)

        stuck_vehicles = find_stuck_vehicles(world, vehicles, vehicle_state, now)
        collided_vehicle_ids = [
            actor_id for actor_id in vehicles if actor_id in collided_vehicles
        ]
        collided_vehicles.difference_update(collided_vehicle_ids)

        bad_vehicles = set(stuck_vehicles) | set(collided_vehicle_ids)
        if bad_vehicles:
            remove_vehicle_sensors(client, vehicle_sensors, bad_vehicles)
            destroy_actor_ids(client, bad_vehicles)
            vehicles[:] = [actor_id for actor_id in vehicles if actor_id not in bad_vehicles]
            for actor_id in bad_vehicles:
                vehicle_state.pop(actor_id, None)

        missing_vehicles = max(0, args.number_of_vehicles - len(vehicles))
        new_vehicles = spawn_vehicles(
            client,
            world,
            traffic_manager,
            vehicle_blueprints,
            missing_vehicles,
            synchronous_master,
        )
        vehicles.extend(new_vehicles)
        vehicle_state.update(build_vehicle_state(world, new_vehicles, now))
        attach_collision_sensors(
            world, new_vehicles, vehicle_sensors, collided_vehicles
        )

        missing_walkers = max(0, args.number_of_walkers - len(walker_records))
        new_walkers = spawn_walkers(
            client,
            world,
            walker_blueprints,
            missing_walkers,
            args,
            synchronous_master,
        )
        walker_records.extend(new_walkers)

        print(
            (
                "environment: vehicles=%d/%d walkers=%d/%d "
                "respawned_vehicles=%d respawned_walkers=%d "
                "stuck_vehicles=%d collided_vehicles=%d"
            )
            % (
                len(vehicles),
                args.number_of_vehicles,
                len(walker_records),
                args.number_of_walkers,
                len(new_vehicles),
                len(new_walkers),
                len(stuck_vehicles),
                len(collided_vehicle_ids),
            ),
            flush=True,
        )


def tick_once(world, args, synchronous_master):
    if args.synch and synchronous_master:
        world.tick()
    else:
        world.wait_for_tick()


def live_actor_ids(world, actor_ids):
    live_ids = {actor.id for actor in world.get_actors(actor_ids)}
    return [actor_id for actor_id in actor_ids if actor_id in live_ids]


def live_walker_records(client, world, walker_records):
    actor_ids = []
    for record in walker_records:
        actor_ids.extend([record["walker"], record["controller"]])

    live_ids = {actor.id for actor in world.get_actors(actor_ids)}
    live_records = []
    orphan_ids = []

    for record in walker_records:
        walker_alive = record["walker"] in live_ids
        controller_alive = record["controller"] in live_ids
        if walker_alive and controller_alive:
            live_records.append(record)
        else:
            if walker_alive:
                orphan_ids.append(record["walker"])
            if controller_alive:
                orphan_ids.append(record["controller"])

    destroy_actor_ids(client, orphan_ids)
    return live_records


def build_vehicle_state(world, vehicle_ids, now):
    state = {}
    for actor in world.get_actors(vehicle_ids):
        state[actor.id] = {
            "spawned_at": now,
            "last_moved_at": now,
            "last_location": location_tuple(actor.get_location()),
        }
    return state


def find_stuck_vehicles(world, vehicle_ids, vehicle_state, now):
    stuck_vehicles = []
    actors = world.get_actors(vehicle_ids)
    live_ids = set()

    for actor in actors:
        live_ids.add(actor.id)
        state = vehicle_state.setdefault(
            actor.id,
            {
                "spawned_at": now,
                "last_moved_at": now,
                "last_location": location_tuple(actor.get_location()),
            },
        )

        location = location_tuple(actor.get_location())
        moved_distance = distance(location, state["last_location"])
        speed = vector_length(actor.get_velocity())

        if speed > STUCK_SPEED_MPS or moved_distance > STUCK_DISTANCE_M:
            state["last_moved_at"] = now
            state["last_location"] = location
            continue

        stuck_long_enough = now - state["last_moved_at"] >= STUCK_SECONDS
        if stuck_long_enough:
            stuck_vehicles.append(actor.id)

    for actor_id in list(vehicle_state.keys()):
        if actor_id not in live_ids:
            vehicle_state.pop(actor_id, None)

    return stuck_vehicles


def location_tuple(location):
    return (location.x, location.y, location.z)


def distance(first, second):
    return math.sqrt(
        (first[0] - second[0]) ** 2
        + (first[1] - second[1]) ** 2
        + (first[2] - second[2]) ** 2
    )


def vector_length(vector):
    return math.sqrt(vector.x * vector.x + vector.y * vector.y + vector.z * vector.z)


def attach_collision_sensors(world, vehicle_ids, vehicle_sensors, collided_vehicles):
    if not vehicle_ids:
        return

    blueprint = world.get_blueprint_library().find("sensor.other.collision")
    for vehicle in world.get_actors(vehicle_ids):
        if vehicle.id in vehicle_sensors:
            continue

        try:
            sensor = world.spawn_actor(blueprint, carla.Transform(), attach_to=vehicle)
        except RuntimeError as error:
            logging.warning("Could not attach collision sensor: %s", error)
            continue

        sensor.listen(
            lambda _event, actor_id=vehicle.id: collided_vehicles.add(actor_id)
        )
        vehicle_sensors[vehicle.id] = sensor


def remove_vehicle_sensors(client, vehicle_sensors, vehicle_ids):
    sensor_ids = []
    for vehicle_id in vehicle_ids:
        sensor = vehicle_sensors.pop(vehicle_id, None)
        if sensor is None:
            continue

        try:
            sensor.stop()
        except RuntimeError as error:
            logging.warning("Could not stop collision sensor: %s", error)
        sensor_ids.append(sensor.id)

    destroy_actor_ids(client, sensor_ids)


def destroy_actor_ids(client, actor_ids):
    if actor_ids:
        client.apply_batch([carla.command.DestroyActor(actor_id) for actor_id in actor_ids])


def cleanup(client, vehicles, vehicle_sensors, walker_records):
    remove_vehicle_sensors(client, vehicle_sensors, list(vehicle_sensors.keys()))

    controllers = world_actors_from_records(client, walker_records, "controller")
    for controller in controllers:
        try:
            controller.stop()
        except RuntimeError as error:
            logging.warning("Could not stop walker controller: %s", error)

    print("\ndestroying %d vehicles" % len(vehicles), flush=True)
    destroy_actor_ids(client, vehicles)

    walker_actor_ids = []
    for record in walker_records:
        walker_actor_ids.extend([record["walker"], record["controller"]])

    print("\ndestroying %d walkers" % len(walker_records), flush=True)
    destroy_actor_ids(client, walker_actor_ids)


def world_actors_from_records(client, records, key):
    actor_ids = [record[key] for record in records]
    return client.get_world().get_actors(actor_ids)


def main():
    args = parse_args()
    logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)
    install_signal_handlers()

    client = connect_to_carla(args)
    if args.wait_for_ego:
        world = wait_for_ego_vehicle(client)
    else:
        world = client.get_world()
    traffic_manager = client.get_trafficmanager(args.tm_port)

    vehicles = []
    vehicle_sensors = {}
    collided_vehicles = set()
    walker_records = []
    previous_weather = None
    original_settings = None
    changed_settings = False

    try:
        previous_weather = apply_weather(world, args.weather)
        original_settings, changed_settings, synchronous_master = configure_simulation(
            world, traffic_manager, args
        )

        vehicle_blueprints = get_blueprints(world, args.filterv)
        walker_blueprints = get_blueprints(world, args.filterw)
        vehicles.extend(
            spawn_vehicles(
                client,
                world,
                traffic_manager,
                vehicle_blueprints,
                args.number_of_vehicles,
                synchronous_master,
            )
        )
        attach_collision_sensors(
            world, vehicles, vehicle_sensors, collided_vehicles
        )

        walker_records.extend(
            spawn_walkers(
                client,
                world,
                walker_blueprints,
                args.number_of_walkers,
                args,
                synchronous_master,
            )
        )

        print(
            "Spawned %d vehicles and %d walkers, press Ctrl+C to exit."
            % (len(vehicles), len(walker_records)),
            flush=True,
        )
        supervise_environment(
            client,
            world,
            traffic_manager,
            args,
            vehicle_blueprints,
            walker_blueprints,
            vehicles,
            vehicle_sensors,
            collided_vehicles,
            walker_records,
            synchronous_master,
        )

    finally:
        cleanup(client, vehicles, vehicle_sensors, walker_records)
        restore_simulation(world, original_settings, changed_settings, previous_weather)
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    finally:
        print("\ndone.")
