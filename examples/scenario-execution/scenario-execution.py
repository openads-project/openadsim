#!/usr/bin/env python3

"""Run OpenSCENARIO files sequentially with isolated Compose environments."""

import argparse
import fnmatch
import json
import logging
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_FOLDER = "carla-simulation/scenarios"
DEFAULT_SCENARIO_FILTER = "*.xosc"
DEFAULT_WAIT_SECONDS = "5"
PROGRESS_INTERVAL_SECONDS = 10
COMPOSE_COMMAND = ("docker", "compose", "--env-file", "/dev/null")
SCENARIO_CHECKER = ROOT_DIR / "utils/scenario-checker/scenario_checker.py"
ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DELAY_PATTERN = re.compile(r"^[0-9]+(?:[.][0-9]+)?$")

SCRIPT_MANAGED_ENV_NAMES = {
    "BAG_DIRECTORY",
    "DATA_DIRECTORY",
    "LANELET_RELOAD",
    "OP_DIRECTORY",
    "SCENARIO_FILE",
    "SCENARIO_NAME",
}
DERIVED_ENV_NAMES = SCRIPT_MANAGED_ENV_NAMES | {
    "CUSTOM_LANELET",
    "CUSTOM_OPENDRIVE",
    "MAP",
}
RUNTIME_ARGUMENT_NAMES = {"SCENARIO_FILTER", "TIME_BETWEEN_EXECUTIONS"}
LOGGER = logging.getLogger("scenario-execution")


class ConfigError(ValueError):
    """Raised when the scenario JSON is invalid."""


class ScenarioExecutionError(RuntimeError):
    """Raised for a user-facing runtime error."""


@dataclass(frozen=True)
class Rule:
    name: str
    selector: str
    value: str
    environment: dict[str, str]

    def matches(self, scenario_file: str) -> bool:
        if self.selector == "scenario":
            return scenario_file == self.value
        return re.search(self.value, scenario_file) is not None


@dataclass(frozen=True)
class Config:
    environment: dict[str, str]
    rules: list[Rule]

    def resolve(self, scenario_file: str) -> tuple[dict[str, str], list[str]]:
        overrides: dict[str, str] = {}
        matched_rules = []
        for rule in self.rules:
            if rule.matches(scenario_file):
                overrides.update(rule.environment)
                matched_rules.append(rule.name)
        return overrides, matched_rules


@dataclass(frozen=True)
class MapSelection:
    description: str
    scenario_opendrive: Path | None = None
    conversion_opendrive: Path | None = None


def validate_environment(
    environment: object,
    location: str,
) -> dict[str, str]:
    if not isinstance(environment, dict):
        raise ConfigError(f"{location} must be an object")

    validated = {}
    for name, value in environment.items():
        if not isinstance(name, str) or not ENV_NAME_PATTERN.fullmatch(name):
            raise ConfigError(f"{location} contains invalid environment name {name!r}")
        if name in SCRIPT_MANAGED_ENV_NAMES:
            raise ConfigError(f"{location}.{name} is managed by scenario-execution.py")
        if name in RUNTIME_ARGUMENT_NAMES:
            raise ConfigError(
                f"{location}.{name} is a runtime argument and cannot be set in JSON"
            )
        if name == "OP_CONV_OPENDRIVE_FILE":
            raise ConfigError(f"{location}.{name} was renamed to OP_OPENDRIVE")
        if not isinstance(value, str):
            raise ConfigError(f"{location}.{name} must be a string")
        if "\0" in value:
            raise ConfigError(f"{location}.{name} must not contain a null byte")
        validated[name] = value

    if validated.get("MAP") and validated.get("CUSTOM_OPENDRIVE"):
        raise ConfigError(
            f"{location}.MAP and {location}.CUSTOM_OPENDRIVE cannot both be non-empty"
        )
    return validated


