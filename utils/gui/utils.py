"""Shared utility helpers for configuration modules."""

from pathlib import Path
import xml.etree.ElementTree as ET


IMPORT_ROOT_PARTS = ("carla-simulation", "scenarios", "custom-imports")


def filter_files_by_directory(
    repo_root: Path, files: list[str], reference: str,
) -> list[str]:
    directory = (repo_root / reference.replace("\\", "/")).resolve().parent
    return [
        path for path in files
        if (repo_root / path.replace("\\", "/")).resolve().parent == directory
    ]


def discover_files_with_suffix(
    repo_root: Path,
    suffixes: str | tuple[str, ...] | list[str],
) -> list[str]:
    found: set[str] = set()
    ignored_parts = {".git", "__pycache__", ".venv", "node_modules"}

    suffix_values = [suffixes] if isinstance(suffixes, str) else list(suffixes)
    normalized_suffixes: list[str] = []
    for suffix in suffix_values:
        normalized = suffix.strip().lower()
        if normalized.startswith("*"):
            normalized = normalized[1:]
        if normalized and not normalized.startswith("."):
            normalized = f".{normalized}"
        if normalized:
            normalized_suffixes.append(normalized)

    if not normalized_suffixes:
        return []

    if not repo_root.exists():
        return []

    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in ignored_parts for part in path.parts):
            continue
        if not any(path.name.lower().endswith(suffix) for suffix in normalized_suffixes):
            continue
        try:
            relative_part = path.relative_to(repo_root).as_posix()
        except ValueError:
            continue
        found.add(relative_part)

    return sorted(found)


def import_bundle_directory(value: str) -> str | None:
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        return None
    parts = Path(normalized).parts
    for index in range(len(parts) - len(IMPORT_ROOT_PARTS)):
        if tuple(parts[index : index + len(IMPORT_ROOT_PARTS)]) != IMPORT_ROOT_PARTS:
            continue
        bundle_index = index + len(IMPORT_ROOT_PARTS)
        if bundle_index + 1 >= len(parts):
            return None
        bundle_name = parts[bundle_index]
        if bundle_name in {"", ".", ".."}:
            return None
        return Path(*IMPORT_ROOT_PARTS, bundle_name).as_posix()
    return None


def first_import_bundle(*values: str) -> str | None:
    return next(
        (
            bundle
            for value in values
            if (bundle := import_bundle_directory(value)) is not None
        ),
        None,
    )


def filter_files_by_import_bundle(files: list[str], bundle: str) -> list[str]:
    return [path for path in files if import_bundle_directory(path) == bundle]


def _normalized_map_reference(map_value: str) -> str:
    value = map_value.strip().replace("\\", "/")
    if not value:
        return ""

    filename = value.rsplit("/", 1)[-1]
    if filename.lower().endswith(".xodr"):
        value = filename[:-len(".xodr")]

    return value.lower()


def _xosc_logic_file(xosc_file: Path) -> str:
    try:
        root = ET.parse(xosc_file).getroot()
    except (OSError, ET.ParseError):
        return ""

    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "LogicFile":
            return element.attrib.get("filepath", "").strip()

    return ""


def filter_xosc_files_by_map(
    repo_root: Path,
    xosc_files: list[str],
    map_value: str,
) -> list[str]:
    map_path = Path(map_value.strip().replace("\\", "/"))
    if map_path.suffix.lower() == ".xodr":
        configured_opendrive = (
            map_path.resolve()
            if map_path.is_absolute()
            else (repo_root / map_path).resolve()
        )
        matching_scenarios: list[str] = []
        for xosc_file in xosc_files:
            scenario = (repo_root / xosc_file).resolve()
            logic_reference = Path(
                _xosc_logic_file(scenario).replace("\\", "/")
            )
            if (
                scenario.parent == configured_opendrive.parent
                and not logic_reference.is_absolute()
                and logic_reference.parent == Path(".")
                and logic_reference.name == configured_opendrive.name
            ):
                matching_scenarios.append(xosc_file)
        return matching_scenarios

    selected_map = _normalized_map_reference(map_value)
    if not selected_map:
        return xosc_files

    return [
        xosc_file
        for xosc_file in xosc_files
        if _normalized_map_reference(_xosc_logic_file(repo_root / xosc_file))
        == selected_map
    ]


def discover_example_overlays(repo_root: Path) -> list[str]:
    examples_root = repo_root / "examples"
    if not examples_root.exists():
        return []

    discovered: list[str] = []
    for example_dir in sorted(examples_root.iterdir()):
        if not example_dir.is_dir():
            continue
        if (example_dir / "docker-compose.override.yml").exists():
            discovered.append(example_dir.name)
    return discovered
