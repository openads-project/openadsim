"""Runtime control helpers for Docker Compose actions triggered by the Configuration GUI."""

import os
from pathlib import Path
import subprocess
from typing import Optional


def run_process(cmd: list[str], cwd: Optional[Path] = None, timeout_seconds: int = 600) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        return False, str(exc)
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout_seconds}s: {' '.join(cmd)}"

    output = (completed.stdout or "").strip()
    error = (completed.stderr or "").strip()
    merged = "\n".join(part for part in [output, error] if part).strip()
    return completed.returncode == 0, merged


def run_processes(
    commands: list[list[str]],
    cwd: Optional[Path] = None,
    timeout_seconds: int = 600,
) -> tuple[bool, str]:
    results: list[str] = []

    for cmd in commands:
        ok, output = run_process(cmd, cwd=cwd, timeout_seconds=timeout_seconds)
        command_text = " ".join(cmd)
        if output:
            results.append(f"$ {command_text}\n{output}")
        else:
            results.append(f"$ {command_text}")
        if not ok:
            return False, "\n\n".join(results)

    return True, "\n\n".join(results)


def docker_available() -> bool:
    ok, _ = run_process(["docker", "version", "--format", "{{.Client.Version}}"], timeout_seconds=20)
    return ok


def _is_gui_container(row: dict[str, str]) -> bool:
    service = row.get("service", "").strip()
    name = row.get("name", "").strip()
    container_id = row.get("id", "").strip()
    current_container_id = os.environ.get("HOSTNAME", "").strip()

    if service == "gui":
        return True
    if name == "openadsim-gui":
        return True
    if current_container_id and container_id.startswith(current_container_id):
        return True
    return False


def list_compose_containers(
    repo_root: Path,
    all_containers: bool = False,
    include_gui: bool = False,
) -> list[dict[str, str]]:
    cmd = ["docker", "compose", "ps"]
    if all_containers:
        cmd.append("--all")
    cmd.extend(
        [
            "--format",
            "{{.ID}}\t{{.Name}}\t{{.Service}}\t{{.Status}}\t{{.Image}}\t{{.Publishers}}",
        ]
    )
    ok, output = run_process(cmd, cwd=repo_root, timeout_seconds=30)
    if not ok or not output:
        return []

    rows: list[dict[str, str]] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        row = {
            "id": parts[0],
            "name": parts[1],
            "service": parts[2],
            "status": parts[3],
            "image": parts[4],
            "ports": parts[5],
        }
        if include_gui or not _is_gui_container(row):
            rows.append(row)
    return rows


def running_compose_containers(repo_root: Path) -> list[dict[str, str]]:
    return list_compose_containers(repo_root=repo_root, all_containers=False, include_gui=False)


def _discover_mount_source(destination: Path) -> Optional[Path]:
    """Resolve host-side bind source for a destination mounted in this container."""
    container_ref = os.environ.get("HOSTNAME", "").strip()
    if not container_ref:
        return None

    ok, output = run_process(
        [
            "docker",
            "inspect",
            "--format",
            "{{range .Mounts}}{{println .Destination \"\\t\" .Source}}{{end}}",
            container_ref,
        ],
        timeout_seconds=20,
    )
    if not ok or not output:
        return None

    wanted = str(destination)
    for line in output.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        mount_dst, mount_src = parts[0].strip(), parts[1].strip()
        if mount_dst == wanted and mount_src:
            return Path(mount_src)
    return None


def resolve_runtime_repo_root(container_repo_root: Path) -> Path:
    """
    Return a path usable as compose cwd when running against host Docker socket.

    When Compose runs inside the Configuration GUI container, bind sources must be real host paths.
    We therefore resolve the host source mounted at `container_repo_root` and create
    a local symlink alias to it when needed.
    """
    host_repo_root = _discover_mount_source(container_repo_root)
    if host_repo_root is None:
        return container_repo_root

    if host_repo_root.exists():
        return host_repo_root

    try:
        host_repo_root.parent.mkdir(parents=True, exist_ok=True)
        if host_repo_root.is_symlink():
            if host_repo_root.resolve() == container_repo_root.resolve():
                return host_repo_root
            return container_repo_root
        if host_repo_root.exists():
            return container_repo_root
        host_repo_root.symlink_to(container_repo_root.resolve(), target_is_directory=True)
        return host_repo_root
    except OSError:
        return container_repo_root
