#!/usr/bin/env python3
"""Rebuild fetched parent charts against a local openadservice chart."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml


def _chart_version(chart_dir: Path) -> str:
    metadata = yaml.safe_load((chart_dir / "Chart.yaml").read_text(encoding="utf-8"))
    version = metadata.get("version") if isinstance(metadata, dict) else None
    if not isinstance(version, str) or not version:
        raise RuntimeError(f"chart has no version: {chart_dir}")
    return version


def _parent_chart(fetch_dir: Path, release_name: str) -> Path:
    release_dir = fetch_dir / release_name
    candidates = [
        chart_yaml.parent
        for chart_yaml in release_dir.rglob("Chart.yaml")
        if "charts" not in chart_yaml.relative_to(release_dir).parts
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected one fetched parent chart for {release_name}, found {candidates}"
        )
    return candidates[0]


def _fetch_parent_chart(
    fetch_dir: Path, release_name: str, chart: str, version: str
) -> Path:
    destination = fetch_dir / release_name
    destination.mkdir(parents=True)
    subprocess.run(
        ["helm", "pull", chart, "--version", version, "--untar", "--untardir", str(destination)],
        check=True,
    )
    return _parent_chart(fetch_dir, release_name)


def _replace_dependency(parent_chart: Path, base_chart: Path, version: str) -> None:
    chart_yaml = parent_chart / "Chart.yaml"
    metadata = yaml.safe_load(chart_yaml.read_text(encoding="utf-8"))
    dependencies = metadata.get("dependencies", [])
    matches = [
        dependency
        for dependency in dependencies
        if isinstance(dependency, dict) and dependency.get("name") == "openadservice"
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one openadservice dependency in {chart_yaml}, found {len(matches)}"
        )

    matches[0]["version"] = version
    matches[0]["repository"] = f"file://{base_chart}"
    chart_yaml.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")

    (parent_chart / "Chart.lock").unlink(missing_ok=True)
    bundled_dir = parent_chart / "charts" / "openadservice"
    if bundled_dir.exists():
        shutil.rmtree(bundled_dir)
    for bundled_archive in (parent_chart / "charts").glob("openadservice-*.tgz"):
        bundled_archive.unlink()

    subprocess.run(
        ["helm", "dependency", "update", str(parent_chart), "--skip-refresh"],
        check=True,
    )


def _absolute_values_paths(document: dict[str, Any], source_dir: Path) -> None:
    for environment in document.get("environments", {}).values():
        if not isinstance(environment, dict):
            continue
        environment["values"] = [
            str((source_dir / value).resolve()) if isinstance(value, str) else value
            for value in environment.get("values", [])
        ]
    for release in document.get("releases", []):
        release["values"] = [
            str((source_dir / value).resolve()) if isinstance(value, str) else value
            for value in release.get("values", [])
        ]


def prepare(state_file: Path, base_chart: Path) -> Path:
    state_file = state_file.resolve()
    base_chart = base_chart.resolve()
    if not (base_chart / "Chart.yaml").is_file():
        raise RuntimeError(f"openadservice chart not found: {base_chart}")

    documents = list(yaml.safe_load_all(state_file.read_text(encoding="utf-8")))
    release_document = next(
        document for document in documents
        if isinstance(document, dict) and "releases" in document
    )
    version = _chart_version(base_chart)
    runtime_dir = state_file.parent / ".runtime" / f"openadservice-{version}"
    fetch_dir = runtime_dir / "charts"
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir)
    fetch_dir.mkdir(parents=True)

    for index, release in enumerate(release_document["releases"]):
        chart_ref = release["chart"]
        chart_key = f"release-{index:02d}"
        if chart_ref.rsplit("/", 1)[-1] == "zenoh-router":
            # The local Zenoh parent chart contains the 1.2.0-compatible
            # ConfigMap naming; the published 0.2.10 parent does not.
            local_parent = base_chart.parent / "zenoh-router"
            if not (local_parent / "Chart.yaml").is_file():
                raise RuntimeError(f"local Zenoh parent chart not found: {local_parent}")
            parent_chart = runtime_dir / "local-parent-charts" / chart_key
            shutil.copytree(local_parent, parent_chart)
        else:
            parent_chart = _fetch_parent_chart(
                fetch_dir, chart_key, chart_ref, str(release["version"])
            )
        _replace_dependency(parent_chart, base_chart, version)
        release["chart"] = str(parent_chart)
        release.pop("version", None)
        release["skipDeps"] = True

    for document in documents:
        if isinstance(document, dict):
            _absolute_values_paths(document, state_file.parent)

    overlay_file = runtime_dir / "helmfile.yaml"
    overlay_file.write_text(
        "# generated temporary overlay; rerun prepare_local_openadservice.py to refresh\n"
        + yaml.safe_dump_all(documents, sort_keys=False, explicit_start=True),
        encoding="utf-8",
    )
    deployment_file = runtime_dir / "deployment-helmfile.yaml"
    addons_file = Path(__file__).resolve().parent / "addons" / "helmfile.yaml"
    deployment_file.write_text(
        yaml.safe_dump({"helmfiles": [
            {"path": str(addons_file)},
            {"path": str(overlay_file)},
        ]}, sort_keys=False),
        encoding="utf-8",
    )
    return deployment_file


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    project_dir = script_dir.parents[1]
    parser = argparse.ArgumentParser(
        description="rebuild fetched parent charts with a local openadservice dependency"
    )
    parser.add_argument(
        "--helmfile", type=Path, default=script_dir / "generated" / "helmfile.yaml",
        help="source Helmfile",
    )
    parser.add_argument(
        "--base-chart", type=Path,
        default=project_dir / "openads-helm" / "charts" / "openadservice",
        help="local openadservice chart",
    )
    args = parser.parse_args()
    print(prepare(args.helmfile, args.base_chart))


if __name__ == "__main__":
    main()
