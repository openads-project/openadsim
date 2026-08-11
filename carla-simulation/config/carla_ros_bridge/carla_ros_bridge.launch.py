import os

import launch
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    town = launch.substitutions.LaunchConfiguration(
        'town',
        default='/opendrive.xodr'
    )

    ignore_altitude = launch.substitutions.LaunchConfiguration(
        'ignore_altitude',
        default='true'
    )

    return launch.LaunchDescription([
        launch.actions.IncludeLaunchDescription(
            launch.launch_description_sources.PythonLaunchDescriptionSource(
                os.path.join(
                    get_package_share_directory('carla_ros_bridge'),
                    'carla_ros_bridge.launch.py'
                )
            ),
            launch_arguments={
                'town': town,
                'ignore_altitude': ignore_altitude
            }.items()
        ),
    ])


if __name__ == '__main__':
    generate_launch_description()
