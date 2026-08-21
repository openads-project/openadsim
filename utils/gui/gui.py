"""Streamlit Configuration GUI for simulation platform configuration management."""

from pathlib import Path
import html
import json
import os
import subprocess
import sys
import tempfile
from typing import Any, Optional

import streamlit as st

from checks import validate_config
from control import (
    docker_available,
    list_compose_containers,
    resolve_runtime_repo_root,
    run_process,
    run_processes,
)
from models import SimulationConfig, UiField
from presets import (
    DEFAULT_PRESET_NAME,
    get_preset_names,
    get_preset,
    is_preset_name,
    match_preset_name,
)


APP_DIR = Path(__file__).resolve().parent
LOGO_PATH = APP_DIR / "assets" / "openads.png"
CURRENT_CONFIG_LABEL = "Current Configuration"
PROJECT_DOCS_URL = "https://openads-project.github.io/"
configured_repo_root = os.environ.get("HOST_REPO_PATH", "").strip()
if configured_repo_root:
    container_repo_root = Path(configured_repo_root)
elif Path("/repo").exists():
    container_repo_root = Path("/repo")
else:
    container_repo_root = next((parent for parent in [APP_DIR, *APP_DIR.parents] if (parent / ".git").exists()), APP_DIR)

REPO_ROOT = resolve_runtime_repo_root(container_repo_root)

CONTAINER_ENV_FILE_PATH = Path("/.env")
ENV_FILE_PATH = CONTAINER_ENV_FILE_PATH if CONTAINER_ENV_FILE_PATH.exists() else REPO_ROOT / ".env"


def load_current_config() -> Optional[SimulationConfig]:
    try:
        return SimulationConfig.from_env_file(ENV_FILE_PATH)
    except Exception as exc:
        st.error(f"Error loading current config: {exc}")
        return None


