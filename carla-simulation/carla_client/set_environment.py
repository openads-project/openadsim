#!/usr/bin/env python3

"""UE5 environment setup for CARLA traffic and optional weather."""

import argparse
import fnmatch
import logging
import math
import os
import signal
import time

import carla
from numpy import random


FOLLOWING_DISTANCE = 2.5
HYBRID_RADIUS = 70.0
FIXED_DELTA_SECONDS = 0.05
WALKER_SPAWN_Z_OFFSET = 2.0
PEDESTRIANS_RUNNING = 0.0
PEDESTRIANS_CROSSING = 0.0
BACKGROUND_VEHICLE_SPAWN_CLEARANCE_M = 20.0
# Traffic around the ego, the same for vehicles and walkers: nothing spawns closer to the ego than
# EGO_SPAWN_CLEARANCE_M (no pop-in in front of its sensors), NEAR_EGO_TARGET_FRACTION of the
# traffic within NEAR_EGO_SPAWN_RADIUS_M and the rest up to RECYCLE_DISTANCE_M. Traffic farther
# away than RECYCLE_DISTANCE_M is removed and respawned near the ego, so it moves along with it.
EGO_SPAWN_CLEARANCE_M = 100.0
NEAR_EGO_SPAWN_RADIUS_M = 200.0
NEAR_EGO_TARGET_FRACTION = 0.7
RECYCLE_DISTANCE_M = 500.0
WALKER_NAV_LOCATION_ATTEMPTS = 50  # random navmesh locations sampled per walker
CONNECT_RETRY_SECONDS = 2.0
CLIENT_TIMEOUT_SECONDS = 10.0

# Background traffic. TRAFFIC (or --traffic) picks a level: (vehicles, walkers), spawned and
# recycled around the ego as set above. Small maps can have fewer free spawn points than a level
# asks for.
TRAFFIC_LEVELS = {
    "none": (0, 0),
    "light": (30, 10),
    "medium": (60, 30),
    "dense": (100, 60),
}
DEFAULT_TRAFFIC = "light"

# Vehicle blueprints never spawned as background traffic (fnmatch patterns on the blueprint id).
EXCLUDED_VEHICLE_BLUEPRINTS = (
    "vehicle.ika.wheelloader",  # causes problems as background traffic
)

# Weather. By default the map's weather stays as it is; --weather or the WEATHER environment
# variable pick the day/night cycle with passing storms or a fixed weather instead.
CYCLE_WEATHER = "Cycle"
MAP_DEFAULT_WEATHER = "MapDefault"
DEFAULT_WEATHER = MAP_DEFAULT_WEATHER

# Weather cycle, in simulation seconds. The night is short: NIGHT_FRACTION of a day.
CYCLE_DAY_LENGTH_S = 480.0
CYCLE_NIGHT_FRACTION = 0.1
CYCLE_STORM_LENGTH_S = 240.0       # half a day: one storm by day, one at night; 0: no storms
CYCLE_STORM_SHARE = 0.5            # part of a storm cycle with a storm: about 40% bad weather
CYCLE_STORM_PEAK_FRACTION = 0.635  # heavy rain within a storm (precipitation 100 from 0.55 to 0.72)
CYCLE_STORM_PEAK_DELAY = 0.05      # of a day after midnight: the storm clears after sunrise, not with it
CYCLE_START_FRACTION = 0.15        # start in the morning, with the sun at about 30 degrees
CYCLE_SUN_MAX_ALTITUDE_DEG = 60.0  # noon
CYCLE_SUN_MIN_ALTITUDE_DEG = -15.0  # midnight: dark (from about -10), but never pitch black
CYCLE_UPDATE_INTERVAL_S = 0.5      # simulation time between two weather updates
CYCLE_WARMUP_S = 5.0               # blend in from the current weather; a jump can glitch the sky
CYCLE_LOG_INTERVAL_S = 60.0
# Another client, e.g. a scenario with an EnvironmentAction, changed the weather when one of these
# fields moved by more than the tolerance since the cycle set it. The cycle then pauses.
CYCLE_EXTERNAL_CHANGE_FIELDS = (
    "cloudiness", "precipitation", "precipitation_deposits", "wind_intensity",
    "sun_altitude_angle", "fog_density", "wetness",
)
CYCLE_EXTERNAL_CHANGE_TOLERANCE = 0.5

