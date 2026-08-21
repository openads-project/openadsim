import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from stat import S_IMODE

from scenario_checker import (
    CRITERIA,
    ScenarioCheckerError,
    import_scenario,
    validate_runtime,
    validate_scenario,
)


SCENARIO = """<?xml version="1.0"?>
<OpenSCENARIO>
  <FileHeader revMajor="1" revMinor="1"/>
  <RoadNetwork><LogicFile filepath="campus"/></RoadNetwork>
  <Entities>
    <ScenarioObject name="car_1">
      <Vehicle name="vehicle.test" vehicleCategory="car"><Properties/></Vehicle>
    </ScenarioObject>
    <ScenarioObject name="car_2">
      <Vehicle name="vehicle.test" vehicleCategory="car"><Properties/></Vehicle>
    </ScenarioObject>
  </Entities>
  <Storyboard>
    <Init><Actions>
      <Private entityRef="car_1"><PrivateAction><TeleportAction><Position><WorldPosition x="0" y="0"/></Position></TeleportAction></PrivateAction></Private>
      <Private entityRef="car_2"><PrivateAction><TeleportAction><Position><WorldPosition x="1" y="0" z="1"/></Position></TeleportAction></PrivateAction></Private>
      <GlobalAction><EnvironmentAction><Environment><Weather><Precipitation intensity="0.5" precipitationType="rain"/></Weather></Environment></EnvironmentAction></GlobalAction>
    </Actions></Init>
    <Story name="story"><Act name="act">
      <ManeuverGroup name="ego"><Actors><EntityRef entityRef="car_1"/></Actors><Maneuver><Event name="ego_route"><Action><PrivateAction><RoutingAction><AssignRouteAction><Route/></AssignRouteAction></RoutingAction></PrivateAction></Action></Event></Maneuver></ManeuverGroup>
      <ManeuverGroup name="other"><Actors><EntityRef entityRef="car_2"/></Actors><Maneuver><Event name="other_route"><Action><PrivateAction><RoutingAction><FollowTrajectoryAction><TrajectoryRef><Trajectory name="other"><Shape><Polyline><Vertex time="0"><Position><WorldPosition x="0" y="0"/></Position></Vertex></Polyline></Shape></Trajectory></TrajectoryRef></FollowTrajectoryAction></RoutingAction></PrivateAction></Action></Event></Maneuver></ManeuverGroup>
      <StartTrigger/>
    </Act></Story>
    <StopTrigger/>
  </Storyboard>
</OpenSCENARIO>
"""


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class ScenarioCheckerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.scenario = self.root / "source.xosc"
        self.scenario.write_text(SCENARIO, encoding="utf-8")
        self.output = self.root / "custom-imports"

    def tearDown(self):
        self.temporary.cleanup()

    def test_import_applies_targeted_adjustments_and_indexes_duplicates(self):
        first = import_scenario(
            self.scenario,
            output_root=self.output,
            name="demo",
            repo_root=self.root,
        )
        second = import_scenario(
            self.scenario,
            output_root=self.output,
            name="demo",
            repo_root=self.root,
        )

        self.assertEqual(first.directory.name, "demo")
        self.assertEqual(second.directory.name, "demo_2")
        self.assertEqual(S_IMODE(first.directory.stat().st_mode), 0o2775)
        self.assertEqual(S_IMODE(second.directory.stat().st_mode), 0o2775)
        root = ET.parse(first.scenario_file).getroot()

        actors = [element for element in root.iter() if local_name(element.tag) == "ScenarioObject"]
        self.assertEqual(actors[0].get("name"), "ego_vehicle")
        refs = [
            element.get("entityRef")
            for element in root.iter()
            if "entityRef" in element.attrib
        ]
        self.assertIn("ego_vehicle", refs)
        self.assertTrue(any(local_name(element.tag) == "AssignControllerAction" for element in root.iter()))

        rts_parameters = [
            element
            for element in root.iter()
            if local_name(element.tag) == "ParameterDeclaration"
            and element.get("name") == "rts-mode"
        ]
        self.assertEqual(len(rts_parameters), 1)
        self.assertEqual(rts_parameters[0].get("value"), "rts")

        precipitation = next(
            element for element in root.iter() if local_name(element.tag) == "Precipitation"
        )
        self.assertNotIn("intensity", precipitation.attrib)
        self.assertEqual(precipitation.get("precipitationIntensity"), "0.5")

        teleports = [
            next(
                child
                for child in element.iter()
                if local_name(child.tag) == "WorldPosition"
            )
            for element in root.iter()
            if local_name(element.tag) == "TeleportAction"
        ]
        self.assertEqual([position.get("z") for position in teleports], ["0.1", "1.1"])

        conditions = [
            element for element in root.iter() if local_name(element.tag) == "Condition"
        ]
        condition_names = {element.get("name") for element in conditions}
        self.assertTrue(set(CRITERIA).issubset(condition_names))
        self.assertIn("EgoRouteDone", condition_names)
        self.assertIn("OpenADSimTimeout", condition_names)

    def test_uploaded_opendrive_overrides_prebuilt_logic_file(self):
        opendrive = self.root / "custom.xodr"
        lanelet = self.root / "custom.osm"
        opendrive.write_text("<OpenDRIVE/>", encoding="utf-8")
        lanelet.write_text("<osm/>", encoding="utf-8")

        result = import_scenario(
            self.scenario,
            output_root=self.output,
            name="custom",
            opendrive=opendrive,
            lanelet=lanelet,
            repo_root=self.root,
        )

        root = ET.parse(result.scenario_file).getroot()
        logic_file = next(
            element for element in root.iter() if local_name(element.tag) == "LogicFile"
        )
        self.assertEqual(logic_file.get("filepath"), "custom.xodr")
        self.assertTrue((result.directory / "custom.xodr").is_file())
        self.assertTrue((result.directory / "custom.osm").is_file())

    def test_existing_ego_controller_is_replaced(self):
        controlled = self.root / "controlled.xosc"
        controlled.write_text(
            SCENARIO.replace(
                '<Private entityRef="car_1"><PrivateAction><TeleportAction>',
                '<Private entityRef="car_1"><PrivateAction><ControllerAction>'
                '<ActivateControllerAction lateral="true" longitudinal="true"/>'
                '</ControllerAction></PrivateAction><PrivateAction><ControllerAction>'
                '<AssignControllerAction><Controller name="ExistingController"/>'
                '</AssignControllerAction></ControllerAction></PrivateAction>'
                '<PrivateAction><TeleportAction>',
            ),
            encoding="utf-8",
        )
        result = import_scenario(
            controlled,
            output_root=self.output,
            name="controlled",
            repo_root=self.root,
        )
        root = ET.parse(result.scenario_file).getroot()
        controllers = [
            element
            for element in root.iter()
            if local_name(element.tag) == "Controller"
        ]
        self.assertEqual(
            [controller.get("name") for controller in controllers],
            ["RosRouteController"],
        )
        controller_actions = [
            element
            for element in root.iter()
            if local_name(element.tag) == "ControllerAction"
        ]
        self.assertEqual(len(controller_actions), 1)
        self.assertTrue(
            any(
                local_name(element.tag) == "AssignControllerAction"
                for element in controller_actions[0].iter()
            )
        )

    def test_import_removes_actor_object_controller_catalog_dependency(self):
        catalog_scenario = self.root / "catalog-controller.xosc"
        source = SCENARIO.replace(
            '<FileHeader revMajor="1" revMinor="1"/>',
            '<FileHeader revMajor="1" revMinor="1"/>'
            '<CatalogLocations><ControllerCatalog><Directory '
            'path="./Catalogs/Controllers"/></ControllerCatalog></CatalogLocations>',
        ).replace(
            "</Vehicle>\n    </ScenarioObject>",
            '</Vehicle><ObjectController><CatalogReference '
            'catalogName="ControllerCatalog" entryName="interactiveDriver"/>'
            "</ObjectController>\n    </ScenarioObject>",
            1,
        )
        catalog_scenario.write_text(source, encoding="utf-8")

        strict_report = validate_scenario(catalog_scenario, repo_root=self.root)
        self.assertFalse(strict_report.is_valid)
        self.assertTrue(
            any("ObjectController" in error for error in strict_report.errors)
        )

        result = import_scenario(
            catalog_scenario,
            output_root=self.output,
            name="catalog-controller",
            repo_root=self.root,
        )
        root = ET.parse(result.scenario_file).getroot()
        self.assertFalse(any(local_name(item.tag) == "ObjectController" for item in root.iter()))
        self.assertFalse(any(local_name(item.tag) == "CatalogReference" for item in root.iter()))
        catalog_locations = next(
            item for item in root.iter() if local_name(item.tag) == "CatalogLocations"
        )
        self.assertEqual(len(list(catalog_locations)), 0)
        self.assertTrue(any("Removed 1" in action for action in result.actions))

    def test_import_rejects_other_external_catalog_references(self):
        catalog_scenario = self.root / "external-catalog.xosc"
        catalog_scenario.write_text(
            SCENARIO.replace(
                "</Vehicle>\n    </ScenarioObject>",
                '</Vehicle><CatalogReference catalogName="VehicleCatalog" '
                'entryName="externalVehicle"/>\n    </ScenarioObject>',
                1,
            ),
            encoding="utf-8",
        )

        report = validate_scenario(
            catalog_scenario,
            repo_root=self.root,
            allow_repair=True,
        )

        self.assertFalse(report.is_valid)
        self.assertTrue(
            any(
                "unsupported external CatalogReference" in error
                for error in report.errors
            )
        )

    def test_referenced_opendrive_requires_explicit_upload(self):
        missing = self.root / "missing.xosc"
        missing.write_text(
            SCENARIO.replace('filepath="campus"', 'filepath="custom.xodr"'),
            encoding="utf-8",
        )
        (self.root / "custom.xodr").write_text("<OpenDRIVE/>", encoding="utf-8")
        (self.root / "custom.osm").write_text("<osm/>", encoding="utf-8")
        report = validate_scenario(missing, repo_root=self.root, allow_repair=True)
        self.assertFalse(report.is_valid)
        self.assertIn("must be explicitly uploaded", report.errors[0])

    def test_custom_map_is_resolved_only_relative_to_scenario(self):
        upload_directory = self.root / "uploads"
        upload_directory.mkdir()
        uploaded_scenario = upload_directory / "source.xosc"
        uploaded_scenario.write_text(
            SCENARIO.replace('filepath="campus"', 'filepath="scenario.xodr"'),
            encoding="utf-8",
        )
        (self.root / "scenario.xodr").write_text("<OpenDRIVE/>", encoding="utf-8")
        (self.root / "scenario.osm").write_text("<osm/>", encoding="utf-8")

        report = validate_scenario(
            uploaded_scenario,
            repo_root=self.root,
            allow_repair=True,
        )

        self.assertFalse(report.is_valid)
        self.assertTrue(any("must be explicitly uploaded" in error for error in report.errors))

    def test_import_colocates_explicit_custom_map_files(self):
        bundle = self.root / "bundle"
        bundle.mkdir()
        map_sources = self.root / "map-sources"
        map_sources.mkdir()
        bundled_scenario = bundle / "source.xosc"
        bundled_scenario.write_text(
            SCENARIO.replace('filepath="campus"', 'filepath="custom.xodr"'),
            encoding="utf-8",
        )
        (map_sources / "custom.xodr").write_text("<OpenDRIVE/>", encoding="utf-8")
        (map_sources / "custom.osm").write_text("<osm/>", encoding="utf-8")

        result = import_scenario(
            bundled_scenario,
            output_root=self.output,
            name="bundle",
            opendrive=map_sources / "custom.xodr",
            lanelet=map_sources / "custom.osm",
            repo_root=self.root,
        )

        self.assertEqual(result.scenario_file.parent, result.directory)
        self.assertEqual(result.map_resolution.opendrive.parent, result.directory)
        self.assertEqual(result.map_resolution.lanelet.parent, result.directory)
        self.assertTrue((result.directory / "bundle.xosc").is_file())
        self.assertTrue((result.directory / "custom.xodr").is_file())
        self.assertTrue((result.directory / "custom.osm").is_file())

    def test_custom_map_files_must_share_scenario_directory(self):
        map_directory = self.root / "maps"
        map_directory.mkdir()
        opendrive = map_directory / "custom.xodr"
        lanelet = map_directory / "custom.osm"
        opendrive.write_text("<OpenDRIVE/>", encoding="utf-8")
        lanelet.write_text("<osm/>", encoding="utf-8")

        report = validate_scenario(
            self.scenario,
            opendrive=opendrive,
            lanelet=lanelet,
            expected_opendrive=opendrive,
            expected_lanelet=lanelet,
            repo_root=self.root,
            allow_repair=True,
        )

        self.assertFalse(report.is_valid)
        self.assertTrue(any("same directory" in error for error in report.errors))

        local_opendrive = self.root / "custom.xodr"
        local_opendrive.write_text("<OpenDRIVE/>", encoding="utf-8")
        lanelet_report = validate_scenario(
            self.scenario,
            opendrive=local_opendrive,
            lanelet=lanelet,
            expected_opendrive=local_opendrive,
            expected_lanelet=lanelet,
            repo_root=self.root,
            allow_repair=True,
        )
        self.assertFalse(lanelet_report.is_valid)
        self.assertTrue(
            any(
                "CUSTOM_LANELET must be in the same directory" in error
                for error in lanelet_report.errors
            )
        )

    def test_runtime_logic_file_must_match_opendrive_name_without_directory(self):
        opendrive = self.root / "custom.xodr"
        lanelet = self.root / "custom.osm"
        opendrive.write_text("<OpenDRIVE/>", encoding="utf-8")
        lanelet.write_text("<osm/>", encoding="utf-8")

        report = validate_scenario(
            self.scenario,
            opendrive=opendrive,
            lanelet=lanelet,
            expected_opendrive=opendrive,
            expected_lanelet=lanelet,
            repo_root=self.root,
            allow_repair=True,
        )

        self.assertFalse(report.is_valid)
        self.assertTrue(
            any(
                "same directory as the OpenSCENARIO file" in error
                for error in report.errors
            )
        )

    def test_runtime_validation_resolves_environment_paths(self):
        opendrive = self.root / "custom.xodr"
        lanelet = self.root / "custom.osm"
        opendrive.write_text("<OpenDRIVE/>", encoding="utf-8")
        lanelet.write_text("<osm/>", encoding="utf-8")
        scenario_root = self.root / "carla-simulation/scenarios"
        result = import_scenario(
            self.scenario,
            output_root=scenario_root / "custom-imports",
            name="runtime",
            opendrive=opendrive,
            lanelet=lanelet,
            repo_root=self.root,
        )
        prefix = "carla-simulation/scenarios/custom-imports/runtime"
        environment = {
            "SCENARIO_FILE": f"{prefix}/runtime.xosc",
            "MAP": "",
            "CUSTOM_OPENDRIVE": f"{prefix}/custom.xodr",
            "CUSTOM_LANELET": f"{prefix}/custom.osm",
        }

        duplicate = scenario_root / "custom-imports/duplicate"
        duplicate.mkdir()
        (duplicate / "duplicate.xosc").write_text(
            SCENARIO.replace('filepath="campus"', 'filepath="custom.xodr"'),
            encoding="utf-8",
        )
        (duplicate / "custom.xodr").write_text("<OpenDRIVE/>", encoding="utf-8")
        (duplicate / "custom.osm").write_text("<osm/>", encoding="utf-8")

        automated = validate_runtime(result.scenario_file, environment)
        manual = validate_runtime(scenario_root, environment)
        wrong_bundle = validate_runtime(duplicate / "duplicate.xosc", environment)

        self.assertTrue(automated["valid"])
        self.assertTrue(manual["valid"])
        self.assertEqual(manual["checked"], 1)
        self.assertFalse(wrong_bundle["valid"])
        self.assertTrue(
            any("same directory" in error for error in wrong_bundle["errors"])
        )
        self.assertEqual(Path(automated["scenario"]), result.scenario_file)

        with self.assertRaisesRegex(ScenarioCheckerError, "does not exist"):
            validate_runtime(scenario_root / "missing.xosc", environment)

    def test_import_converts_openscenario_1_2_to_1_1(self):
        scenario_1_2 = self.root / "source-1.2.xosc"
        scenario_1_2.write_text(
            SCENARIO.replace('revMinor="1"', 'revMinor="2"')
            .replace(
                "  <RoadNetwork>",
                "  <VariableDeclarations><VariableDeclaration name=\"state\" variableType=\"string\" value=\"idle\"/></VariableDeclarations>\n"
                "  <RoadNetwork>",
            )
            .replace(
                '<Event name="other_route">',
                '<Event name="other_route" priority="override">',
            )
            .replace(
                "      <StartTrigger/>",
                '      <RelativeDistanceCondition relativeDistanceType="cartesianDistance"/>\n'
                "      <StartTrigger/>",
            ),
            encoding="utf-8",
        )

        strict_report = validate_scenario(scenario_1_2, repo_root=self.root)
        self.assertFalse(strict_report.is_valid)
        self.assertTrue(any("OpenSCENARIO 1.2" in error for error in strict_report.errors))

        result = import_scenario(
            scenario_1_2,
            output_root=self.output,
            name="converted",
            repo_root=self.root,
        )
        root = ET.parse(result.scenario_file).getroot()
        header = next(element for element in root if local_name(element.tag) == "FileHeader")
        self.assertEqual((header.get("revMajor"), header.get("revMinor")), ("1", "1"))
        self.assertFalse(
            any(local_name(element.tag) == "VariableDeclarations" for element in root)
        )
        event = next(
            element
            for element in root.iter()
            if local_name(element.tag) == "Event" and element.get("name") == "other_route"
        )
        self.assertEqual(event.get("priority"), "overwrite")
        distance = next(
            element
            for element in root.iter()
            if local_name(element.tag) == "RelativeDistanceCondition"
        )
        self.assertEqual(distance.get("relativeDistanceType"), "euclidianDistance")


if __name__ == "__main__":
    unittest.main()
