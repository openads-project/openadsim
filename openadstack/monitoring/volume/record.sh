#!/usr/bin/env bash

ros2 bag record \
  /rosout \
  /tf \
  /tf_static \
  /simulation/ego_data \
  /simulation/imu \
  /simulation/odometry \
  /simulation/vehicle_state \
  /simulation/object_list \
  /localization/ego_state_estimation/ego_data \
  /planning/lanelet2_route_planning/route \
  /planning/simple_planner/trajectory \
  /planning/trajectory_optimization/trajectory \
  /understanding/autoware_multi_object_tracker/object_list \
  /understanding/lanelet2_object_list_prediction/object_list