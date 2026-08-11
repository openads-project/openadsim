from pathlib import Path

import launch
import launch_ros.actions
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    ld = launch.LaunchDescription([
        launch.actions.DeclareLaunchArgument(
            name='role_name',
            default_value='ego_vehicle'
        ),
        launch.actions.DeclareLaunchArgument(
            name='control_loop_rate',
            default_value='0.05'
        ),
        launch_ros.actions.Node(
            package='carla_ackermann_control',
            executable='carla_ackermann_control_node',
            name='carla_ackermann_control',
            output='screen',
            remappings=[
                ('/carla/ego_vehicle/ackermann_cmd', '/control/ackermann_trajectory_control/controls'),
                ('/carla/ego_vehicle/vehicle_control_cmd', '/carla/ego_vehicle/vehicle_control_cmd_automated'),
            ],
            parameters=[
                Path(get_package_share_directory('carla_ackermann_control'), "config/params.yml"),
                {
                    'role_name': launch.substitutions.LaunchConfiguration('role_name'),
                    'control_loop_rate': launch.substitutions.LaunchConfiguration('control_loop_rate')
                }
            ]
        )
    ])
    return ld


if __name__ == '__main__':
    generate_launch_description()