def load_config(path: Path) -> Config:
    try:
        with path.open(encoding="utf-8") as config_file:
            raw_config = json.load(config_file)
    except OSError as error:
        raise ConfigError(str(error)) from error
    except json.JSONDecodeError as error:
        raise ConfigError(
            f"{path}:{error.lineno}:{error.colno}: {error.msg}"
        ) from error

    if not isinstance(raw_config, dict):
        raise ConfigError("top-level value must be an object")
    unknown_keys = set(raw_config) - {"env", "rules"}
    if unknown_keys:
        raise ConfigError(f"unknown top-level keys: {', '.join(sorted(unknown_keys))}")
    if "env" not in raw_config:
        raise ConfigError('required top-level key "env" is missing')

    environment = validate_environment(raw_config["env"], "env")
    if not environment.get("COMPOSE_PROFILES"):
        raise ConfigError("env.COMPOSE_PROFILES must be a non-empty string")

    raw_rules = raw_config.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ConfigError("rules must be an array")

    rules = []
    for index, raw_rule in enumerate(raw_rules):
        location = f"rules[{index}]"
        if not isinstance(raw_rule, dict):
            raise ConfigError(f"{location} must be an object")
        unknown_rule_keys = set(raw_rule) - {"name", "scenario", "match", "env"}
        if unknown_rule_keys:
            raise ConfigError(
                f"{location} has unknown keys: {', '.join(sorted(unknown_rule_keys))}"
            )

        selectors = [name for name in ("scenario", "match") if name in raw_rule]
        if len(selectors) != 1:
            raise ConfigError(
                f'{location} must define exactly one of "scenario" or "match"'
            )
        selector = selectors[0]
        selector_value = raw_rule[selector]
        if not isinstance(selector_value, str) or not selector_value:
            raise ConfigError(f"{location}.{selector} must be a non-empty string")
        if selector == "match":
            try:
                re.compile(selector_value)
            except re.error as error:
                raise ConfigError(f"{location}.match is invalid: {error}") from error

        name = raw_rule.get("name", f"rule {index + 1}")
        if not isinstance(name, str) or not name:
            raise ConfigError(f"{location}.name must be a non-empty string")
        rules.append(
            Rule(
                name=name,
                selector=selector,
                value=selector_value,
                environment=validate_environment(
                    raw_rule.get("env", {}),
                    f"{location}.env",
                ),
            )
        )

    return Config(environment=environment, rules=rules)


def resolve_path(value: str, base: Path | None = None) -> Path:
    path = Path(value)
    return (
        path.resolve() if path.is_absolute() else ((base or ROOT_DIR) / path).resolve()
    )


def require_file(path: Path, description: str) -> None:
    if not path.is_file():
        raise ScenarioExecutionError(f"{description} not found: {path}")


def repo_relative(path: Path) -> str:
    return os.path.relpath(path, ROOT_DIR)


def read_logic_file(scenario_file: Path) -> str:
    try:
        root = ET.parse(scenario_file).getroot()
    except (OSError, ET.ParseError) as error:
        raise ScenarioExecutionError(
            f"Cannot read OpenSCENARIO file {scenario_file}: {error}"
        ) from error

    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "LogicFile":
            logic_file = element.attrib.get("filepath", "")
            if logic_file:
                return logic_file
            break
    raise ScenarioExecutionError(
        f"No RoadNetwork/LogicFile filepath found in {scenario_file}"
    )


def log_environment_names(label: str, environment: dict[str, str]) -> None:
    names = " ".join(sorted(environment))
    LOGGER.info("%s: %s", label, names or "none")


def format_duration(seconds: float) -> str:
    if seconds < 10:
        return f"{seconds:.1f}s"
    total_seconds = int(seconds)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02}:{minutes:02}:{seconds:02}"
    return f"{minutes:02}:{seconds:02}"


def run_command(
    command: list[str] | tuple[str, ...],
    environment: dict[str, str],
    *,
    check: bool = True,
    stdout=None,
    stderr=None,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command,
            cwd=ROOT_DIR,
            env=environment,
            check=check,
            stdout=stdout,
            stderr=stderr,
        )
    except FileNotFoundError as error:
        if not check:
            return subprocess.CompletedProcess(command, 127)
        raise ScenarioExecutionError(f"Command not found: {command[0]}") from error