def _to_widget_value(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    return value


def _clear_field_widget_state():
    for state_key in list(st.session_state.keys()):
        if state_key.startswith("field::"):
            del st.session_state[state_key]


def _next_field_revision():
    st.session_state.field_revision = st.session_state.get("field_revision", 0) + 1


def _commit_config_change(config: SimulationConfig, changed_path: str):
    if changed_path != "additional.example_overlay":
        config.additional.example_overlay = ""
    st.session_state.config = config
    st.session_state.editable_config = config.normalized_copy()
    st.session_state.force_current_configuration = True
    _next_field_revision()
    st.rerun()


def _apply_preset_selection():
    selected = st.session_state.get("preset_selector", CURRENT_CONFIG_LABEL)
    if selected == "Default":
        selected = DEFAULT_PRESET_NAME
    if selected == CURRENT_CONFIG_LABEL:
        st.session_state.force_current_configuration = True
        editable_config = st.session_state.get("editable_config")
        if editable_config is not None:
            st.session_state.config = editable_config.normalized_copy()
    else:
        st.session_state.force_current_configuration = False
        current_config = st.session_state.get("config")
        if current_config is not None:
            st.session_state.editable_config = current_config.normalized_copy()
        st.session_state.config = get_preset(selected)

    _clear_field_widget_state()
    _next_field_revision()


def _selected_preset_has_example_overlay() -> bool:
    selected = st.session_state.get("preset_selector", CURRENT_CONFIG_LABEL)
    if selected == "Default":
        selected = DEFAULT_PRESET_NAME
    if selected == CURRENT_CONFIG_LABEL or not is_preset_name(selected):
        return False
    return bool(get_preset(selected).additional.example_overlay.strip())


def _field_widget_key(path: str, disabled: bool) -> Optional[str]:
    if disabled:
        return None
    revision = st.session_state.get("field_revision", 0)
    return f"field::{revision}::{path}"


def _inject_layout_css():
    st.markdown(
        """
<style>
:root {
    --openads-bg: #0b1117;
    --openads-panel: #111a22;
    --openads-panel-soft: #16212b;
    --openads-border: #263440;
    --openads-text: #e5edf4;
    --openads-muted: #93a4b5;
    --openads-accent: #5eead4;
    --openads-danger: #fb7185;
    --openads-warning: #fbbf24;
    --openads-success: #34d399;
}

.stApp {
    background: var(--openads-bg);
}

section[data-testid="stSidebar"] {
    min-width: 238px !important;
    max-width: 238px !important;
    background: #090f14;
    border-right: 1px solid var(--openads-border);
}

section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] span {
    color: var(--openads-text);
}

section[data-testid="stSidebar"] h3 {
    font-size: 0.84rem;
    margin: 0.45rem 0 0.24rem 0;
}

section[data-testid="stSidebar"] hr {
    margin: 0.5rem 0;
}

div[data-testid="stVerticalBlockBorderWrapper"],
div[data-testid="stExpander"] {
    border-color: var(--openads-border) !important;
    background: var(--openads-panel) !important;
}

div[data-testid="stAlert"] {
    border-radius: 8px;
}

.block-container {
    padding-top: 2.9rem;
    padding-bottom: 2rem;
}

.openads-header {
    padding: 0 0 1rem 0;
    border-bottom: 1px solid var(--openads-border);
    margin-bottom: 1.25rem;
    overflow: visible;
}

.openads-title {
    font-size: 1.58rem;
    font-weight: 720;
    line-height: 1.5;
    color: var(--openads-text);
    overflow: visible;
}

.openads-subtitle {
    color: var(--openads-muted);
    margin-top: 0.2rem;
    font-size: 0.92rem;
}

.openads-sidebar-logo {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.45rem;
}

.openads-sidebar-logo img {
    width: 30px;
    height: 30px;
    object-fit: contain;
}

.openads-sidebar-title {
    font-weight: 720;
    color: var(--openads-text);
    font-size: 0.98rem;
}

.openads-sidebar-subtitle {
    color: var(--openads-muted);
    font-size: 0.68rem;
}

.openads-project-links {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.45rem;
    margin: 0.1rem 0 0.45rem 0;
}

.openads-project-links a {
    color: var(--openads-accent) !important;
    text-decoration: none;
}

.openads-docs-badge {
    display: inline-flex;
    width: fit-content;
    align-items: center;
    border: 1px solid #1f766d;
    border-radius: 999px;
    padding: 0.08rem 0.42rem;
    color: var(--openads-accent) !important;
    background: rgba(94, 234, 212, 0.08);
    font-size: 0.68rem;
    font-weight: 650;
}

.openads-host-path {
    color: var(--openads-muted);
    font-size: 0.66rem;
    overflow-wrap: anywhere;
    white-space: normal;
    line-height: 1.25;
    min-width: 0;
}

.openads-section {
    padding: 1rem;
    border: 1px solid var(--openads-border);
    background: var(--openads-panel);
    border-radius: 8px;
    margin-bottom: 1rem;
}

.openads-section h3 {
    color: var(--openads-text);
    font-size: 1.05rem;
    margin: 0 0 0.2rem 0;
}

.openads-section p {
    color: var(--openads-muted);
    margin: 0 0 0.75rem 0;
    font-size: 0.85rem;
}

.openads-section-heading {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    margin: 0 0 0.2rem 0;
}

.openads-section-heading svg {
    flex: 0 0 auto;
}

.openads-section-heading span {
    color: var(--openads-text);
    font-size: 1.18rem;
    font-weight: 700;
}

.openads-section-description {
    color: var(--openads-muted);
    font-size: 0.86rem;
    margin: -0.15rem 0 0.7rem 0;
}

.openads-profile-label {
    display: flex;
    align-items: center;
    gap: 0.38rem;
    margin: 0.16rem 0 0.16rem 0;
    min-height: 1.25rem;
}

.openads-profile-label svg {
    flex: 0 0 auto;
}

.openads-profile-label span,
.openads-field-label span {
    color: var(--openads-text);
    font-size: 0.84rem;
    font-weight: 650;
    line-height: 1.1;
}

.openads-profile-label code,
.openads-field-label code {
    color: var(--openads-muted);
    background: transparent;
    border: 0;
    padding: 0;
    font-size: 0.66rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.openads-field-label {
    display: flex;
    align-items: baseline;
    gap: 0.38rem;
    margin: 0.18rem 0 0.16rem 0;
    min-height: 1.2rem;
}

.openads-footer {
    border-top: 1px solid var(--openads-border);
    color: var(--openads-muted);
    font-size: 0.78rem;
    margin-top: 2rem;
    padding-top: 1rem;
}

.openads-footer a {
    color: var(--openads-accent) !important;
    text-decoration: none;
}

div[data-testid="stSelectbox"] label,
div[data-testid="stTextInput"] label,
div[data-testid="stCheckbox"] label,
div[data-testid="stNumberInput"] label,
div[data-testid="stMultiSelect"] label {
    color: var(--openads-text) !important;
    font-weight: 600;
}

div[data-baseweb="select"] > div,
input,
textarea {
    background-color: #0e171f !important;
    border-color: #2c3a46 !important;
    color: var(--openads-text) !important;
}

button[kind="primary"] {
    background: #0f766e !important;
    border: 1px solid #14b8a6 !important;
}

button {
    border-radius: 7px !important;
}

hr {
    border-color: var(--openads-border);
}
</style>
        """,
        unsafe_allow_html=True,
    )


def _section_title(title: str) -> str:
    return title


def _render_app_header():
    st.markdown(
        """
<div class="openads-header">
  <div class="openads-title">OpenADSim</div>
  <div class="openads-subtitle">Simulation configuration, validation and runtime control</div>
</div>
        """,
        unsafe_allow_html=True,
    )


def _render_sidebar_header():
    logo_html = ""
    if LOGO_PATH.exists():
        import base64

        encoded_logo = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
        logo_html = f'<img src="data:image/png;base64,{encoded_logo}" alt="OpenADS logo" />'
    st.markdown(
        f"""
<div class="openads-sidebar-logo">
  {logo_html}
  <div>
    <div class="openads-sidebar-title">OpenADSim</div>
    <div class="openads-sidebar-subtitle">Configuration GUI</div>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )


def _render_project_info():
    repo_root_path = html.escape(str(REPO_ROOT))
    st.markdown(
        f"""
<div class="openads-project-links">
  <a class="openads-docs-badge" href="{PROJECT_DOCS_URL}" target="_blank">Docs</a>
  <div class="openads-host-path">Host: {repo_root_path}</div>
</div>
        """,
        unsafe_allow_html=True,
    )


def _field_label(field: UiField) -> str:
    return f"{field.label} ({field.env_name})"


def _profile_icon_svg(path: str) -> str:
    icons = {
        "additional.simulation": '<rect x="4" y="5" width="16" height="14" rx="2"/><path d="M8 9h8"/><path d="M8 13h5"/>',
        "additional.traffic_profile": '<path d="M3 17h18"/><path d="M5 17l1.5-6h11L19 17"/><circle cx="8" cy="17" r="2"/><circle cx="16" cy="17" r="2"/><path d="M8 11l2-4h4l2 4"/>',
        "additional.testing_profile": '<path d="M9 3h6"/><path d="M10 3v5l-4 8a4 4 0 0 0 3.6 5h4.8a4 4 0 0 0 3.6-5l-4-8V3"/><path d="M8 16h8"/>',
        "additional.perception_profile": '<path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z"/><circle cx="12" cy="12" r="2.7"/>',
        "additional.planning_profile": '<path d="M4 19V5"/><path d="M4 6c4-3 8 3 12 0v9c-4 3-8-3-12 0"/><path d="M17 19h3"/><path d="M18.5 17.5V20.5"/>',
    }
    paths = icons.get(path)
    if not paths:
        return ""
    return f'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths}</svg>'


def _render_field_label(field: UiField, *, icon: str = ""):
    label = html.escape(field.label)
    env_name = html.escape(field.env_name)
    css_class = "openads-profile-label" if icon else "openads-field-label"
    st.markdown(
        f"""
<div class="{css_class}">
  {icon}
  <span>{label}</span>
  <code>{env_name}</code>
</div>
        """,
        unsafe_allow_html=True,
    )


def _section_icon_svg(title: str) -> str:
    icons = {
        "Vehicle": '<path d="M3 13h18l-2-5H5l-2 5Z"/><path d="M5 13v5"/><path d="M19 13v5"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="18" r="2"/>',
        "Map": '<path d="M9 18 3 21V6l6-3 6 3 6-3v15l-6 3-6-3Z"/><path d="M9 3v15"/><path d="M15 6v15"/>',
        "Scenario": '<path d="M4 5h16v14H4z"/><path d="M8 5v14"/><path d="M16 5v14"/><path d="M4 9h4"/><path d="M16 9h4"/><path d="M4 15h4"/><path d="M16 15h4"/>',
    }
    paths = icons.get(title)
    if not paths:
        return ""
    return f'<svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">{paths}</svg>'


def _render_section_heading(title: str, description: str):
    icon = _section_icon_svg(title)
    if icon:
        description_html = (
            f'<div class="openads-section-description">{html.escape(description)}</div>'
            if description
            else ""
        )
        st.markdown(
            f"""
<div class="openads-section-heading">{icon}<span>{title}</span></div>
{description_html}
            """,
            unsafe_allow_html=True,
        )
    else:
        st.subheader(_section_title(title))
        if description:
            st.caption(description)


def _render_footer():
    st.markdown(
        f"""
<div class="openads-footer">
  Copyright 2026 <a href="https://www.ika.rwth-aachen.de/en/" target="_blank">Institute for Automotive Engineering (ika) at RWTH Aachen University</a>.
</div>
        """,
        unsafe_allow_html=True,
    )


def render_field(
    config: SimulationConfig,
    field: UiField,
    *,
    force_disabled: Optional[bool] = None,
    force_options: Optional[list[str]] = None,
    label_visibility: str = "visible",
    label_icon: str = "",
) -> bool:
    current_value = config.get_value(field.path)
    current_widget_value = _to_widget_value(current_value)
    if field.none_as_empty and current_widget_value is None:
        current_widget_value = ""
    if field.control in {"text", "select_or_text"}:
        if current_widget_value is None:
            current_widget_value = ""
        else:
            current_widget_value = str(current_widget_value)

    label = _field_label(field)
    help_text = field.description
    effective_disabled = field.disabled if force_disabled is None else (field.disabled or force_disabled)
    widget_key = _field_widget_key(field.path, effective_disabled)
    widget_label_visibility = label_visibility
    if label_visibility == "visible":
        _render_field_label(field, icon=label_icon)
        widget_label_visibility = "collapsed"

    if field.control == "select":
        options = list(force_options if force_options is not None else field.options)
        if (
            not field.strict_options
            and current_widget_value not in options
            and current_widget_value not in (None, "")
        ):
            options.insert(0, str(current_widget_value))
        if not options:
            return False

        index = options.index(current_widget_value) if current_widget_value in options else 0
        selectbox_kwargs = {
            "label": label,
            "options": options,
            "index": index,
            "help": help_text,
            "key": widget_key,
            "disabled": effective_disabled,
            "label_visibility": widget_label_visibility,
        }
        if field.empty_option_label:
            selectbox_kwargs["format_func"] = (
                lambda option, label=field.empty_option_label: label if option == "" else option
            )
        new_value = st.selectbox(**selectbox_kwargs)

    elif field.control == "select_or_text":
        options = list(force_options if force_options is not None else field.options)
        if (
            not field.strict_options
            and current_widget_value not in options
            and current_widget_value not in (None, "")
        ):
            options.insert(0, str(current_widget_value))
        if not options:
            options = [""]

        index = options.index(current_widget_value) if current_widget_value in options else 0
        selectbox_kwargs = {
            "label": label,
            "options": options,
            "index": index,
            "help": help_text,
            "key": widget_key,
            "disabled": effective_disabled,
            "label_visibility": widget_label_visibility,
        }
        if field.empty_option_label:
            selectbox_kwargs["format_func"] = (
                lambda option, empty_label=field.empty_option_label: (
                    empty_label if option == "" else option
                )
            )

        new_value = st.selectbox(
            **selectbox_kwargs,
            accept_new_options=not field.strict_options,
        )

    elif field.control == "multiselect":
        options = list(field.options)
        default_values = [value for value in current_widget_value if value in options]
        new_value = st.multiselect(
            label,
            options=options,
            default=default_values,
            help=help_text,
            key=widget_key,
            disabled=effective_disabled,
            label_visibility=widget_label_visibility,
        )

    elif field.control == "number":
        new_value = st.number_input(
            label,
            value=float(current_widget_value),
            format="%.6f",
            help=help_text,
            key=widget_key,
            disabled=effective_disabled,
            label_visibility=widget_label_visibility,
        )

    elif field.control == "bool":
        new_value = st.checkbox(
            label,
            value=bool(current_widget_value),
            help=help_text,
            key=widget_key,
            disabled=effective_disabled,
            label_visibility=widget_label_visibility,
        )

    else:
        new_value = st.text_input(
            label,
            value=str(current_widget_value or ""),
            help=help_text,
            key=widget_key,
            disabled=effective_disabled,
            label_visibility=widget_label_visibility,
        )

    if effective_disabled:
        return False

    if field.control == "multiselect":
        if list(current_widget_value) != list(new_value):
            try:
                config.set_value(field.path, new_value, repo_root=REPO_ROOT)
                _commit_config_change(config, field.path)
                return True
            except ValueError as exc:
                st.caption(f"Invalid value for {field.env_name}: {exc}")
        return False

    if new_value != current_widget_value:
        try:
            config.set_value(field.path, new_value, repo_root=REPO_ROOT)
            _commit_config_change(config, field.path)
            return True
        except ValueError as exc:
            st.caption(f"Invalid value for {field.env_name}: {exc}")

    return False


def _render_sidebar(config: SimulationConfig):
    sidebar_validation = validate_config(config, repo_root=REPO_ROOT)
    command_output = st.session_state.get("runtime_command_output", "")
    command_success = st.session_state.get("runtime_command_success")
    active_preset_name = match_preset_name(config)
    force_current_configuration = st.session_state.get("force_current_configuration", False)
    preset_options = [CURRENT_CONFIG_LABEL] + get_preset_names()
    desired_preset_selection = CURRENT_CONFIG_LABEL if force_current_configuration else (active_preset_name or CURRENT_CONFIG_LABEL)
    if st.session_state.get("preset_selector") != desired_preset_selection:
        st.session_state.preset_selector = desired_preset_selection

    with st.sidebar:
        _render_sidebar_header()
        _render_project_info()
        st.markdown("---")
        st.markdown("### Presets")

        st.selectbox(
            "Preset",
            options=preset_options,
            key="preset_selector",
            on_change=_apply_preset_selection,
            label_visibility="collapsed",
        )

        if active_preset_name and is_preset_name(active_preset_name) and not force_current_configuration:
            st.caption(f"Active preset: {active_preset_name}")
        else:
            st.caption("Editable draft from the current `.env`.")

        st.markdown("---")
        st.markdown("### ENV Verification")

        if sidebar_validation.is_valid:
            normalized = config.normalized_copy()
            desired_content = "".join(normalized.to_env_lines())
            current_content = ""
            if ENV_FILE_PATH.exists():
                current_content = ENV_FILE_PATH.read_text(encoding="utf-8")
            synced = current_content == desired_content
            if current_content != desired_content:
                normalized.write_env_file(ENV_FILE_PATH)
                st.session_state.config = normalized
                synced = True
                st.caption("Updated `.env` from the valid draft.")
            st.success("Draft is valid and synced to `.env`" if synced else "Draft is valid.")
            st.caption(f"ENV file: `{ENV_FILE_PATH}`")
        else:
            st.error("Draft has validation errors. `.env` was not updated.")
            for issue in sidebar_validation.errors:
                st.caption(f"- {issue.path}: {issue.message}")

        st.markdown("---")
        st.markdown("### Runtime Control")

        if not docker_available():
            st.error("Docker CLI is not available in this Configuration GUI environment.")
            return

        start_cmd = ["docker", "compose", "up", "-d"]
        teardown_cmds = [
            ["docker", "compose", "kill"],
            ["docker", "compose", "rm", "--force"],
        ]

        start_disabled = not sidebar_validation.is_valid
        if st.button("Start", type="primary", disabled=start_disabled, use_container_width=True):
            normalized = config.normalized_copy()
            normalized.write_env_file(ENV_FILE_PATH)
            st.session_state.config = normalized
            ok, output = run_process(start_cmd, cwd=REPO_ROOT)
            st.session_state.runtime_command_success = ok
            st.session_state.runtime_command_output = output
            st.rerun()

        if st.button("Stop", use_container_width=True):
            ok, output = run_processes(teardown_cmds, cwd=REPO_ROOT)
            st.session_state.runtime_command_success = ok
            st.session_state.runtime_command_output = output
            st.rerun()

        if command_success is True:
            st.success("Last runtime command succeeded.")
        elif command_success is False:
            st.error("Last runtime command failed.")

        if command_output:
            with st.expander("Last command output", expanded=False):
                st.code(command_output, language="bash")


def _render_profiles_section(
    config: SimulationConfig,
    section_fields: tuple[UiField, ...],
) -> bool:
    fields_by_path = {field.path: field for field in section_fields}
    simulation_field = fields_by_path.get("additional.simulation")
    traffic_field = fields_by_path.get("additional.traffic_profile")
    testing_field = fields_by_path.get("additional.testing_profile")
    perception_field = fields_by_path.get("additional.perception_profile")
    planning_field = fields_by_path.get("additional.planning_profile")
    example_field = fields_by_path.get("additional.example_overlay")
    if not all(
        [
            simulation_field,
            traffic_field,
            testing_field,
            perception_field,
            planning_field,
        ]
    ):
        changed = False
        for field in section_fields:
            changed = render_field(config, field) or changed
        return changed

    changed = False

    left_col, right_col = st.columns(2)
    with left_col:
        changed = _render_profile_field(config, simulation_field) or changed
        changed = _render_profile_field(config, traffic_field) or changed
        changed = _render_profile_field(config, testing_field) or changed
        if config.additional.simulation.value != "carla":
            st.caption("Traffic and testing profiles are only available for CARLA.")

    with right_col:
        changed = _render_profile_field(config, perception_field) or changed
        changed = _render_profile_field(config, planning_field) or changed
        if config.additional.simulation.value != "carla":
            st.caption("The perception profile is only available for CARLA.")
            if config.additional.perception_profile.value != "no-perception":
                config.set_value("additional.perception_profile", "no-perception")
                changed = True
            if config.additional.planning_profile.value != "planning":
                config.set_value("additional.planning_profile", "planning")
                changed = True

    if example_field is not None and _selected_preset_has_example_overlay():
        changed = render_field(config, example_field) or changed

    return changed


def _render_profile_field(config: SimulationConfig, field: UiField) -> bool:
    return render_field(config, field, label_icon=_profile_icon_svg(field.path))


def _render_scenario_import(config: SimulationConfig):
    import_result = st.session_state.pop("scenario_import_result", None)
    if import_result:
        st.success(f"Imported `{import_result['scenario_file']}`")
        with st.expander("Applied scenario adjustments"):
            for action in import_result.get("actions", []):
                st.caption(f"- {action}")
            for warning in import_result.get("warnings", []):
                st.warning(warning)

    with st.expander("Import scenario"):
        st.caption(
            "Upload an OpenSCENARIO file and optionally override its map with an "
            "OpenDRIVE/Lanelet2 pair. If LogicFile references an .xodr, both map "
            "files must be uploaded. Existing import names receive an index suffix."
        )
        revision = st.session_state.get("scenario_import_revision", 0)
        with st.form(f"scenario_import_form::{revision}"):
            import_name = st.text_input(
                "Import name (optional)",
                help="When empty, a UTC timestamp is used.",
            )
            scenario_upload = st.file_uploader(
                "OpenSCENARIO (.xosc)",
                type=["xosc"],
            )
            opendrive_upload = st.file_uploader(
                "OpenDRIVE override (.xodr, optional)",
                type=["xodr"],
            )
            lanelet_upload = st.file_uploader(
                "Lanelet2 map (.osm, required for custom OpenDRIVE)",
                type=["osm"],
            )
            stop_on_route = st.checkbox(
                "Stop when ego route completes",
                value=True,
            )
            timeout = st.number_input(
                "Maximum duration [s]",
                min_value=0.1,
                value=60.0,
                step=1.0,
            )
            driven_distance = st.number_input(
                "Driven-distance criterion [m]",
                min_value=0.1,
                value=30.0,
                step=1.0,
            )
            submitted = st.form_submit_button(
                "Validate and import",
                use_container_width=True,
            )

        if not submitted:
            return
        if scenario_upload is None:
            st.error("Please upload an OpenSCENARIO (.xosc) file.")
            return

        checker = REPO_ROOT / "utils/scenario-checker/scenario_checker.py"
        output_root = REPO_ROOT / "carla-simulation/scenarios/custom-imports"
        if not checker.is_file():
            st.error(f"Scenario checker not found: {checker}")
            return

        try:
            with tempfile.TemporaryDirectory(prefix="openadsim-scenario-import-") as temporary:
                temporary_root = Path(temporary)

                def save_upload(upload) -> Optional[Path]:
                    if upload is None:
                        return None
                    destination = temporary_root / Path(upload.name).name
                    destination.write_bytes(upload.getvalue())
                    return destination

                scenario_path = save_upload(scenario_upload)
                opendrive_path = save_upload(opendrive_upload)
                lanelet_path = save_upload(lanelet_upload)
                assert scenario_path is not None

                command = [
                    sys.executable,
                    str(checker),
                    "import",
                    str(scenario_path),
                    "--output-root",
                    str(output_root),
                    "--repo-root",
                    str(REPO_ROOT),
                    "--name",
                    import_name,
                    "--timeout",
                    str(timeout),
                    "--driven-distance",
                    str(driven_distance),
                    "--json",
                ]
                if opendrive_path:
                    command.extend(["--opendrive", str(opendrive_path)])
                if lanelet_path:
                    command.extend(["--lanelet", str(lanelet_path)])
                if not stop_on_route:
                    command.append("--no-ego-route-stop")

                completed = subprocess.run(
                    command,
                    cwd=REPO_ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                payload = json.loads(completed.stdout or "{}")
        except (OSError, json.JSONDecodeError) as error:
            st.error(f"Scenario import failed: {error}")
            return

        if completed.returncode or not payload.get("valid"):
            errors = payload.get("errors", [])
            st.error("Scenario import failed")
            for error in errors or [completed.stderr.strip() or "Unknown error"]:
                st.caption(f"- {error}")
            return

        map_data = payload["map"]
        if map_data["type"] == "prebuilt":
            config.set_value("map.prebuilt_map", map_data["map_name"])
        else:
            scenario_directory = Path(payload["scenario_file"]).parent

            def imported_map_path(value: str) -> str:
                path = Path(value)
                if not path.is_absolute() and path.parent == Path("."):
                    path = scenario_directory / path
                return path.as_posix()

            config.set_value("map.prebuilt_map", "")
            config.set_value(
                "map.custom_opendrive",
                imported_map_path(map_data["opendrive"]),
            )
            config.set_value(
                "map.custom_lanelet",
                imported_map_path(map_data["lanelet"]),
            )
        config.set_value("scenario.scenario_file", payload["scenario_file"])

        st.session_state.config = config
        st.session_state.editable_config = config.normalized_copy()
        st.session_state.force_current_configuration = True
        st.session_state.scenario_import_result = payload
        st.session_state.scenario_import_revision = revision + 1
        _clear_field_widget_state()
        _next_field_revision()
        st.rerun()


def _render_configuration_tab(config: SimulationConfig):
    config_changed = False
    sections = {section.title: section for section in config.get_ui_sections(REPO_ROOT)}
    force_current_configuration = st.session_state.get("force_current_configuration", False)
    active_preset_name = "" if force_current_configuration else match_preset_name(config)

    profiles_section = sections.get("Profiles")
    additional_section = sections.get("Additional Options")
    vehicle_section = sections.get("Vehicle")
    map_section = sections.get("Map")
    scenario_section = sections.get("Scenario")

    if not all([profiles_section, additional_section, vehicle_section, map_section, scenario_section]):
        for section in sections.values():
            _render_section_heading(section.title, section.description)
            if section.title == "Profiles":
                changed = _render_profiles_section(config, section.fields)
            else:
                changed = False
                for field in section.fields:
                    changed = render_field(config, field) or changed
            config_changed = config_changed or changed
            st.markdown("---")
    else:
        _render_section_heading(profiles_section.title, profiles_section.description)
        changed = _render_profiles_section(config, profiles_section.fields)
        config_changed = config_changed or changed

        st.markdown("---")

        col_vehicle, col_map, col_scenario = st.columns(3)

        with col_vehicle:
            _render_section_heading(vehicle_section.title, vehicle_section.description)
            changed = False
            for field in vehicle_section.fields:
                changed = render_field(config, field) or changed
            config_changed = config_changed or changed

            if config.additional.simulation.value == "sumo":
                st.caption("Sensors are not used for SUMO and are exported as empty.")

        with col_map:
            _render_section_heading(map_section.title, map_section.description)
            changed = False
            for field in map_section.fields:
                changed = render_field(config, field) or changed
            config_changed = config_changed or changed

        with col_scenario:
            _render_section_heading(scenario_section.title, scenario_section.description)
            changed = False
            scenario_enabled = config.additional.simulation.value == "carla"
            for field in scenario_section.fields:
                changed = render_field(config, field) or changed
            config_changed = config_changed or changed

            if scenario_enabled:
                _render_scenario_import(config)

            if not scenario_enabled:
                st.caption("Scenario file is only configurable for manual or automated testing.")

        st.markdown("---")
        _render_section_heading(additional_section.title, additional_section.description)
        changed = False
        for field in additional_section.fields:
            changed = render_field(config, field) or changed
        config_changed = config_changed or changed

    if active_preset_name:
        st.info(f"Preset '{active_preset_name}' is active. Edit any field to switch back to Current Configuration.")

    if config_changed:
        st.session_state.config = config
        st.session_state.editable_config = config.normalized_copy()
        st.session_state.force_current_configuration = True
        _next_field_revision()
        st.rerun()

    st.header("Configuration Validation")
    validation = validate_config(config, repo_root=REPO_ROOT)

    if validation.is_valid:
        st.success("Configuration is valid.")
    else:
        st.error("Configuration validation failed.")
        for issue in validation.errors:
            st.caption(f"- {issue.path}: {issue.message}")

    if validation.warnings:
        st.warning("Validation warnings")
        for issue in validation.warnings:
            st.caption(f"- {issue.path}: {issue.message}")

    with st.expander("Generated ENV"):
        st.json(config.to_env_dict())


def _render_monitoring_tab():
    st.subheader("Container Monitoring")

    if not docker_available():
        st.error("Docker CLI is not available in this Configuration GUI environment.")
        return

    show_all = st.checkbox("Show all containers (-a)", value=False)
    if st.button("Refresh Container List"):
        st.rerun()

    containers = list_compose_containers(repo_root=REPO_ROOT, all_containers=show_all)
    running = [container for container in containers if container["status"].lower().startswith("up")]
    st.caption(f"Containers shown: {len(containers)} | Running: {len(running)}")

    if not containers:
        st.info("No containers found.")
        return

    st.dataframe(containers, use_container_width=True, hide_index=True)


def main():
    st.set_page_config(
        page_title="OpenADSim",
        page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else None,
        layout="wide",
    )
    _inject_layout_css()

    _render_app_header()

    if "config" not in st.session_state:
        current_config = load_current_config()
        if current_config:
            st.session_state.config = current_config
            st.session_state.force_current_configuration = True
        else:
            st.session_state.config = get_preset(DEFAULT_PRESET_NAME)
            st.session_state.force_current_configuration = False
    if "force_current_configuration" not in st.session_state:
        st.session_state.force_current_configuration = True
    if "editable_config" not in st.session_state:
        st.session_state.editable_config = st.session_state.config.normalized_copy()
    if "field_revision" not in st.session_state:
        st.session_state.field_revision = 0

    config: SimulationConfig = st.session_state.config
    _render_sidebar(config)

    active_tab = st.segmented_control(
        "Section",
        options=["Configuration", "Monitoring"],
        selection_mode="single",
        default="Configuration",
        label_visibility="collapsed",
    )

    if active_tab == "Configuration":
        _render_configuration_tab(config)
    else:
        _render_monitoring_tab()

    _render_footer()
    st.session_state.config = config


if __name__ == "__main__":
    main()