# Storm cycle of PythonAPI/examples/dynamic_day_night_weather_cycle.py (CARLA 1.0): every field
# has its own (fraction, value) keyframes, so clouds come first, then rain, wind, wet road,
# puddles and some fog at the height of the storm, before it clears up again.
STORM_KEYFRAMES = {
    "cloudiness": [(0.00, 0), (0.15, 90), (0.20, 100), (0.75, 100), (0.85, 85), (1.00, 0)],
    "precipitation": [(0.00, 0), (0.19, 0), (0.20, 5), (0.35, 45), (0.55, 100), (0.72, 100),
                      (0.80, 40), (0.90, 0), (1.00, 0)],
    "wind_intensity": [(0.00, 0), (0.30, 0), (0.35, 20), (0.55, 55), (0.72, 100), (0.85, 60),
                       (0.95, 10), (1.00, 0)],
    "wetness": [(0.00, 0), (0.20, 0), (0.22, 10), (0.30, 60), (0.38, 80), (0.55, 95), (0.72, 100),
                (0.85, 70), (0.95, 15), (1.00, 0)],
    "precipitation_deposits": [(0.00, 0), (0.44, 0), (0.45, 10), (0.60, 55), (0.72, 100),
                               (0.88, 60), (0.97, 10), (1.00, 0)],
    "fog_density": [(0.00, 0), (0.44, 0), (0.50, 8), (0.65, 20), (0.72, 30), (0.85, 15),
                    (0.95, 0), (1.00, 0)],
    "fog_distance": [(0.00, 100), (0.50, 100), (0.65, 60), (0.72, 35), (0.85, 70), (1.00, 100)],
    "fog_falloff": [(0.00, 1.0), (0.50, 1.0), (0.72, 2.0), (1.00, 1.0)],
    "scattering_intensity": [(0.00, 1.0), (0.60, 1.0), (0.72, 2.5), (0.90, 1.0), (1.00, 1.0)],
}
WEATHER_FIELDS = [
    "cloudiness", "precipitation", "precipitation_deposits", "wind_intensity", "sun_azimuth_angle",
    "sun_altitude_angle", "fog_density", "fog_distance", "fog_falloff", "wetness",
    "scattering_intensity", "mie_scattering_scale", "rayleigh_scattering_scale", "dust_storm",
]

# Fixed weathers of OpenADSim. Fields left out keep the map's values, including the sun azimuth.
CUSTOM_WEATHERS = {
    "OpenADSAfternoon": {
        "cloudiness": 40.0,              # some clouds dim the light a little
        "precipitation": 40.0,            # a trace of rain
        "precipitation_deposits": 0.01,  # a few small puddles: a slightly wet road
        "wetness": 10.0,                 # wet sheen, RGB cameras only
        "wind_intensity": 10.0,
        "sun_altitude_angle": 20.0,       # low evening sun; street lights switch on only below 0.8
        "rayleigh_scattering_scale": 0.05,  # default 0.0331; reddens the low sun and the sky near it
        "mie_scattering_scale": 0.05,       # default 0.03; warm haze and halo around the sun
        "fog_density": 10.0,
    },
}
EGO_ROLE_NAME = "ego_vehicle"
BACKGROUND_ROLE_NAME = "autopilot"

STATUS_INTERVAL = 5.0
LIGHT_UPDATE_INTERVAL = 1.0

