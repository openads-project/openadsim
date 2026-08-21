#!/usr/bin/env python3

"""Validate and import OpenSCENARIO bundles for OpenADSim/CARLA."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


PREBUILT_MAPS = {
    "aldenhoven": "aldenhoven",
    "campus": "campus",
    "town10hd_opt": "Town10HD_Opt",
}
CRITERIA = (
    "criteria_RunningStopTest",
    "criteria_RunningRedLightTest",
    "criteria_CollisionTest",
    "criteria_WrongLaneTest",
    "criteria_OnSidewalkTest",
    "criteria_DrivenDistanceTest",
)
ROS_CONTROLLER_MODULE = "ros_vehicle_control_route_action.py"


class ScenarioCheckerError(RuntimeError):
    """Raised for a user-facing validation or import error."""


@dataclass(frozen=True)
class MapResolution:
    kind: str
    logic_file: str
    map_name: str = ""
    opendrive: Optional[Path] = None
    lanelet: Optional[Path] = None


@dataclass
class ScenarioReport:
    scenario: Path
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    planned_changes: list[str] = field(default_factory=list)
    map_resolution: Optional[MapResolution] = None
    ego_actor: str = ""

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        map_data: dict[str, object] = {}
        if self.map_resolution:
            map_data = {
                "type": self.map_resolution.kind,
                "logic_file": self.map_resolution.logic_file,
                "map_name": self.map_resolution.map_name,
                "opendrive": str(self.map_resolution.opendrive or ""),
                "lanelet": str(self.map_resolution.lanelet or ""),
            }
        return {
            "valid": self.is_valid,
            "scenario": str(self.scenario),
            "ego_actor": self.ego_actor,
            "map": map_data,
            "errors": self.errors,
            "warnings": self.warnings,
            "planned_changes": self.planned_changes,
        }


@dataclass(frozen=True)
class ImportResult:
    directory: Path
    scenario_file: Path
    map_resolution: MapResolution
    actions: tuple[str, ...]
    warnings: tuple[str, ...]
    repo_root: Path

    def _relative(self, path: Optional[Path]) -> str:
        if path is None:
            return ""
        try:
            return path.resolve().relative_to(self.repo_root.resolve()).as_posix()
        except ValueError:
            return str(path.resolve())

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": True,
            "directory": self._relative(self.directory),
            "scenario_file": self._relative(self.scenario_file),
            "map": {
                "type": self.map_resolution.kind,
                "map_name": self.map_resolution.map_name,
                "opendrive": self._relative(self.map_resolution.opendrive),
                "lanelet": self._relative(self.map_resolution.lanelet),
            },
            "actions": list(self.actions),
            "warnings": list(self.warnings),
        }


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(element: ET.Element) -> str:
    if element.tag.startswith("{"):
        return element.tag.split("}", 1)[0] + "}"
    return ""


def _child(parent: Optional[ET.Element], name: str) -> Optional[ET.Element]:
    if parent is None:
        return None
    return next(
        (element for element in list(parent) if _local_name(element.tag) == name),
        None,
    )


def _children(parent: Optional[ET.Element], name: str) -> list[ET.Element]:
    if parent is None:
        return []
    return [element for element in list(parent) if _local_name(element.tag) == name]


def _path(parent: Optional[ET.Element], *names: str) -> Optional[ET.Element]:
    element = parent
    for name in names:
        element = _child(element, name)
        if element is None:
            return None
    return element


def _iter(parent: Optional[ET.Element], name: str):
    if parent is None:
        return
    for element in parent.iter():
        if _local_name(element.tag) == name:
            yield element


def _subelement(
    parent: ET.Element,
    name: str,
    attributes: Optional[dict[str, str]] = None,
) -> ET.Element:
    return ET.SubElement(
        parent,
        f"{_namespace(parent)}{name}",
        attributes or {},
    )


def _normalize_map_reference(value: str) -> str:
    normalized = value.strip().replace("\\", "/").rstrip("/")
    filename = normalized.rsplit("/", 1)[-1]
    if filename.lower().endswith(".xodr"):
        filename = filename[:-5]
    return filename.lower()


def _canonical_prebuilt_map(value: str) -> str:
    return PREBUILT_MAPS.get(_normalize_map_reference(value), "")


def _version_conversion_issues(root: ET.Element) -> list[str]:
    issues: list[str] = []
    header = _child(root, "FileHeader")
    if header is None:
        return ["FileHeader is missing"]

    major = header.get("revMajor", "")
    minor = header.get("revMinor", "")
    if major != "1":
        return [f"Unsupported OpenSCENARIO major version {major or '<missing>'}; expected 1"]
    if minor != "1":
        issues.append(f"Convert OpenSCENARIO {major}.{minor or '<missing>'} to 1.1")
    if _child(root, "VariableDeclarations") is not None:
        issues.append("Remove OpenSCENARIO 1.2 VariableDeclarations")
    if any(event.get("priority") == "override" for event in _iter(root, "Event")):
        issues.append("Convert Event priority 'override' to 'overwrite'")
    if any(
        condition.get("relativeDistanceType") == "cartesianDistance"
        for condition in _iter(root, "RelativeDistanceCondition")
    ):
        issues.append("Convert cartesianDistance to euclidianDistance")
    return issues


def _convert_to_osc_1_1(root: ET.Element) -> list[str]:
    actions: list[str] = []
    header = _child(root, "FileHeader")
    if header is None:
        raise ScenarioCheckerError("FileHeader is missing")

    major = header.get("revMajor", "")
    minor = header.get("revMinor", "")
    if major != "1":
        raise ScenarioCheckerError(
            f"Unsupported OpenSCENARIO major version {major or '<missing>'}; expected 1"
        )
    if minor != "1":
        header.set("revMinor", "1")
        actions.append(f"Converted OpenSCENARIO {major}.{minor or '<missing>'} to 1.1")

    variable_declarations = _child(root, "VariableDeclarations")
    if variable_declarations is not None:
        root.remove(variable_declarations)
        actions.append("Removed OpenSCENARIO 1.2 VariableDeclarations")

    priorities_converted = 0
    for event in _iter(root, "Event"):
        if event.get("priority") == "override":
            event.set("priority", "overwrite")
            priorities_converted += 1
    if priorities_converted:
        actions.append(
            f"Converted {priorities_converted} Event priority value(s) from override to overwrite"
        )

    distances_converted = 0
    for condition in _iter(root, "RelativeDistanceCondition"):
        if condition.get("relativeDistanceType") == "cartesianDistance":
            condition.set("relativeDistanceType", "euclidianDistance")
            distances_converted += 1
    if distances_converted:
        actions.append(
            f"Converted {distances_converted} cartesianDistance value(s) to euclidianDistance"
        )
    return actions


def _parse_xml(path: Path, expected_root: str) -> ET.ElementTree:
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as error:
        raise ScenarioCheckerError(f"Cannot parse {path}: {error}") from error
    if _local_name(tree.getroot().tag) != expected_root:
        raise ScenarioCheckerError(
            f"{path} must have <{expected_root}> as its root element"
        )
    return tree


def _repo_root_from(path: Path) -> Path:
    for candidate in (path.resolve(), *path.resolve().parents):
        if (candidate / "carla-simulation").is_dir() and (candidate / "utils").is_dir():
            return candidate
    return Path.cwd().resolve()


def _resolve_map(
    scenario_file: Path,
    root: ET.Element,
    *,
    opendrive: Optional[Path],
    lanelet: Optional[Path],
) -> MapResolution:
    logic_element = _path(root, "RoadNetwork", "LogicFile")
    if logic_element is None or not logic_element.get("filepath", "").strip():
        raise ScenarioCheckerError("RoadNetwork/LogicFile filepath is missing")
    logic_file = logic_element.get("filepath", "").strip()

    selected_opendrive = opendrive.resolve() if opendrive else None
    if selected_opendrive:
        if selected_opendrive.suffix.lower() != ".xodr":
            raise ScenarioCheckerError("Uploaded OpenDRIVE map must end in .xodr")
        _parse_xml(selected_opendrive, "OpenDRIVE")
    elif logic_file.lower().endswith(".xodr"):
        raise ScenarioCheckerError(
            "OpenDRIVE map referenced by LogicFile must be explicitly uploaded: "
            f"{logic_file}"
        )

    if selected_opendrive:
        selected_lanelet = lanelet.resolve() if lanelet else None
        if selected_lanelet is None or not selected_lanelet.is_file():
            raise ScenarioCheckerError(
                "Custom OpenDRIVE requires an explicitly uploaded Lanelet2 .osm file"
            )
        if selected_lanelet.suffix.lower() != ".osm":
            raise ScenarioCheckerError("Lanelet2 map must end in .osm")
        _parse_xml(selected_lanelet, "osm")
        return MapResolution(
            kind="custom",
            logic_file=logic_file,
            opendrive=selected_opendrive,
            lanelet=selected_lanelet,
        )

    if lanelet:
        raise ScenarioCheckerError(
            "A Lanelet2 upload without a custom OpenDRIVE map is not supported by the importer"
        )
    map_name = _canonical_prebuilt_map(logic_file)
    if not map_name:
        supported = ", ".join(PREBUILT_MAPS.values())
        raise ScenarioCheckerError(
            f"LogicFile references unknown prebuilt map {logic_file!r}; supported: {supported}"
        )
    return MapResolution(kind="prebuilt", logic_file=logic_file, map_name=map_name)


def _scenario_objects(root: ET.Element) -> list[ET.Element]:
    return _children(_child(root, "Entities"), "ScenarioObject")


def _actor_payload(actor: ET.Element) -> Optional[ET.Element]:
    return next(
        (
            element
            for element in list(actor)
            if _local_name(element.tag) in {"Vehicle", "Pedestrian"}
        ),
        None,
    )


def _actor_type(actor: ET.Element) -> str:
    payload = _actor_payload(actor)
    properties = _child(payload, "Properties")
    for prop in _children(properties, "Property"):
        if prop.get("name") == "type":
            return prop.get("value", "")
    return ""


def _select_ego(
    root: ET.Element,
    requested_ego: str,
    *,
    allow_repair: bool,
) -> tuple[Optional[ET.Element], list[str]]:
    actors = _scenario_objects(root)
    ego_actors = [
        actor
        for actor in actors
        if actor.get("name") == "ego_vehicle" or _actor_type(actor) == "ego_vehicle"
    ]
    unique_ego = list({id(actor): actor for actor in ego_actors}.values())
    if len(unique_ego) > 1:
        return None, ["Multiple actors are marked as ego_vehicle"]
    if unique_ego:
        if requested_ego and unique_ego[0].get("name") != requested_ego:
            return None, [
                f"Requested ego actor {requested_ego!r} conflicts with existing ego_vehicle"
            ]
        return unique_ego[0], []

    vehicle_actors = []
    for actor in actors:
        payload = _actor_payload(actor)
        if payload is not None and _local_name(payload.tag) == "Vehicle":
            vehicle_actors.append(actor)
    if requested_ego:
        requested = next(
            (actor for actor in vehicle_actors if actor.get("name") == requested_ego),
            None,
        )
        if requested is None:
            return None, [f"Requested ego actor does not exist or is not a Vehicle: {requested_ego}"]
        return requested, []
    if not vehicle_actors:
        return None, ["Scenario contains no inline Vehicle actor that can become ego_vehicle"]
    if not allow_repair:
        return None, [
            "Scenario has no ego_vehicle; import it to select and normalize an ego actor"
        ]
    return vehicle_actors[0], []


def _ego_private(root: ET.Element, ego_name: str) -> Optional[ET.Element]:
    actions = _path(root, "Storyboard", "Init", "Actions")
    for private in _children(actions, "Private"):
        if private.get("entityRef") == ego_name:
            return private
    return None


def _ego_controller_actions(root: ET.Element, ego_name: str) -> list[ET.Element]:
    private = _ego_private(root, ego_name)
    return [
        action
        for action in _children(private, "PrivateAction")
        if _child(action, "ControllerAction") is not None
    ]


def _has_normalized_ego_controller(root: ET.Element, ego_name: str) -> bool:
    controller_actions = _ego_controller_actions(root, ego_name)
    if len(controller_actions) != 1:
        return False
    assign_actions = list(_iter(controller_actions[0], "AssignControllerAction"))
    if len(assign_actions) != 1:
        return False
    return any(
        prop.get("name") == "module" and prop.get("value") == ROS_CONTROLLER_MODULE
        for prop in _iter(assign_actions[0], "Property")
    )


def _object_controllers(root: ET.Element) -> list[ET.Element]:
    return [
        controller
        for actor in _scenario_objects(root)
        for controller in _children(actor, "ObjectController")
    ]


def _catalog_references_outside_object_controllers(
    root: ET.Element,
) -> list[ET.Element]:
    replaced_reference_ids = {
        id(reference)
        for controller in _object_controllers(root)
        for reference in _iter(controller, "CatalogReference")
    }
    return [
        reference
        for reference in _iter(root, "CatalogReference")
        if id(reference) not in replaced_reference_ids
    ]


def _ego_route_event(root: ET.Element, ego_name: str) -> str:
    for maneuver_group in _iter(root, "ManeuverGroup"):
        actors = _child(maneuver_group, "Actors")
        if not any(
            ref.get("entityRef") == ego_name for ref in _children(actors, "EntityRef")
        ):
            continue
        for event in _iter(maneuver_group, "Event"):
            if any(True for _ in _iter(event, "AssignRouteAction")):
                return event.get("name", "")
    return ""


def _missing_rts_count(root: ET.Element, ego_name: str) -> int:
    missing = 0
    for maneuver_group in _iter(root, "ManeuverGroup"):
        actors = {
            ref.get("entityRef", "")
            for ref in _children(_child(maneuver_group, "Actors"), "EntityRef")
        }
        if not actors or ego_name in actors:
            continue
        for trajectory in _iter(maneuver_group, "Trajectory"):
            declarations = _child(trajectory, "ParameterDeclarations")
            if not any(
                parameter.get("name") == "rts-mode"
                for parameter in _children(declarations, "ParameterDeclaration")
            ):
                missing += 1
    return missing


def _missing_criteria(root: ET.Element) -> list[str]:
    storyboard = _child(root, "Storyboard")
    stop_trigger = _child(storyboard, "StopTrigger")
    existing = {
        condition.get("name", "") for condition in _iter(stop_trigger, "Condition")
    }
    return [name for name in CRITERIA if name not in existing]


def _validate_expected_map(
    report: ScenarioReport,
    *,
    expected_map: str,
    expected_opendrive: Optional[Path],
    expected_lanelet: Optional[Path],
) -> None:
    resolution = report.map_resolution
    if resolution is None:
        return
    if expected_map:
        expected_canonical = _canonical_prebuilt_map(expected_map)
        if resolution.kind != "prebuilt" or resolution.map_name != expected_canonical:
            report.errors.append(
                f"Configured prebuilt map {expected_map!r} does not match LogicFile {resolution.logic_file!r}"
            )
    if expected_opendrive:
        if resolution.kind != "custom" or resolution.opendrive is None:
            report.errors.append(
                "Configured CUSTOM_OPENDRIVE does not match the prebuilt map in LogicFile"
            )
        else:
            configured = expected_opendrive.resolve()
            if configured.parent != report.scenario.parent.resolve():
                report.errors.append(
                    "CUSTOM_OPENDRIVE must be in the same directory as the "
                    f"OpenSCENARIO file: {configured}"
                )
            logic_reference = Path(resolution.logic_file.replace("\\", "/"))
            if (
                logic_reference.is_absolute()
                or logic_reference.parent != Path(".")
                or logic_reference.name != configured.name
            ):
                report.errors.append(
                    f"LogicFile filepath must reference the configured OpenDRIVE in the "
                    "same directory as the OpenSCENARIO file; expected only the filename "
                    f"{configured.name!r}, got {resolution.logic_file!r}"
                )
            try:
                matches = resolution.opendrive.samefile(configured)
            except OSError:
                matches = resolution.opendrive.resolve() == configured
            if not matches:
                report.errors.append(
                    f"Configured OpenDRIVE {configured} does not match LogicFile map {resolution.opendrive}"
                )
    if expected_lanelet:
        if resolution.lanelet is None:
            report.errors.append("Configured CUSTOM_LANELET has no matching custom OpenDRIVE map")
        else:
            configured = expected_lanelet.resolve()
            if configured.parent != report.scenario.parent.resolve():
                report.errors.append(
                    "CUSTOM_LANELET must be in the same directory as the "
                    f"OpenSCENARIO file: {configured}"
                )
            try:
                matches = resolution.lanelet.samefile(configured)
            except OSError:
                matches = resolution.lanelet.resolve() == configured
            if not matches:
                report.errors.append(
                    f"Configured Lanelet2 map {configured} does not match resolved map {resolution.lanelet}"
                )


def validate_scenario(
    scenario_file: Path | str,
    *,
    opendrive: Path | str | None = None,
    lanelet: Path | str | None = None,
    ego: str = "",
    repo_root: Path | str | None = None,
    expected_map: str = "",
    expected_opendrive: Path | str | None = None,
    expected_lanelet: Path | str | None = None,
    allow_repair: bool = False,
) -> ScenarioReport:
    scenario_path = Path(scenario_file).resolve()
    report = ScenarioReport(scenario=scenario_path)
    try:
        tree = _parse_xml(scenario_path, "OpenSCENARIO")
        root = tree.getroot()
        version_issues = _version_conversion_issues(root)
        unrepairable_version = any(
            issue.startswith("Unsupported") or issue == "FileHeader is missing"
            for issue in version_issues
        )
        if unrepairable_version or not allow_repair:
            report.errors.extend(version_issues)
        else:
            report.planned_changes.extend(version_issues)
        report.map_resolution = _resolve_map(
            scenario_path,
            root,
            opendrive=Path(opendrive) if opendrive else None,
            lanelet=Path(lanelet) if lanelet else None,
        )
        ego_actor, ego_errors = _select_ego(
            root,
            ego,
            allow_repair=allow_repair,
        )
        report.errors.extend(ego_errors)
        if ego_actor is not None:
            report.ego_actor = ego_actor.get("name", "")
            if report.ego_actor != "ego_vehicle":
                report.planned_changes.append(
                    f"Rename ego actor {report.ego_actor!r} to 'ego_vehicle'"
                )
            if _actor_type(ego_actor) != "ego_vehicle":
                report.planned_changes.append("Set ego actor type=ego_vehicle")
            if not _has_normalized_ego_controller(root, report.ego_actor):
                if allow_repair:
                    report.planned_changes.append(
                        "Replace ego ControllerAction elements with RosRouteController"
                    )
                else:
                    report.errors.append(
                        "ego_vehicle controller setup is not normalized; import the "
                        "scenario to replace it with RosRouteController"
                    )
            missing_rts = _missing_rts_count(root, report.ego_actor)
            if missing_rts:
                report.planned_changes.append(
                    f"Add rts-mode=rts to {missing_rts} non-ego trajectory/trajectories"
                )

        object_controller_count = len(_object_controllers(root))
        if object_controller_count:
            message = (
                f"Remove {object_controller_count} ScenarioObject "
                "ObjectController element(s)"
            )
            if allow_repair:
                report.planned_changes.append(message)
            else:
                report.errors.append(
                    f"{message}; import the scenario to use runtime controllers"
                )
        remaining_catalog_references = _catalog_references_outside_object_controllers(root)
        if remaining_catalog_references:
            report.errors.append(
                f"Scenario contains {len(remaining_catalog_references)} unsupported "
                "external CatalogReference element(s)"
            )
        old_precipitation = sum(
            1 for element in _iter(root, "Precipitation") if "intensity" in element.attrib
        )
        if old_precipitation:
            report.planned_changes.append(
                f"Rename intensity on {old_precipitation} Precipitation element(s)"
            )

        init_actions = _path(root, "Storyboard", "Init", "Actions")
        teleport_count = 0
        for teleport in _iter(init_actions, "TeleportAction"):
            world_position = next(iter(_iter(teleport, "WorldPosition")), None)
            if world_position is None:
                report.errors.append("Init TeleportAction has no WorldPosition")
                continue
            try:
                float(world_position.get("z", "0"))
            except ValueError:
                report.errors.append("Init TeleportAction WorldPosition requires a numeric z value")
                continue
            teleport_count += 1
        if teleport_count:
            report.planned_changes.append(
                f"Raise {teleport_count} initial TeleportAction position(s) by 0.1 m"
            )

        missing_criteria = _missing_criteria(root)
        if missing_criteria:
            report.planned_changes.append(
                f"Add {len(missing_criteria)} missing ScenarioRunner criterion/criteria"
            )

        _validate_expected_map(
            report,
            expected_map=expected_map,
            expected_opendrive=Path(expected_opendrive) if expected_opendrive else None,
            expected_lanelet=Path(expected_lanelet) if expected_lanelet else None,
        )
    except ScenarioCheckerError as error:
        report.errors.append(str(error))
    return report


def validate_scenario_folder(
    scenario_folder: Path | str,
    *,
    repo_root: Path | str | None = None,
    expected_map: str = "",
    expected_opendrive: Path | str | None = None,
    lanelet: Path | str | None = None,
) -> dict[str, object]:
    folder = Path(scenario_folder).resolve()
    if not folder.is_dir():
        return {
            "valid": False,
            "folder": str(folder),
            "checked": 0,
            "errors": [f"Scenario folder does not exist: {folder}"],
            "warnings": [],
        }

    custom_opendrive = Path(expected_opendrive).resolve() if expected_opendrive else None
    selected_reference = custom_opendrive.name if custom_opendrive else expected_map
    selected_normalized = _normalize_map_reference(selected_reference)
    errors: list[str] = []
    warnings: list[str] = []
    checked = 0

    for scenario in sorted(folder.rglob("*.xosc")):
        if "catalogs" in {part.lower() for part in scenario.parts}:
            continue
        try:
            tree = _parse_xml(scenario, "OpenSCENARIO")
            logic = _path(tree.getroot(), "RoadNetwork", "LogicFile")
            logic_file = logic.get("filepath", "") if logic is not None else ""
            if custom_opendrive:
                logic_reference = Path(logic_file.replace("\\", "/"))
                if (
                    scenario.parent.resolve() != custom_opendrive.parent
                    or logic_reference.is_absolute()
                    or logic_reference.parent != Path(".")
                    or logic_reference.name != custom_opendrive.name
                ):
                    continue
            elif selected_normalized and _normalize_map_reference(logic_file) != selected_normalized:
                continue
        except ScenarioCheckerError as error:
            errors.append(f"{scenario}: {error}")
            continue

        report = validate_scenario(
            scenario,
            repo_root=repo_root,
            opendrive=expected_opendrive,
            lanelet=lanelet,
            expected_map=expected_map,
            expected_opendrive=expected_opendrive,
            expected_lanelet=lanelet,
        )
        checked += 1
        errors.extend(f"{scenario}: {message}" for message in report.errors)
        warnings.extend(f"{scenario}: {message}" for message in report.warnings)

    if selected_normalized and checked == 0:
        errors.append(f"No OpenSCENARIO files match the configured map {selected_reference!r}")

    return {
        "valid": not errors,
        "folder": str(folder),
        "checked": checked,
        "errors": errors,
        "warnings": warnings,
    }


def _runtime_asset_path(value: str, scenario_root: Path) -> Path:
    reference = Path(value)
    root = scenario_root.resolve()
    if reference.is_absolute() and reference.exists():
        resolved = reference.resolve()
    else:
        normalized = value.strip().replace("\\", "/").lstrip("/")
        repository_prefix = "carla-simulation/scenarios/"
        if repository_prefix in normalized:
            normalized = normalized.split(repository_prefix, 1)[1]
        resolved = (root / normalized).resolve()
    if not resolved.is_relative_to(root):
        raise ScenarioCheckerError(
            f"Runtime scenario path must stay below {root}: {value}"
        )
    return resolved


def _scenario_root_from_file(scenario: Path) -> Path:
    for parent in scenario.resolve().parents:
        if parent.name == "scenarios" and parent.parent.name == "carla-simulation":
            return parent
    return scenario.resolve().parent


def validate_runtime(
    target: Path | str,
    environment: Optional[dict[str, str]] = None,
) -> dict[str, object]:
    env = os.environ if environment is None else environment
    resolved_target = Path(target).resolve()
    if resolved_target == Path("/"):
        raise ScenarioCheckerError("Scenario path must not be empty or the filesystem root")
    if resolved_target.is_dir():
        target_type = "folder"
        root = resolved_target
    elif resolved_target.is_file():
        target_type = "file"
        root = _scenario_root_from_file(resolved_target)
    else:
        raise ScenarioCheckerError(f"Scenario path does not exist: {resolved_target}")

    expected_map = env.get("MAP", "").strip()
    opendrive_value = env.get("CUSTOM_OPENDRIVE", "").strip()
    lanelet_value = env.get("CUSTOM_LANELET", "").strip()
    expected_opendrive = (
        _runtime_asset_path(opendrive_value, root) if opendrive_value else None
    )
    lanelet = _runtime_asset_path(lanelet_value, root) if lanelet_value else None

    if target_type == "folder":
        return validate_scenario_folder(
            root,
            expected_map=expected_map,
            expected_opendrive=expected_opendrive,
            lanelet=lanelet,
        )
    report = validate_scenario(
        resolved_target,
        opendrive=expected_opendrive,
        expected_map=expected_map,
        expected_opendrive=expected_opendrive,
        expected_lanelet=lanelet,
        lanelet=lanelet,
    )
    return report.to_dict()


def _rename_ego(root: ET.Element, actor: ET.Element) -> None:
    old_name = actor.get("name", "")
    if old_name == "ego_vehicle":
        return
    for element in root.iter():
        for attribute, value in list(element.attrib.items()):
            if value == old_name:
                element.set(attribute, "ego_vehicle")


def _ensure_actor_types(root: ET.Element, ego_actor: ET.Element) -> None:
    for actor in _scenario_objects(root):
        payload = _actor_payload(actor)
        if payload is None:
            continue
        properties = _child(payload, "Properties")
        if properties is None:
            properties = _subelement(payload, "Properties")
        property_type = next(
            (
                prop
                for prop in _children(properties, "Property")
                if prop.get("name") == "type"
            ),
            None,
        )
        if property_type is None:
            property_type = _subelement(properties, "Property", {"name": "type"})
        property_type.set(
            "value",
            "ego_vehicle" if actor is ego_actor else "other_vehicle",
        )


def _remove_object_controllers(root: ET.Element) -> int:
    removed = 0
    for actor in _scenario_objects(root):
        for controller in _children(actor, "ObjectController"):
            actor.remove(controller)
            removed += 1
    return removed


def _clear_catalog_locations(root: ET.Element) -> int:
    catalog_locations = _child(root, "CatalogLocations")
    if catalog_locations is None:
        return 0
    removed = len(list(catalog_locations))
    catalog_locations.clear()
    return removed


def _replace_ros_controller(root: ET.Element) -> int:
    actions = _path(root, "Storyboard", "Init", "Actions")
    if actions is None:
        raise ScenarioCheckerError("Storyboard/Init/Actions is required to add ego controller")
    ego_private = _ego_private(root, "ego_vehicle")
    if ego_private is None:
        ego_private = _subelement(actions, "Private", {"entityRef": "ego_vehicle"})

    removed = 0
    for private_action in list(_children(ego_private, "PrivateAction")):
        if _child(private_action, "ControllerAction") is not None:
            ego_private.remove(private_action)
            removed += 1

    private_action = _subelement(ego_private, "PrivateAction")
    controller_action = _subelement(private_action, "ControllerAction")
    override = _subelement(controller_action, "OverrideControllerValueAction")
    for name in ("Throttle", "Brake", "Clutch", "ParkingBrake", "SteeringWheel"):
        _subelement(override, name, {"value": "0", "active": "false"})
    _subelement(override, "Gear", {"number": "0", "active": "false"})
    assign = _subelement(controller_action, "AssignControllerAction")
    controller = _subelement(assign, "Controller", {"name": "RosRouteController"})
    properties = _subelement(controller, "Properties")
    _subelement(
        properties,
        "Property",
        {"name": "module", "value": ROS_CONTROLLER_MODULE},
    )
    return removed


def _ensure_rts(root: ET.Element) -> int:
    added = 0
    for maneuver_group in _iter(root, "ManeuverGroup"):
        actors = {
            ref.get("entityRef", "")
            for ref in _children(_child(maneuver_group, "Actors"), "EntityRef")
        }
        if not actors or "ego_vehicle" in actors:
            continue
        for trajectory in _iter(maneuver_group, "Trajectory"):
            declarations = _child(trajectory, "ParameterDeclarations")
            if declarations is None:
                declarations = ET.Element(f"{_namespace(trajectory)}ParameterDeclarations")
                trajectory.insert(0, declarations)
            existing = next(
                (
                    parameter
                    for parameter in _children(declarations, "ParameterDeclaration")
                    if parameter.get("name") == "rts-mode"
                ),
                None,
            )
            if existing is None:
                _subelement(
                    declarations,
                    "ParameterDeclaration",
                    {"name": "rts-mode", "value": "rts", "parameterType": "string"},
                )
                added += 1
    return added


def _convert_weather(root: ET.Element) -> int:
    converted = 0
    for precipitation in _iter(root, "Precipitation"):
        if "intensity" not in precipitation.attrib:
            continue
        if "precipitationIntensity" not in precipitation.attrib:
            precipitation.set("precipitationIntensity", precipitation.get("intensity", ""))
        del precipitation.attrib["intensity"]
        converted += 1
    return converted


def _adjust_teleports(root: ET.Element) -> int:
    adjusted = 0
    init_actions = _path(root, "Storyboard", "Init", "Actions")
    for teleport in _iter(init_actions, "TeleportAction"):
        world_position = next(iter(_iter(teleport, "WorldPosition")), None)
        if world_position is None:
            raise ScenarioCheckerError("Init TeleportAction has no WorldPosition")
        try:
            z_value = float(world_position.get("z", "0"))
        except ValueError as error:
            raise ScenarioCheckerError(
                "Init TeleportAction WorldPosition requires a numeric z value"
            ) from error
        world_position.set("z", f"{z_value + 0.1:.12g}")
        adjusted += 1
    return adjusted


def _condition_group(stop_trigger: ET.Element) -> ET.Element:
    return _subelement(stop_trigger, "ConditionGroup")


def _ensure_act_stop_trigger(
    root: ET.Element,
    *,
    timeout: Optional[float],
    stop_on_ego_route_complete: bool,
) -> list[str]:
    actions = []
    stories = _children(_child(root, "Storyboard"), "Story")
    acts = _children(stories[-1], "Act") if stories else []
    if not acts:
        return actions
    act = acts[-1]
    stop_trigger = _child(act, "StopTrigger")
    if stop_trigger is None:
        stop_trigger = _subelement(act, "StopTrigger")

    if timeout is not None and not any(True for _ in _iter(stop_trigger, "SimulationTimeCondition")):
        group = _condition_group(stop_trigger)
        condition = _subelement(
            group,
            "Condition",
            {"name": "OpenADSimTimeout", "delay": "0", "conditionEdge": "rising"},
        )
        by_value = _subelement(condition, "ByValueCondition")
        _subelement(
            by_value,
            "SimulationTimeCondition",
            {"value": f"{timeout:g}", "rule": "greaterThan"},
        )
        actions.append(f"Added {timeout:g}s Act timeout")

    if stop_on_ego_route_complete:
        event_name = _ego_route_event(root, "ego_vehicle")
        existing_refs = {
            condition.get("storyboardElementRef", "")
            for condition in _iter(stop_trigger, "StoryboardElementStateCondition")
            if condition.get("state") == "completeState"
        }
        if event_name and event_name not in existing_refs:
            group = _condition_group(stop_trigger)
            condition = _subelement(
                group,
                "Condition",
                {"name": "EgoRouteDone", "delay": "0", "conditionEdge": "rising"},
            )
            by_value = _subelement(condition, "ByValueCondition")
            _subelement(
                by_value,
                "StoryboardElementStateCondition",
                {
                    "storyboardElementType": "event",
                    "storyboardElementRef": event_name,
                    "state": "completeState",
                },
            )
            actions.append(f"Added EgoRouteDone condition for event {event_name}")
    return actions


def _ensure_criteria(root: ET.Element, driven_distance: float) -> list[str]:
    storyboard = _child(root, "Storyboard")
    if storyboard is None:
        raise ScenarioCheckerError("Storyboard is required to add ScenarioRunner criteria")
    stop_trigger = _child(storyboard, "StopTrigger")
    if stop_trigger is None:
        stop_trigger = _subelement(storyboard, "StopTrigger")

    existing = {
        condition.get("name", "") for condition in _iter(stop_trigger, "Condition")
    }
    criteria_group = next(
        (
            group
            for group in _children(stop_trigger, "ConditionGroup")
            if any(
                condition.get("name", "").startswith("criteria_")
                for condition in _children(group, "Condition")
            )
        ),
        None,
    )
    if criteria_group is None:
        criteria_group = _condition_group(stop_trigger)

    added = []
    for name in CRITERIA:
        if name in existing:
            continue
        condition = _subelement(
            criteria_group,
            "Condition",
            {"name": name, "delay": "0.0", "conditionEdge": "rising"},
        )
        by_value = _subelement(condition, "ByValueCondition")
        if name == "criteria_DrivenDistanceTest":
            attributes = {
                "parameterRef": "distance_success",
                "value": f"{driven_distance:g}",
                "rule": "greaterThan",
            }
        else:
            attributes = {"parameterRef": "", "value": "0", "rule": "lessThan"}
        _subelement(by_value, "ParameterCondition", attributes)
        added.append(name)
    return added


def _sanitize_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    normalized = normalized.strip("._-")
    return normalized


def _available_target(output_root: Path, base_name: str) -> Path:
    candidate = output_root / base_name
    index = 2
    while candidate.exists():
        candidate = output_root / f"{base_name}_{index}"
        index += 1
    return candidate


def _write_xml(tree: ET.ElementTree, path: Path) -> None:
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def import_scenario(
    scenario_file: Path | str,
    *,
    output_root: Path | str,
    name: str = "",
    opendrive: Path | str | None = None,
    lanelet: Path | str | None = None,
    ego: str = "",
    timeout: Optional[float] = 60.0,
    stop_on_ego_route_complete: bool = True,
    driven_distance: float = 30.0,
    repo_root: Path | str | None = None,
) -> ImportResult:
    if timeout is not None and timeout <= 0:
        raise ScenarioCheckerError("Scenario timeout must be greater than zero")
    if driven_distance <= 0:
        raise ScenarioCheckerError("Driven-distance criterion must be greater than zero")
    scenario_path = Path(scenario_file).resolve()
    root_path = Path(repo_root).resolve() if repo_root else _repo_root_from(scenario_path)
    report = validate_scenario(
        scenario_path,
        opendrive=opendrive,
        lanelet=lanelet,
        ego=ego,
        repo_root=root_path,
        allow_repair=True,
    )
    if not report.is_valid or report.map_resolution is None:
        raise ScenarioCheckerError("; ".join(report.errors))

    tree = _parse_xml(scenario_path, "OpenSCENARIO")
    root = tree.getroot()
    ego_actor, ego_errors = _select_ego(root, ego, allow_repair=True)
    if ego_actor is None:
        raise ScenarioCheckerError("; ".join(ego_errors))

    actions: list[str] = []
    import_warnings = list(report.warnings)
    actions.extend(_convert_to_osc_1_1(root))
    original_ego_name = ego_actor.get("name", "")
    _rename_ego(root, ego_actor)
    if original_ego_name != "ego_vehicle":
        actions.append(f"Renamed ego actor {original_ego_name} to ego_vehicle")
    object_controllers_removed = _remove_object_controllers(root)
    if object_controllers_removed:
        actions.append(
            f"Removed {object_controllers_removed} ScenarioObject ObjectController element(s)"
        )
    catalog_locations_cleared = _clear_catalog_locations(root)
    if catalog_locations_cleared:
        actions.append(
            f"Cleared {catalog_locations_cleared} unused catalog location(s)"
        )
    _ensure_actor_types(root, ego_actor)
    actions.append("Normalized actor type properties")
    replaced_controller_actions = _replace_ros_controller(root)
    if replaced_controller_actions:
        actions.append(
            f"Replaced {replaced_controller_actions} ego ControllerAction element(s) "
            "with RosRouteController"
        )
    else:
        actions.append("Added RosRouteController to ego_vehicle")

    rts_added = _ensure_rts(root)
    if rts_added:
        actions.append(f"Added rts-mode=rts to {rts_added} non-ego trajectory/trajectories")
    weather_converted = _convert_weather(root)
    if weather_converted:
        actions.append(f"Converted {weather_converted} precipitation attribute(s)")
    teleport_adjusted = _adjust_teleports(root)
    if teleport_adjusted:
        actions.append(f"Raised {teleport_adjusted} initial teleport position(s) by 0.1 m")
    if stop_on_ego_route_complete and not _ego_route_event(root, "ego_vehicle"):
        import_warnings.append(
            "No ego AssignRouteAction event found; route-completion StopTrigger was not added"
        )
    actions.extend(
        _ensure_act_stop_trigger(
            root,
            timeout=timeout,
            stop_on_ego_route_complete=stop_on_ego_route_complete,
        )
    )
    criteria_added = _ensure_criteria(root, driven_distance)
    if criteria_added:
        actions.append(f"Added {len(criteria_added)} ScenarioRunner criterion/criteria")

    resolution = report.map_resolution
    logic_element = _path(root, "RoadNetwork", "LogicFile")
    assert logic_element is not None

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_name = _sanitize_name(name) if name.strip() else timestamp
    if not base_name:
        raise ScenarioCheckerError("Import name contains no usable characters")
    destination_root = Path(output_root).resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    destination = _available_target(destination_root, base_name)
    staging = Path(tempfile.mkdtemp(prefix=".scenario-import-", dir=destination_root))
    staging.chmod(0o2775)

    try:
        scenario_output = staging / f"{destination.name}.xosc"
        imported_resolution = resolution
        if resolution.kind == "custom":
            assert resolution.opendrive is not None and resolution.lanelet is not None
            opendrive_name = _sanitize_name(resolution.opendrive.stem) + ".xodr"
            lanelet_name = _sanitize_name(resolution.lanelet.stem) + ".osm"
            opendrive_output = staging / opendrive_name
            lanelet_output = staging / lanelet_name
            shutil.copy2(resolution.opendrive, opendrive_output)
            shutil.copy2(resolution.lanelet, lanelet_output)
            logic_element.set("filepath", opendrive_name)
            imported_resolution = MapResolution(
                kind="custom",
                logic_file=opendrive_name,
                opendrive=destination / opendrive_name,
                lanelet=destination / lanelet_name,
            )
            actions.append(f"Set LogicFile to imported OpenDRIVE {opendrive_name}")
        else:
            logic_element.set("filepath", resolution.map_name)
            imported_resolution = MapResolution(
                kind="prebuilt",
                logic_file=resolution.map_name,
                map_name=resolution.map_name,
            )

        _write_xml(tree, scenario_output)
        manifest = {
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "source": str(scenario_path),
            "scenario": f"{destination.name}.xosc",
            "map": {
                "type": imported_resolution.kind,
                "map_name": imported_resolution.map_name,
                "opendrive": imported_resolution.opendrive.name if imported_resolution.opendrive else "",
                "lanelet": imported_resolution.lanelet.name if imported_resolution.lanelet else "",
            },
            "actions": actions,
            "teleport_z_adjusted": True,
        }
        (staging / "import.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        staging.rename(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return ImportResult(
        directory=destination,
        scenario_file=destination / f"{destination.name}.xosc",
        map_resolution=imported_resolution,
        actions=tuple(actions),
        warnings=tuple(import_warnings),
        repo_root=root_path,
    )


def _add_map_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--opendrive", help="custom OpenDRIVE file overriding LogicFile")
    parser.add_argument("--lanelet", help="matching Lanelet2 .osm file")
    parser.add_argument("--ego", default="", help="actor to use when ego_vehicle is missing")
    parser.add_argument("--repo-root", help="OpenADSim repository root")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and import OpenSCENARIO bundles for OpenADSim/CARLA"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate without modifying files")
    validate.add_argument("scenario")
    _add_map_arguments(validate)
    validate.add_argument("--expected-map", default="")
    validate.add_argument("--expected-opendrive")
    validate.add_argument("--expected-lanelet")

    validate_folder = subparsers.add_parser(
        "validate-folder",
        help="validate scenarios matching one active map",
    )
    validate_folder.add_argument("folder")
    validate_folder.add_argument("--repo-root")
    validate_folder.add_argument("--expected-map", default="")
    validate_folder.add_argument("--expected-opendrive")
    validate_folder.add_argument("--lanelet")
    validate_folder.add_argument("--json", action="store_true")

    validate_runtime_parser = subparsers.add_parser(
        "validate-runtime",
        help="validate the active scenario configuration from environment variables",
    )
    validate_runtime_parser.add_argument("target")
    validate_runtime_parser.add_argument("--json", action="store_true")

    importer = subparsers.add_parser("import", help="validate and create an import bundle")
    importer.add_argument("scenario")
    _add_map_arguments(importer)
    importer.add_argument("--output-root", required=True)
    importer.add_argument("--name", default="")
    importer.add_argument("--timeout", type=float, default=60.0)
    importer.add_argument("--no-ego-route-stop", action="store_true")
    importer.add_argument("--driven-distance", type=float, default=30.0)
    return parser.parse_args()


def _print_report(data: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return
    if data.get("valid"):
        target = data.get("scenario_file") or data.get("scenario") or data.get("folder", "")
        print(f"Valid: {target}")
        if "checked" in data:
            print(f"  checked: {data['checked']}")
        for action in data.get("actions", data.get("planned_changes", [])):
            print(f"  - {action}")
        for warning in data.get("warnings", []):
            print(f"  warning: {warning}")
    else:
        for error in data.get("errors", []):
            print(f"Error: {error}", file=sys.stderr)


def main() -> int:
    args = _parse_args()
    try:
        if args.command == "validate":
            report = validate_scenario(
                args.scenario,
                opendrive=args.opendrive,
                lanelet=args.lanelet,
                ego=args.ego,
                repo_root=args.repo_root,
                expected_map=args.expected_map,
                expected_opendrive=args.expected_opendrive,
                expected_lanelet=args.expected_lanelet,
            )
            data = report.to_dict()
            _print_report(data, args.json)
            return 0 if report.is_valid else 1

        if args.command == "validate-folder":
            data = validate_scenario_folder(
                args.folder,
                repo_root=args.repo_root,
                expected_map=args.expected_map,
                expected_opendrive=args.expected_opendrive,
                lanelet=args.lanelet,
            )
            _print_report(data, args.json)
            return 0 if data["valid"] else 1

        if args.command == "validate-runtime":
            data = validate_runtime(args.target)
            _print_report(data, args.json)
            return 0 if data["valid"] else 1

        repo_root = Path(args.repo_root).resolve() if args.repo_root else None
        result = import_scenario(
            args.scenario,
            output_root=args.output_root,
            name=args.name,
            opendrive=args.opendrive,
            lanelet=args.lanelet,
            ego=args.ego,
            timeout=args.timeout,
            stop_on_ego_route_complete=not args.no_ego_route_stop,
            driven_distance=args.driven_distance,
            repo_root=repo_root,
        )
        data = result.to_dict()
        _print_report(data, args.json)
        return 0
    except ScenarioCheckerError as error:
        data = {"valid": False, "errors": [str(error)], "warnings": []}
        _print_report(data, args.json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
