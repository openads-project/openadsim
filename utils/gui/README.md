# OpenADSim Configuration GUI

The OpenADSim Configuration GUI is a Streamlit-based internal helper tool for configuring and controlling
the local OpenADSim Compose environment.

It can:
- load existing `.env` configurations and expose them as editable forms
- apply presets such as `scenario-execution` and `cooperative-perception`
- configure profiles, vehicle, sensors, map, scenario and additional options
- validate and import OpenSCENARIO bundles with optional OpenDRIVE/Lanelet2 maps
- show scenarios and Lanelet2 files from the selected OpenDRIVE file's directory, additionally matching scenarios to their OpenDRIVE reference, while keeping OpenDRIVE maps freely selectable
- validate configuration dependencies and show warnings/errors
- automatically sync valid drafts back to `.env`
- run `docker compose up -d` and stop/remove running containers
- list Compose containers and monitor their current status

Disclaimer: This Configuration GUI is `vibe-coded` in many places and is primarily a practical
convenience tool, not a polished product. Review outputs, validations and Docker
actions critically before using it in production or as a reference for robust
automation.