# Vehicle lights follow the weather with the thresholds of the Traffic Manager
# (LibCarla/source/carla/trafficmanager/Constants.h), so the ego looks like the traffic.
SUN_ALTITUDE_LOW_BEAM_DEG = 15.0   # below: position lights and low beam
SUN_ALTITUDE_POSITION_DEG = 35.0   # below: position lights
HEAVY_PRECIPITATION = 80.0         # above: position lights and low beam
FOG_DENSITY_FOG_LIGHTS = 20.0      # above: position lights, low beam and fog lights
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
    parser.add_argument(
        "--host", default=os.environ.get("CARLA_HOST", "carla-server"),
        help="CARLA server host",
    )
    parser.add_argument(
        "-p", "--port", default=os.environ.get("CARLA_PORT", "2000"),
        type=int, help="CARLA server port",
    )
    parser.add_argument("--tm-port", default=8000, type=int, help="Traffic Manager port")
    parser.add_argument(
        "--traffic",
        metavar="LEVEL",
        default=os.environ.get("TRAFFIC") or DEFAULT_TRAFFIC,
        help="Background traffic: %s (default %s, or the TRAFFIC environment variable)"
        % (", ".join(
            "%s (%d vehicles, %d walkers)" % (level, *counts)
            for level, counts in TRAFFIC_LEVELS.items()
        ), DEFAULT_TRAFFIC),
    )
    parser.add_argument(
        "-n",
        "--number-of-vehicles",
        type=int,
        help="Number of vehicles, overrides the traffic level",
    )
    parser.add_argument(
        "-w",
        "--number-of-walkers",
        type=int,
        help="Number of walkers, overrides the traffic level",
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
        default=os.environ.get("WEATHER") or DEFAULT_WEATHER,
        help='Weather: "%s" (default, or the WEATHER environment variable) leaves the map\'s '
        'weather alone, e.g. while another client changes it; "%s" cycles through day and night '
        'with passing storms; a fixed weather is "OpenADSAfternoon" or a CARLA preset such as '
        '"ClearNoon" or "WetCloudySunset"' % (MAP_DEFAULT_WEATHER, CYCLE_WEATHER),
    )
    parser.add_argument(
        "--day-length", type=float, default=CYCLE_DAY_LENGTH_S,
        help="Weather cycle: length of one day in simulation seconds (default %(default)s)",
    )
    parser.add_argument(
        "--night-fraction", type=float, default=CYCLE_NIGHT_FRACTION,
        help="Weather cycle: share of the day the sun is below the horizon (default %(default)s)",
    )
    parser.add_argument(
        "--storm-length", type=float, default=CYCLE_STORM_LENGTH_S,
        help="Weather cycle: length of one storm cycle in simulation seconds, 0 for no storms "
        "(default %(default)s)",
    )
    args = parser.parse_args()
    if args.day_length <= 0 or not 0.0 < args.night_fraction < 1.0 or args.storm_length < 0:
        parser.error("--day-length must be > 0, --night-fraction in (0, 1), --storm-length >= 0")
    args.traffic = args.traffic.strip().lower()
    if args.traffic not in TRAFFIC_LEVELS:
        parser.error(
            "Unknown traffic level '%s'. Available levels: %s"
            % (args.traffic, ", ".join(TRAFFIC_LEVELS))
        )
    vehicles, walkers = TRAFFIC_LEVELS[args.traffic]
    if args.number_of_vehicles is None:
        args.number_of_vehicles = vehicles
    if args.number_of_walkers is None:
        args.number_of_walkers = walkers
    return args


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


def get_blueprints(world, pattern, excluded=()):
    blueprints = world.get_blueprint_library().filter(pattern)
    kept = [
        blueprint
        for blueprint in blueprints
        if not any(fnmatch.fnmatchcase(blueprint.id, exclude) for exclude in excluded)
    ]
    skipped = sorted({blueprint.id for blueprint in blueprints} - {blueprint.id for blueprint in kept})
    if skipped:
        logging.info("Excluded blueprints: %s", ", ".join(skipped))
    if not kept:
        raise ValueError("No blueprints found for filter: %s" % pattern)
    return sorted(kept, key=lambda blueprint: blueprint.id)


