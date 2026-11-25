#!/usr/bin/env python3
"""Minimal gate participant for sim-clock (SIM_TIME_MODE=gated, GATE_PARTICIPANTS=example_participant).

A participant (any component of the system under test) consumes one input, computes, then publishes
/simulation/step_done with the stamp of the input it used and its participant id. sim-clock holds
the simulation until every participant has acknowledged the current step, so a slow component can get
the time it needs.

    python3 examples/ack_example.py --topic /carla/ego_vehicle/rgb_view/image --compute-ms 200
"""

import argparse
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from rosidl_runtime_py.utilities import get_message
from std_msgs.msg import Header


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", default="/carla/ego_vehicle/rgb_view/image")
    parser.add_argument("--type", default="sensor_msgs/msg/Image")
    parser.add_argument("--participant", default="example_participant")
    parser.add_argument("--compute-ms", type=float, default=200.0)
    args = parser.parse_args()

    rclpy.init()
    node = Node("example_participant")
    ack = node.create_publisher(Header, "/simulation/step_done",
                                QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE))

    def on_input(msg):
        time.sleep(args.compute_ms / 1000.0)        # the component's processing
        # ... publish the component's output (e.g. objects, a trajectory or control) here ...
        done = Header()
        # the stamp of the input this output is based on
        done.stamp = getattr(msg, "header", msg).stamp
        done.frame_id = args.participant
        ack.publish(done)

    # keep only the newest input, as a real-time component would
    node.create_subscription(get_message(args.type), args.topic, on_input,
                             QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST,
                                        reliability=ReliabilityPolicy.BEST_EFFORT))
    rclpy.spin(node)


if __name__ == "__main__":
    main()
