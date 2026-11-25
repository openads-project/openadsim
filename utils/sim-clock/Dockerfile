# sim-clock: tick master of OpenADSim (CARLA Python API 1.0, ROS 2 jazzy, TraCI)
FROM ghcr.io/openads-project/carla-ros-bridge:v1.0.0-1.0.0 AS carla

# the monitoring image has rclpy and the message types of OpenADStack (GATE_TOPICS can be e.g. /simulation/object_list)
FROM ghcr.io/openads-project/monitoring:v26.10.20

# CARLA Python API 1.0 (not on PyPI): the wheel the bridge image is built with, matching the server
# TraCI client matching SUMO 1.28 of sumo_interface
COPY --from=carla /opt/carla/PythonAPI/carla/dist/carla-1.0-cp312-cp312-linux_x86_64.whl /tmp/
RUN pip3 install --no-cache-dir --break-system-packages /tmp/carla-1.0-cp312-cp312-linux_x86_64.whl traci==1.28.0 \
    && rm /tmp/carla-1.0-cp312-cp312-linux_x86_64.whl

COPY sim_clock /opt/sim-clock/sim_clock
COPY examples /opt/sim-clock/examples
COPY tests /opt/sim-clock/tests
ENV PYTHONPATH=/opt/sim-clock
WORKDIR /opt/sim-clock