def apply_weather(world, preset_name):
    if not preset_name or preset_name == MAP_DEFAULT_WEATHER:
        return None
    if preset_name == CYCLE_WEATHER:
        logging.info("Weather cycles through day and night, driven by the supervision loop")
        return world.get_weather()

    if preset_name in CUSTOM_WEATHERS:
        # Start from the map's weather so that unset fields, e.g. the sun azimuth, stay as they are.
        weather = world.get_weather()
        for field, value in CUSTOM_WEATHERS[preset_name].items():
            setattr(weather, field, value)
    elif hasattr(carla.WeatherParameters, preset_name):
        weather = getattr(carla.WeatherParameters, preset_name)
    else:
        presets = [
            name
            for name in dir(carla.WeatherParameters)
            if not name.startswith("_") and name[0].isupper()
        ]
        raise ValueError(
            "Unknown weather preset '%s'. Available presets: %s"
            % (preset_name, ", ".join(sorted(list(CUSTOM_WEATHERS) + [CYCLE_WEATHER, MAP_DEFAULT_WEATHER] + presets)))
        )

    previous_weather = world.get_weather()
    world.set_weather(weather)
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


def sample_keyframes(keyframes, fraction):
    """Linear interpolation of (fraction, value) keyframes."""
    if fraction <= keyframes[0][0]:
        return keyframes[0][1]
    for (f0, v0), (f1, v1) in zip(keyframes, keyframes[1:]):
        if fraction <= f1:
            return v0 + (v1 - v0) * ((fraction - f0) / (f1 - f0) if f1 > f0 else 0.0)
    return keyframes[-1][1]


def cycle_sun(fraction, night_fraction):
    """Sun altitude and azimuth over one day: a long day arc up to noon, then a short night arc."""
    day_fraction = 1.0 - night_fraction
    if fraction < day_fraction:
        altitude = CYCLE_SUN_MAX_ALTITUDE_DEG * math.sin(math.pi * fraction / day_fraction)
    else:
        # Ease into and out of the night: the sun lingers near the horizon and only dips fast in
        # the middle of the night. A short night otherwise drops the light within seconds, faster
        # than the cameras' auto exposure follows (pitch black at dusk, a flash at dawn). CARLA
        # hands the light over from the sun to the moon within +-8 degrees of the horizon.
        night = (fraction - day_fraction) / night_fraction
        eased = night - math.sin(2.0 * math.pi * night) / (2.0 * math.pi)
        altitude = CYCLE_SUN_MIN_ALTITUDE_DEG * math.sin(math.pi * eased)
    return altitude, (360.0 * fraction) % 360.0


def copy_weather(weather):
    return carla.WeatherParameters(**{field: getattr(weather, field) for field in WEATHER_FIELDS})


def changed_externally(expected, current):
    """True if the weather differs from the one the cycle set last."""
    return any(
        abs(getattr(current, field) - getattr(expected, field)) > CYCLE_EXTERNAL_CHANGE_TOLERANCE
        for field in CYCLE_EXTERNAL_CHANGE_FIELDS
    )