def validate_scenario_compatibility(
    scenario_file: Path,
    environment: dict[str, str],
) -> None:
    require_file(SCENARIO_CHECKER, "scenario checker")
    command = [
        sys.executable,
        str(SCENARIO_CHECKER),
        "validate",
        str(scenario_file),
        "--repo-root",
        str(ROOT_DIR),
        "--json",
    ]
    if environment.get("MAP"):
        command.extend(["--expected-map", environment["MAP"]])
    if environment.get("CUSTOM_OPENDRIVE"):
        opendrive_path = str(resolve_path(environment["CUSTOM_OPENDRIVE"]))
        command.extend(
            [
                "--opendrive",
                opendrive_path,
                "--expected-opendrive",
                opendrive_path,
            ]
        )
    if environment.get("CUSTOM_LANELET"):
        lanelet_path = str(resolve_path(environment["CUSTOM_LANELET"]))
        command.extend(
            [
                "--lanelet",
                lanelet_path,
                "--expected-lanelet",
                lanelet_path,
            ]
        )

    completed = run_command(
        command,
        os.environ.copy(),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as error:
        raise ScenarioExecutionError(
            f"Scenario checker returned invalid output: {completed.stderr.strip()}"
        ) from error
    for warning in payload.get("warnings", []):
        LOGGER.warning("Scenario validation: %s", warning)
    if completed.returncode or not payload.get("valid"):
        errors = payload.get("errors", [])
        raise ScenarioExecutionError(
            "Scenario validation failed: " + "; ".join(errors or ["unknown error"])
        )


class ScenarioExecutor:
    def __init__(self, args: argparse.Namespace, config_path: Path, config: Config):
        self.args = args
        self.config_path = config_path
        self.config = config
        self.record_bag = args.record_bag or args.op_conversion
        self.base_environment = os.environ.copy()
        self.base_environment.update(config.environment)
        self.pulled_profile_sets: set[frozenset[str]] = set()

    def resolve_profiles(
        self, environment: dict[str, str], scenario_label: str
    ) -> list[str]:
        profiles = [
            profile.strip()
            for profile in environment.get("COMPOSE_PROFILES", "").split(",")
            if profile.strip() and profile.strip() != "bag-recording"
        ]
        if self.record_bag:
            profiles.append("bag-recording")
        environment["COMPOSE_PROFILES"] = ",".join(profiles)

        missing_profiles = [
            profile
            for profile in ("carla", "automated-testing")
            if profile not in profiles
        ]
        if missing_profiles:
            raise ScenarioExecutionError(
                f'{scenario_label} COMPOSE_PROFILES="{environment["COMPOSE_PROFILES"]}" '
                f"must include {', '.join(missing_profiles)}"
            )
        if "sumo" in profiles:
            raise ScenarioExecutionError(
                f'{scenario_label} COMPOSE_PROFILES="{environment["COMPOSE_PROFILES"]}" '
                "must not include sumo together with carla"
            )

        traffic_profiles = [
            profile for profile in ("no-traffic", "traffic") if profile in profiles
        ]
        if len(traffic_profiles) != 1:
            raise ScenarioExecutionError(
                f'{scenario_label} COMPOSE_PROFILES="{environment["COMPOSE_PROFILES"]}" '
                "must include exactly one of no-traffic or traffic"
            )
        if "manual-testing" in profiles:
            raise ScenarioExecutionError(
                f'{scenario_label} COMPOSE_PROFILES="{environment["COMPOSE_PROFILES"]}" '
                "must not include manual-testing together with automated-testing"
            )
        return profiles

    def compose(
        self,
        arguments: list[str],
        environment: dict[str, str],
        *,
        check: bool = True,
        stdout=None,
        stderr=None,
    ) -> subprocess.CompletedProcess:
        return run_command(
            [*COMPOSE_COMMAND, *arguments],
            environment,
            check=check,
            stdout=stdout,
            stderr=stderr,
        )

    def cleanup_containers(
        self, environment: dict[str, str], scenario_label: str
    ) -> None:
        LOGGER.info("%s Cleaning up containers", scenario_label)
        self.compose(
            ["kill"],
            environment,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.compose(
            ["down"],
            environment,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def resolve_map(
        self, scenario_file: Path, environment: dict[str, str]
    ) -> MapSelection:
        configured_map = environment.get("MAP", "")
        configured_opendrive = environment.get("CUSTOM_OPENDRIVE", "")
        logic_file = read_logic_file(scenario_file)
        if configured_map and configured_opendrive:
            raise ScenarioExecutionError(
                f"MAP and CUSTOM_OPENDRIVE cannot both be non-empty for {scenario_file}."
            )

        if configured_opendrive:
            opendrive = resolve_path(configured_opendrive)
            require_file(opendrive, "CUSTOM_OPENDRIVE file")
            logic_reference = Path(logic_file.replace("\\", "/"))
            if opendrive.parent != scenario_file.parent.resolve():
                raise ScenarioExecutionError(
                    "CUSTOM_OPENDRIVE must be in the same directory as the "
                    f"OpenSCENARIO file: {opendrive}"
                )
            if (
                logic_reference.is_absolute()
                or logic_reference.parent != Path(".")
                or logic_reference.name != opendrive.name
            ):
                raise ScenarioExecutionError(
                    "LogicFile filepath must reference CUSTOM_OPENDRIVE in the same "
                    "directory as the OpenSCENARIO file; expected only the filename "
                    f"{opendrive.name!r}, got {logic_file!r}"
                )
            environment["CUSTOM_OPENDRIVE"] = repo_relative(opendrive)
            environment["MAP"] = ""
            return MapSelection(
                description=f"configured custom OpenDRIVE: {environment['CUSTOM_OPENDRIVE']}",
                scenario_opendrive=opendrive,
                conversion_opendrive=opendrive,
            )

        if configured_map:
            environment["MAP"] = configured_map
            environment["CUSTOM_OPENDRIVE"] = ""
            description = f"configured prebuilt CARLA map: {configured_map}"
        else:
            if logic_file.lower().endswith(".xodr"):
                logic_reference = Path(logic_file.replace("\\", "/"))
                if logic_reference.is_absolute() or logic_reference.parent != Path("."):
                    raise ScenarioExecutionError(
                        "LogicFile must reference an OpenDRIVE filename in the same "
                        f"directory as the OpenSCENARIO file, got {logic_file!r}"
                    )
                opendrive = resolve_path(logic_file, scenario_file.parent)
                require_file(opendrive, "OpenDRIVE file referenced by the scenario")
                environment["CUSTOM_OPENDRIVE"] = repo_relative(opendrive)
                environment["MAP"] = ""
                return MapSelection(
                    description=f"custom OpenDRIVE: {environment['CUSTOM_OPENDRIVE']}",
                    scenario_opendrive=opendrive,
                    conversion_opendrive=opendrive,
                )
            environment["CUSTOM_OPENDRIVE"] = ""
            environment["MAP"] = logic_file
            description = f"prebuilt CARLA map from LogicFile: {logic_file}"

        conversion_opendrive = None
        if self.args.op_conversion:
            configured_conversion_map = environment.get("OP_OPENDRIVE", "")
            if not configured_conversion_map:
                raise ScenarioExecutionError(
                    f"Omega-Prime conversion for CARLA map '{environment['MAP']}' "
                    "requires OP_OPENDRIVE."
                )
            conversion_opendrive = resolve_path(configured_conversion_map)
            require_file(
                conversion_opendrive, "OpenDRIVE file for Omega-Prime conversion"
            )
        return MapSelection(description, conversion_opendrive=conversion_opendrive)

    def resolve_lanelet(
        self,
        scenario_file: Path,
        map_selection: MapSelection,
        environment: dict[str, str],
    ) -> str:
        configured_lanelet = environment.get("CUSTOM_LANELET", "")
        scenario_lanelet = Path(f"{str(scenario_file)[:-5]}.osm")
        opendrive_lanelet = (
            map_selection.scenario_opendrive.with_suffix(".osm")
            if map_selection.scenario_opendrive
            else None
        )

        lanelet_file = None
        if configured_lanelet:
            lanelet_file = resolve_path(configured_lanelet)
            require_file(lanelet_file, "CUSTOM_LANELET file")
        elif scenario_lanelet.is_file():
            lanelet_file = scenario_lanelet.resolve()
        elif opendrive_lanelet and opendrive_lanelet.is_file():
            lanelet_file = opendrive_lanelet.resolve()
        elif map_selection.scenario_opendrive:
            raise ScenarioExecutionError(
                "Custom OpenDRIVE scenario requires a matching CUSTOM_LANELET file."
            )

        if lanelet_file:
            if lanelet_file.parent != scenario_file.parent.resolve():
                raise ScenarioExecutionError(
                    "CUSTOM_LANELET must be in the same directory as the "
                    f"OpenSCENARIO file: {lanelet_file}"
                )
            environment["CUSTOM_LANELET"] = repo_relative(lanelet_file)
            environment["LANELET_RELOAD"] = "false"
            return (
                f"custom Lanelet2: {environment['CUSTOM_LANELET']} "
                "(LANELET_RELOAD=false)"
            )

        environment["CUSTOM_LANELET"] = ""
        environment["LANELET_RELOAD"] = "true"
        return "automatic map selection (LANELET_RELOAD=true)"

    def prepare_output(self, scenario_name: str, environment: dict[str, str]) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_root = ROOT_DIR / "examples/scenario-execution/scenario-data"
        directory_name = f"{scenario_name}__{timestamp}"
        data_directory = output_root / directory_name
        collision_index = 2
        while True:
            try:
                data_directory.mkdir(parents=True)
                break
            except FileExistsError:
                data_directory = output_root / f"{directory_name}-{collision_index}"
                collision_index += 1

        data_directory = data_directory.relative_to(ROOT_DIR)
        environment["DATA_DIRECTORY"] = data_directory.as_posix()
        environment["BAG_DIRECTORY"] = ""
        environment["OP_DIRECTORY"] = (data_directory / "op").as_posix()
        if self.record_bag:
            environment["BAG_DIRECTORY"] = (data_directory / "bags").as_posix()
            (ROOT_DIR / environment["BAG_DIRECTORY"]).mkdir()
        if self.args.op_conversion:
            (ROOT_DIR / environment["OP_DIRECTORY"]).mkdir(parents=True, exist_ok=True)

    def save_environment(self, environment: dict[str, str], names: set[str]) -> Path:
        relative_path = Path(environment["DATA_DIRECTORY"]) / "environment.json"
        snapshot = {name: environment.get(name, "") for name in sorted(names)}
        with (ROOT_DIR / relative_path).open("w", encoding="utf-8") as snapshot_file:
            json.dump(snapshot, snapshot_file, indent=2, sort_keys=True)
            snapshot_file.write("\n")
        return relative_path

    def run_compose_scenario(
        self,
        environment: dict[str, str],
        scenario_label: str,
        compose_log: Path,
    ) -> int:
        command = [
            *COMPOSE_COMMAND,
            "up",
            "--abort-on-container-exit",
            "--exit-code-from",
            "carla-scenario-runner-ros-automated-testing",
        ]
        started_at = time.monotonic()
        next_progress_at = PROGRESS_INTERVAL_SECONDS
        LOGGER.info(
            "%s Started | compose_log=%s",
            scenario_label,
            repo_relative(compose_log),
        )

        try:
            with compose_log.open("w", encoding="utf-8") as log_file:
                process = subprocess.Popen(
                    command,
                    cwd=ROOT_DIR,
                    env=environment,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
                try:
                    while True:
                        try:
                            return_code = process.wait(timeout=1)
                            break
                        except subprocess.TimeoutExpired:
                            elapsed = time.monotonic() - started_at
                            if elapsed >= next_progress_at:
                                LOGGER.info(
                                    "%s Running | elapsed=%s",
                                    scenario_label,
                                    format_duration(elapsed),
                                )
                                while next_progress_at <= elapsed:
                                    next_progress_at += PROGRESS_INTERVAL_SECONDS
                except BaseException:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    raise
        except FileNotFoundError as error:
            raise ScenarioExecutionError(
                f"Command not found: {command[0]}"
            ) from error

        duration = format_duration(time.monotonic() - started_at)
        if return_code == 0:
            LOGGER.info(
                "%s Completed | duration=%s | exit_code=%s",
                scenario_label,
                duration,
                return_code,
            )
        else:
            LOGGER.error(
                "%s Failed | duration=%s | exit_code=%s",
                scenario_label,
                duration,
                return_code,
            )
        return return_code

    def save_scenario_logs(
        self, environment: dict[str, str], scenario_label: str
    ) -> int:
        log_path = ROOT_DIR / environment["DATA_DIRECTORY"] / "scenario.log"
        with log_path.open("w", encoding="utf-8") as log_file:
            result = self.compose(
                ["logs", "carla-scenario-runner-ros-automated-testing"],
                environment,
                check=False,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        if result.returncode:
            LOGGER.warning(
                "%s Could not collect scenario logs | exit_code=%s",
                scenario_label,
                result.returncode,
            )
        return result.returncode

    def convert_to_omega_prime(
        self,
        environment: dict[str, str],
        opendrive_file: Path,
    ) -> None:
        bag_directory = ROOT_DIR / environment["BAG_DIRECTORY"]
        LOGGER.info("Checking bag files for %s", environment["SCENARIO_FILE"])
        if not any(path.is_file() for path in bag_directory.rglob("metadata.yaml")):
            LOGGER.info(
                "No bag files found in %s; skipping Omega-Prime conversion",
                environment["BAG_DIRECTORY"],
            )
            return

        LOGGER.info("Converting %s to Omega-Prime", environment["SCENARIO_FILE"])
        run_command(
            [
                "docker",
                "run",
                "--rm",
                "-it",
                "-e",
                "EGO_DATA_TOPIC=/localization/ego_state_estimation/ego_data",
                "-e",
                "OBJECT_LIST_TOPIC=/understanding/lanelet2_object_list_prediction/object_list",
                "-v",
                f"{bag_directory}:/input",
                "-v",
                f"{ROOT_DIR / environment['OP_DIRECTORY']}:/output",
                "-v",
                f"{opendrive_file}:/map/map.xodr",
                "ghcr.io/ika-rwth-aachen/omega-prime-ros:v1.1.1",
            ],
            environment,
        )

    def run_scenario(
        self, scenario_file: Path, scenario_index: int, scenario_count: int
    ) -> None:
        scenario_relative = repo_relative(scenario_file)
        overrides, matched_rules = self.config.resolve(scenario_relative)
        environment = self.base_environment.copy()
        environment.update(overrides)
        environment["SCENARIO_FILE"] = scenario_relative
        environment["SCENARIO_NAME"] = Path(
            scenario_relative.removesuffix(".xosc")
        ).name
        snapshot_names = (
            set(self.config.environment) | set(overrides) | DERIVED_ENV_NAMES
        )
        scenario_label = (
            f"[{scenario_index}/{scenario_count}] {environment['SCENARIO_NAME']}"
        )
        profiles = self.resolve_profiles(environment, scenario_label)

        LOGGER.info("%s Preparing | file=%s", scenario_label, scenario_relative)
        LOGGER.info(
            "%s Matching config rules: %s",
            scenario_label,
            ", ".join(matched_rules) or "none",
        )
        log_environment_names(f"{scenario_label} Config overrides", overrides)
        LOGGER.info("%s Compose profiles: %s", scenario_label, ",".join(profiles))

        profile_set = frozenset(profiles)
        if self.args.pull_images and profile_set not in self.pulled_profile_sets:
            LOGGER.info("%s Updating Compose images", scenario_label)
            self.compose(["pull"], environment)
            self.pulled_profile_sets.add(profile_set)

        try:
            map_selection = self.resolve_map(scenario_file, environment)
            lanelet_description = self.resolve_lanelet(
                scenario_file, map_selection, environment
            )
            validate_scenario_compatibility(scenario_file, environment)
            self.prepare_output(environment["SCENARIO_NAME"], environment)
            environment_file = self.save_environment(environment, snapshot_names)
            compose_log = ROOT_DIR / environment["DATA_DIRECTORY"] / "compose.log"

            LOGGER.info("%s Map: %s", scenario_label, map_selection.description)
            LOGGER.info("%s Lanelet2: %s", scenario_label, lanelet_description)
            LOGGER.info(
                "%s Bag recording: %s",
                scenario_label,
                "enabled" if self.record_bag else "disabled",
            )
            if self.args.op_conversion:
                LOGGER.info(
                    "%s Omega-Prime OpenDRIVE: %s",
                    scenario_label,
                    map_selection.conversion_opendrive,
                )
            LOGGER.info(
                "%s Artifacts: %s",
                scenario_label,
                environment["DATA_DIRECTORY"],
            )
            LOGGER.info(
                "%s Environment: %s", scenario_label, environment_file.as_posix()
            )

            return_code = self.run_compose_scenario(
                environment, scenario_label, compose_log
            )
            log_return_code = self.save_scenario_logs(environment, scenario_label)
            if return_code:
                raise subprocess.CalledProcessError(return_code, COMPOSE_COMMAND)
            if log_return_code:
                raise subprocess.CalledProcessError(log_return_code, COMPOSE_COMMAND)
            if self.args.op_conversion:
                assert map_selection.conversion_opendrive is not None
                self.convert_to_omega_prime(
                    environment, map_selection.conversion_opendrive
                )
        finally:
            self.cleanup_containers(environment, scenario_label)

    def discover_scenarios(self, folder: Path) -> list[Path]:
        return sorted(
            path
            for path in folder.rglob("*")
            if path.is_file()
            and "catalogs" not in path.parts
            and fnmatch.fnmatchcase(path.name, self.args.scenario_filter)
        )

    def run(self) -> int:
        log_environment_names("Base config environment", self.config.environment)
        base_profiles = re.sub(
            r"\s", "", self.base_environment.get("COMPOSE_PROFILES", "")
        )

        scenario_folder = resolve_path(self.args.scenario_folder)
        if not scenario_folder.is_dir():
            raise ScenarioExecutionError(
                f"Scenario folder not found: {scenario_folder}"
            )

        LOGGER.info(
            "Execution config | config=%s | base_profiles=%s | folder=%s | "
            "filter=%s | delay=%ss | bag_recording=%s | .env=disabled",
            self.config_path,
            base_profiles,
            scenario_folder,
            self.args.scenario_filter,
            self.args.wait_seconds,
            "enabled" if self.record_bag else "disabled",
        )
        LOGGER.info(
            "Precedence | environment=scenario rules > config env > host env | "
            "map=MAP/CUSTOM_OPENDRIVE > LogicFile"
        )

        LOGGER.info("Searching for matching OpenSCENARIO files")
        scenarios = self.discover_scenarios(scenario_folder)
        if not scenarios:
            LOGGER.warning("No matching scenarios found")
            return 1
        LOGGER.info("Discovered %s scenarios", len(scenarios))

        failures: list[tuple[Path, str]] = []
        xhost_enabled = False
        try:
            run_command(
                ["xhost", "+local:"],
                self.base_environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            xhost_enabled = True
            for index, scenario_file in enumerate(scenarios, start=1):
                try:
                    self.run_scenario(scenario_file, index, len(scenarios))
                except (subprocess.CalledProcessError, ScenarioExecutionError, OSError) as error:
                    reason = str(error)
                    failures.append((scenario_file, reason))
                    LOGGER.error(
                        "[%s/%s] %s Failed | %s",
                        index, len(scenarios), repo_relative(scenario_file), reason,
                    )
                if index < len(scenarios) and self.args.wait_seconds != "0":
                    LOGGER.info(
                        "Waiting %s seconds before the next scenario",
                        self.args.wait_seconds,
                    )
                    time.sleep(float(self.args.wait_seconds))
        finally:
            if xhost_enabled:
                run_command(
                    ["xhost", "-local:"],
                    self.base_environment,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        LOGGER.info(
            "Execution summary | total=%s | succeeded=%s | failed=%s",
            len(scenarios), len(scenarios) - len(failures), len(failures),
        )
        for scenario_file, reason in failures:
            LOGGER.error("Failed scenario: %s | %s", repo_relative(scenario_file), reason)
        return 1 if failures else 0


def parse_delay(value: str) -> str:
    if not DELAY_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError(
            f"must be a non-negative number, got {value!r}"
        )
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Run matching OpenSCENARIO files sequentially with the CARLA "
            "automated-testing profile. The repository .env file is disabled; "
            "the required JSON config provides the simulation environment."
        ),
        epilog="""
Configuration:
  COMPOSE_PROFILES is required in the top-level JSON env and may be overridden
  by scenario rules. Each effective value must include carla,
  automated-testing, and exactly one of no-traffic or traffic. It must not
  include sumo or manual-testing. Environment precedence is:
  scenario rules > config env > host environment.

  MAP selects a prebuilt CARLA map. CUSTOM_OPENDRIVE selects an .xodr file.
  They override RoadNetwork/LogicFile and must not both be non-empty.
  CUSTOM_LANELET overrides automatic .osm detection. LANELET_RELOAD is
  derived from the resolved Lanelet2 source. Use -b to record a ROS bag. The -o
  option also enables bag recording and requires OP_OPENDRIVE for prebuilt
  CARLA maps.

  SCENARIO_FILE, SCENARIO_NAME, LANELET_RELOAD, DATA_DIRECTORY,
  BAG_DIRECTORY, and OP_DIRECTORY are script-managed and forbidden in JSON.
  SCENARIO_FILTER and the delay (-t) are runtime arguments, not environment
  variables.

Scenario rules:
  The top-level env applies to every scenario. Each ordered rule selects an
  exact repo-relative path with "scenario" or a group with the Python regex
  "match". Later matching rules win; an empty string clears a value.
  Each run uses <scenario>__<YYYYMMDDTHHMMSSZ> as its output directory.
  Effective values are saved as environment.json. When bag recording is
  enabled, bags are written below the run's bags directory.
  Compose output is written to compose.log; the console reports progress every
  10 seconds and the final duration and exit code.

Examples:
  %(prog)s -c examples/scenario-execution/example.json
  %(prog)s -b -c examples/scenario-execution/example.json
  %(prog)s -c CONFIG -d carla-simulation/scenarios \\
    -f '*synthetic*.xosc'
  %(prog)s -o -t 0 -c CONFIG
""",
    )
    parser.add_argument(
        "-c", "--config", required=True, help="complete JSON environment config"
    )
    parser.add_argument(
        "-b",
        "--record-bag",
        action="store_true",
        help="record a ROS bag for each scenario",
    )
    parser.add_argument(
        "-o",
        "--op-conversion",
        action="store_true",
        help="record ROS bags and convert them to Omega-Prime",
    )
    parser.add_argument(
        "-p",
        "--pull-images",
        action="store_true",
        help="pull Compose images before running",
    )
    parser.add_argument(
        "-t",
        "--wait-seconds",
        type=parse_delay,
        default=DEFAULT_WAIT_SECONDS,
        metavar="SEC",
        help=f"delay between scenarios (default: {DEFAULT_WAIT_SECONDS})",
    )
    parser.add_argument(
        "-d",
        "--scenario-folder",
        default=DEFAULT_SCENARIO_FOLDER,
        metavar="SCENARIO_FOLDER_PATH",
        help=f"scenario root (default: {DEFAULT_SCENARIO_FOLDER})",
    )
    parser.add_argument(
        "-f",
        "--scenario-filter",
        default=DEFAULT_SCENARIO_FILTER,
        metavar="SCENARIO_FILTER",
        help=f"filename glob (default: {DEFAULT_SCENARIO_FILTER!r})",
    )
    return parser.parse_args()


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    configure_logging()
    args = parse_args()
    config_path = resolve_path(args.config)
    config = load_config(config_path)
    return ScenarioExecutor(args, config_path, config).run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConfigError as error:
        LOGGER.error("Invalid scenario config: %s", error)
        raise SystemExit(2) from error
    except ScenarioExecutionError as error:
        LOGGER.error("%s", error)
        raise SystemExit(1) from error
    except OSError as error:
        LOGGER.error("%s", error)
        raise SystemExit(1) from error
    except subprocess.CalledProcessError as error:
        LOGGER.error(
            "Command failed | exit_code=%s | command=%s",
            error.returncode,
            error.cmd,
        )
        raise SystemExit(error.returncode or 1) from error
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted")
        raise SystemExit(130) from None
