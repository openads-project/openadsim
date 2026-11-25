import unittest

from sim_clock.config import ConfigError, is_topic_pattern, load, parse_member, topic_matches


class ConfigTest(unittest.TestCase):

    def test_disabled_without_mode(self):
        self.assertFalse(load({}).enabled)
        self.assertFalse(load({"SIM_TIME_MODE": ""}).enabled)
        self.assertFalse(load({"SIM_TIME_MODE": "  "}).enabled)

    def test_realtime_defaults_to_auto(self):
        cfg = load({"CARLA_BRIDGE_PASSIVE": "true", "SIM_TIME_MODE": "realtime"})
        self.assertTrue(cfg.enabled)
        self.assertIsNone(cfg.rt_factor)
        cfg = load({"CARLA_BRIDGE_PASSIVE": "true", "SIM_TIME_MODE": "realtime", "SIM_RT_FACTOR": ""})
        self.assertIsNone(cfg.rt_factor)

    def test_factor_choices(self):
        for value in ("0.25", "0.5", "0.75", "1.0", "1"):
            self.assertEqual(load({"CARLA_BRIDGE_PASSIVE": "true", "SIM_TIME_MODE": "realtime", "SIM_RT_FACTOR": value}).rt_factor, float(value))
        for value in ("0.3", "2", "fast", "-1"):
            with self.assertRaises(ConfigError):
                load({"CARLA_BRIDGE_PASSIVE": "true", "SIM_TIME_MODE": "realtime", "SIM_RT_FACTOR": value})

    def test_invalid_mode_and_backend(self):
        with self.assertRaises(ConfigError):
            load({"SIM_TIME_MODE": "slow"})
        with self.assertRaises(ConfigError):
            load({"SIM_TIME_MODE": "fast", "SIM_CLOCK_BACKEND": "ros"})

    def test_gated_needs_members(self):
        with self.assertRaises(ConfigError):
            load({"CARLA_BRIDGE_PASSIVE": "true", "SIM_TIME_MODE": "gated"})
        cfg = load({"CARLA_BRIDGE_PASSIVE": "true", "SIM_TIME_MODE": "gated", "GATE_PARTICIPANTS": "my_planner, recorder"})
        self.assertEqual(cfg.gate_participants, ["my_planner", "recorder"])
        cfg = load({"CARLA_BRIDGE_PASSIVE": "true", "SIM_TIME_MODE": "gated", "GATE_TOPICS": "/control/x/controls"})
        self.assertEqual(cfg.gate_topics, ["/control/x/controls"])

    def test_carla_backend_needs_passive_bridge(self):
        with self.assertRaises(ConfigError):
            load({"SIM_TIME_MODE": "fast"})
        self.assertTrue(load({"SIM_TIME_MODE": "fast", "CARLA_BRIDGE_PASSIVE": "true"}).enabled)
        self.assertTrue(load({"SIM_TIME_MODE": "fast", "SIM_CLOCK_BACKEND": "traci"}).enabled)

    def test_auto_needs_warmup(self):
        with self.assertRaises(ConfigError):
            load({"CARLA_BRIDGE_PASSIVE": "true", "SIM_TIME_MODE": "realtime", "WARMUP_S": "0"})
        cfg = load({"CARLA_BRIDGE_PASSIVE": "true", "SIM_TIME_MODE": "realtime", "WARMUP_S": "0",
                    "SIM_RT_FACTOR": "0.5"})
        self.assertEqual(cfg.rt_factor, 0.5)

    def test_warmup_only_by_default_for_auto(self):
        base = {"CARLA_BRIDGE_PASSIVE": "true"}
        self.assertEqual(load(dict(base, SIM_TIME_MODE="realtime")).warmup_s, 60.0)
        self.assertEqual(load(dict(base, SIM_TIME_MODE="realtime", SIM_RT_FACTOR="0.5")).warmup_s, 0.0)
        self.assertEqual(load(dict(base, SIM_TIME_MODE="fast")).warmup_s, 0.0)
        self.assertEqual(load(dict(base, SIM_TIME_MODE="gated", GATE_TOPICS="/x")).warmup_s, 0.0)
        self.assertEqual(load(dict(base, SIM_TIME_MODE="gated", GATE_TOPICS="/x", WARMUP_S="")).warmup_s, 0.0)
        # an explicit value applies to every mode
        self.assertEqual(load(dict(base, SIM_TIME_MODE="gated", GATE_TOPICS="/x", WARMUP_S="30")).warmup_s, 30.0)
        self.assertEqual(load(dict(base, SIM_TIME_MODE="realtime", WARMUP_S="20")).warmup_s, 20.0)

    def test_stop_after_sim_seconds(self):
        cfg = load({"CARLA_BRIDGE_PASSIVE": "true", "SIM_TIME_MODE": "fast", "SIM_CLOCK_STOP_AFTER_SIM_S": "30"})
        self.assertEqual(cfg.stop_after_sim_s, 30.0)
        cfg = load({"SIM_TIME_MODE": "gated", "SIM_CLOCK_BACKEND": "traci",
                    "GATE_TOPICS": "/carla/ego_vehicle/karl_rack_cameras/*/image/compressed"})
        self.assertEqual(cfg.gate_topics, ["/carla/ego_vehicle/karl_rack_cameras/*/image/compressed"])

    def test_gate_on_timeout(self):
        with self.assertRaises(ConfigError):
            load({"CARLA_BRIDGE_PASSIVE": "true", "SIM_TIME_MODE": "gated", "GATE_PARTICIPANTS": "a", "GATE_ON_TIMEOUT": "ignore"})

    def test_gate_topic_patterns(self):
        self.assertFalse(is_topic_pattern("/simulation/object_list"))
        self.assertTrue(is_topic_pattern("/carla/ego_vehicle/*/image/compressed"))
        self.assertTrue(is_topic_pattern("^/carla/ego_vehicle/.*/image/compressed$"))
        topic = "/carla/ego_vehicle/karl_rack_cameras/camera_front_left/image/compressed"
        self.assertTrue(topic_matches("/carla/ego_vehicle/*/image/compressed", topic))
        self.assertTrue(topic_matches("^/carla/ego_vehicle/.*/image/compressed$", topic))
        self.assertFalse(topic_matches("^/carla/ego_vehicle/.*/image/compressed$", topic + "/x"))
        self.assertFalse(topic_matches("^/carla/ego_vehicle/.*/image$", topic))

    def test_member_periods(self):
        self.assertEqual(parse_member("/simulation/object_list"), ("/simulation/object_list", 0.0))
        self.assertEqual(parse_member("/carla/ego_vehicle/lidar/point_cloud@0.1"),
                         ("/carla/ego_vehicle/lidar/point_cloud", 0.1))
        self.assertEqual(parse_member("^/carla/.*/point_cloud$@0.1"), ("^/carla/.*/point_cloud$", 0.1))
        for entry in ("/x@", "/x@fast", "/x@0", "/x@-0.1", "@0.1"):
            with self.assertRaises(ConfigError):
                parse_member(entry)
        cfg = load({"SIM_TIME_MODE": "gated", "SIM_CLOCK_BACKEND": "traci",
                    "GATE_PARTICIPANTS": "my_planner@0.1, recorder",
                    "GATE_TOPICS": "/simulation/object_list, /carla/ego_vehicle/*/point_cloud@0.1"})
        self.assertEqual(cfg.gate_participants, ["my_planner", "recorder"])
        self.assertEqual(cfg.gate_topics, ["/simulation/object_list", "/carla/ego_vehicle/*/point_cloud"])
        self.assertEqual(cfg.gate_periods, {"my_planner": 0.1, "recorder": 0.0, "/simulation/object_list": 0.0,
                                            "/carla/ego_vehicle/*/point_cloud": 0.1})


if __name__ == "__main__":
    unittest.main()