class WeatherCycle:
    """Day/night and storm cycle on simulation time, updated from the supervision loop.

    The cycle yields to other clients: once another client changes the weather, e.g. a scenario
    that defines its own, the cycle pauses until the next episode (map reload).
    """

    def __init__(self, day_length, night_fraction, storm_length):
        self.day_length = day_length
        self.night_fraction = night_fraction
        self.storm_length = storm_length
        self.start_sim_time = None
        self.last_sim_time = None
        self.last_update = None
        self.last_log = None
        self.start_weather = None
        self.last_weather = None
        self.paused = False
        # Shift the storms so that the heavy rain of one falls just after midnight (and with a
        # storm cycle of half a day, the other one just after noon).
        midnight = ((1.0 - night_fraction / 2.0 - CYCLE_START_FRACTION) % 1.0) * day_length
        midnight += CYCLE_STORM_PEAK_DELAY * day_length
        peak = CYCLE_STORM_PEAK_FRACTION * CYCLE_STORM_SHARE * storm_length
        self.storm_offset = (midnight - peak) % storm_length if storm_length > 0 else 0.0

    def storm_fraction(self, elapsed):
        """Position in the storm keyframes; 1.0 (clear) outside the storm part of the cycle."""
        if self.storm_length <= 0:
            return 0.0
        phase = ((elapsed - self.storm_offset) / self.storm_length) % 1.0
        return phase / CYCLE_STORM_SHARE if phase < CYCLE_STORM_SHARE else 1.0

    def weather_at(self, elapsed):
        weather = copy_weather(self.start_weather)  # keeps the fields the cycle does not drive
        day = (CYCLE_START_FRACTION + elapsed / self.day_length) % 1.0
        weather.sun_altitude_angle, weather.sun_azimuth_angle = cycle_sun(day, self.night_fraction)
        storm = self.storm_fraction(elapsed)
        for field, keyframes in STORM_KEYFRAMES.items():
            setattr(weather, field, sample_keyframes(keyframes, storm))
        return weather

    def update(self, world):
        now = world.get_snapshot().timestamp.elapsed_seconds
        if self.start_sim_time is None or now < self.last_sim_time:
            # first call, or the episode restarted (map reload): start over from its weather
            if self.paused:
                logging.info("Weather cycle: new episode, resuming")
            self.start_sim_time, self.last_update, self.last_log = now, None, None
            self.start_weather = world.get_weather()
            self.last_weather = None
            self.paused = False
        self.last_sim_time = now
        if self.paused:
            return
        elapsed = now - self.start_sim_time
        if self.last_update is not None and elapsed - self.last_update < CYCLE_UPDATE_INTERVAL_S:
            return
        self.last_update = elapsed

        if self.last_weather is not None and changed_externally(self.last_weather, world.get_weather()):
            logging.info(
                "Weather cycle: another client, e.g. a scenario, set the weather; "
                "pausing until the next episode"
            )
            self.paused = True
            return

        weather = self.weather_at(elapsed)
        if elapsed < CYCLE_WARMUP_S:
            alpha = elapsed / CYCLE_WARMUP_S
            for field in WEATHER_FIELDS:
                start = getattr(self.start_weather, field)
                setattr(weather, field, start + (getattr(weather, field) - start) * alpha)
        world.set_weather(weather)
        self.last_weather = weather

        if self.last_log is None or elapsed - self.last_log >= CYCLE_LOG_INTERVAL_S:
            self.last_log = elapsed
            logging.info(
                "Weather cycle: sun %.0f deg, clouds %.0f, rain %.0f, puddles %.0f, fog %.0f",
                weather.sun_altitude_angle, weather.cloudiness, weather.precipitation,
                weather.precipitation_deposits, weather.fog_density,
            )


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

    vehicle_ids = apply_spawn_batch(client, batch, synchronous_master)
    # Let the Traffic Manager switch the lights of the traffic with the weather.
    for vehicle in world.get_actors(vehicle_ids):
        traffic_manager.update_vehicle_lights(vehicle, True)
    return vehicle_ids


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
        if EGO_SPAWN_CLEARANCE_M
        <= spawn_point.location.distance(ego_location)
        <= RECYCLE_DISTANCE_M
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
        (len(background_vehicles) + count) * NEAR_EGO_TARGET_FRACTION
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
        EGO_SPAWN_CLEARANCE_M,
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


def spawn_walkers(client, world, blueprints, count, args, synchronous_master, walker_ids=()):
    if count <= 0:
        return []

    world.set_pedestrians_cross_factor(PEDESTRIANS_CROSSING)

    spawn_points = find_walker_spawn_points(world, count, walker_ids)
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


