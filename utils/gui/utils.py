"""Shared utility helpers for configuration modules."""

from pathlib import Path
import xml.etree.ElementTree as ET


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


def _normalized_map_reference(map_value: str) -> str:
    value = map_value.strip().replace("\\", "/")
    if not value:
        return ""

    filename = value.rsplit("/", 1)[-1]
    if filename.lower().endswith(".xodr"):
        value = filename[:-len(".xodr")]

    return value.lower()


def _xosc_map_reference(xosc_file: Path) -> str:
    try:
        root = ET.parse(xosc_file).getroot()
    except (OSError, ET.ParseError):
        return ""

    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "LogicFile":
            return _normalized_map_reference(element.attrib.get("filepath", ""))

    return ""


def filter_xosc_files_by_map(
    repo_root: Path,
    xosc_files: list[str],
    map_value: str,
) -> list[str]:
    selected_map = _normalized_map_reference(map_value)
    if not selected_map:
        return xosc_files

    return [
        xosc_file
        for xosc_file in xosc_files
        if _xosc_map_reference(repo_root / xosc_file) == selected_map
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