def find_walker_spawn_points(world, count, walker_ids=()):
    """Navmesh locations for count walkers, around the ego like the vehicle spawn points."""
    ego = find_ego_vehicle(world)
    ego_location = ego.get_location() if ego is not None else None
    near_locations = []
    distant_locations = []

    for _ in range(count * WALKER_NAV_LOCATION_ATTEMPTS):
        if len(distant_locations) >= count and (
            ego_location is None or len(near_locations) >= count
        ):
            break

        location = world.get_random_location_from_navigation()
        if location is None:
            continue
        if ego_location is None:
            distant_locations.append(location)
            continue
        distance = location.distance(ego_location)
        if EGO_SPAWN_CLEARANCE_M <= distance <= NEAR_EGO_SPAWN_RADIUS_M:
            near_locations.append(location)
        elif NEAR_EGO_SPAWN_RADIUS_M < distance <= RECYCLE_DISTANCE_M:
            distant_locations.append(location)

    if ego_location is None:
        selected = distant_locations[:count]
    else:
        current_nearby = sum(
            walker.get_location().distance(ego_location) <= NEAR_EGO_SPAWN_RADIUS_M
            for walker in world.get_actors(list(walker_ids))
        )
        desired_nearby = round((len(walker_ids) + count) * NEAR_EGO_TARGET_FRACTION)
        near_target = min(count, max(0, desired_nearby - current_nearby))
        selected = near_locations[:near_target]
        selected += distant_locations[:count - len(selected)]
        selected += near_locations[near_target:near_target + count - len(selected)]
        selected_nearby = sum(
            location.distance(ego_location) <= NEAR_EGO_SPAWN_RADIUS_M for location in selected
        )
        logging.info(
            "Selected %d/%d walker spawn points within %.0f-%.0f m of the ego "
            "(current=%d target=%d)",
            selected_nearby,
            len(selected),
            EGO_SPAWN_CLEARANCE_M,
            NEAR_EGO_SPAWN_RADIUS_M,
            current_nearby,
            desired_nearby,
        )

    spawn_points = []
    for location in selected:
        location.z += WALKER_SPAWN_Z_OFFSET
        spawn_points.append(carla.Transform(location))
    return spawn_points


def walker_target(world, ego_location):
    """A random navmesh location within NEAR_EGO_SPAWN_RADIUS_M of the ego, else the closest one
    sampled; anywhere without an ego."""
    closest = None
    closest_distance = math.inf
    for _ in range(WALKER_NAV_LOCATION_ATTEMPTS):
        location = world.get_random_location_from_navigation()
        if location is None:
            continue
        if ego_location is None:
            return location
        distance = location.distance(ego_location)
        if distance <= NEAR_EGO_SPAWN_RADIUS_M:
            return location
        if distance < closest_distance:
            closest, closest_distance = location, distance
    return closest


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
    ego = find_ego_vehicle(world)
    ego_location = ego.get_location() if ego is not None else None

    for controller in controllers:
        controller.start()
        target = walker_target(world, ego_location)
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
    weather_cycle=None,
):
    vehicle_state = build_vehicle_state(world, vehicles, time.monotonic())
    next_status = time.monotonic()
    next_lights = time.monotonic()

    while True:
        tick_once(world, args, synchronous_master)

        if weather_cycle is not None:
            weather_cycle.update(world)

        now = time.monotonic()
        if now >= next_lights:
            next_lights = now + LIGHT_UPDATE_INTERVAL
            update_ego_lights(world)
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

        # Recycle the traffic that is too far from the ego: it respawns near the ego below.
        ego = find_ego_vehicle(world)
        ego_location = ego.get_location() if ego is not None else None
        far_vehicles = far_actor_ids(world, vehicles, ego_location)
        far_walkers = far_actor_ids(
            world, [record["walker"] for record in walker_records], ego_location
        )
        if far_walkers:
            destroy_walker_records(
                client, [record for record in walker_records if record["walker"] in far_walkers]
            )
            walker_records[:] = [
                record for record in walker_records if record["walker"] not in far_walkers
            ]

        bad_vehicles = set(stuck_vehicles) | set(collided_vehicle_ids) | far_vehicles
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
            walker_ids=[record["walker"] for record in walker_records],
        )
        walker_records.extend(new_walkers)

        print(
            (
                "environment: vehicles=%d/%d walkers=%d/%d "
                "respawned_vehicles=%d respawned_walkers=%d "
                "stuck_vehicles=%d collided_vehicles=%d "
                "recycled_vehicles=%d recycled_walkers=%d"
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
                len(far_vehicles),
                len(far_walkers),
            ),
            flush=True,
        )


def weather_light_state(weather):
    """Position, low beam and fog lights for the weather, as the Traffic Manager sets them."""
    lights = carla.VehicleLightState.NONE
    if weather.sun_altitude_angle < SUN_ALTITUDE_POSITION_DEG:
        lights |= carla.VehicleLightState.Position
    if weather.sun_altitude_angle < SUN_ALTITUDE_LOW_BEAM_DEG or weather.precipitation > HEAVY_PRECIPITATION:
        lights |= carla.VehicleLightState.Position | carla.VehicleLightState.LowBeam
    if weather.fog_density > FOG_DENSITY_FOG_LIGHTS:
        lights |= (carla.VehicleLightState.Position | carla.VehicleLightState.LowBeam
                   | carla.VehicleLightState.Fog)
    return int(lights)


def update_ego_lights(world):
    """Switch the ego's weather-dependent lights; brake lights, blinkers and high beam stay as they are."""
    ego = find_ego_vehicle(world)
    if ego is None:
        return
    managed = int(carla.VehicleLightState.Position | carla.VehicleLightState.LowBeam
                  | carla.VehicleLightState.Fog)
    try:
        current = int(ego.get_light_state())
        wanted = (current & ~managed) | weather_light_state(world.get_weather())
        if wanted != current:
            ego.set_light_state(carla.VehicleLightState(wanted))
            logging.info("Ego lights set to %s", carla.VehicleLightState(wanted))
    except RuntimeError as error:
        logging.warning("Could not update the ego lights: %s", error)


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


def far_actor_ids(world, actor_ids, ego_location):
    """Ids of the actors farther from the ego than RECYCLE_DISTANCE_M; none without an ego."""
    if ego_location is None or not actor_ids:
        return set()
    return {
        actor.id
        for actor in world.get_actors(list(actor_ids))
        if actor.get_location().distance(ego_location) > RECYCLE_DISTANCE_M
    }


def destroy_walker_records(client, walker_records):
    """Stop the walkers' controllers, then destroy walkers and controllers."""
    for controller in world_actors_from_records(client, walker_records, "controller"):
        try:
            controller.stop()
        except RuntimeError as error:
            logging.warning("Could not stop walker controller: %s", error)

    walker_actor_ids = []
    for record in walker_records:
        walker_actor_ids.extend([record["walker"], record["controller"]])
    destroy_actor_ids(client, walker_actor_ids)


def cleanup(client, vehicles, vehicle_sensors, walker_records):
    remove_vehicle_sensors(client, vehicle_sensors, list(vehicle_sensors.keys()))

    print("\ndestroying %d vehicles" % len(vehicles), flush=True)
    destroy_actor_ids(client, vehicles)

    print("\ndestroying %d walkers" % len(walker_records), flush=True)
    destroy_walker_records(client, walker_records)


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
        weather_cycle = (
            WeatherCycle(args.day_length, args.night_fraction, args.storm_length)
            if args.weather == CYCLE_WEATHER else None
        )
        original_settings, changed_settings, synchronous_master = configure_simulation(
            world, traffic_manager, args
        )

        logging.info(
            "Traffic %s: %d vehicles, %d walkers",
            args.traffic, args.number_of_vehicles, args.number_of_walkers,
        )
        vehicle_blueprints = get_blueprints(world, args.filterv, EXCLUDED_VEHICLE_BLUEPRINTS)
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
            weather_cycle,
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
