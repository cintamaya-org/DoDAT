# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import json
import re
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from cintafactory.conf_utils import ensure_conf_dir
from workflows.definitions import DAT_WORKFLOW_VISUALIZATION
from workflows.exceptions import WorkflowError
from workflows.services import ensure_workflow_instance

DEFAULT_WORKFLOW_TEMPLATE: dict[str, Any] = deepcopy(DAT_WORKFLOW_VISUALIZATION)

_config_lock = threading.Lock()
_config_cache: dict[str, Any] | None = None
_config_mtime: float | None = None
_PLACEHOLDER_PATTERN = re.compile(r"{{\s*([a-zA-Z0-9_.]+)\s*}}")


def _config_path() -> Path:
    conf_dir = ensure_conf_dir()
    return conf_dir / "dat_viewflow_template.json"


def ensure_dat_viewflow_template_exists() -> None:
    path = _config_path()
    if path.exists():
        return
    path.write_text(json.dumps(DEFAULT_WORKFLOW_TEMPLATE, indent=2) + "\n")


def load_dat_viewflow_template() -> dict[str, Any]:
    global _config_cache, _config_mtime

    path = _config_path()
    if not path.exists():
        ensure_dat_viewflow_template_exists()

    try:
        stat = path.stat()
    except OSError:
        return _normalize_template(DEFAULT_WORKFLOW_TEMPLATE)

    with _config_lock:
        if _config_cache is None or _config_mtime != stat.st_mtime:
            try:
                raw = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                raw = {}
            _config_cache = _normalize_template(raw)
            _config_mtime = stat.st_mtime
        return json.loads(json.dumps(_config_cache))


def build_dat_viewflow_template(dat, workflow_process=None) -> dict[str, Any]:
    try:
        workflow_instance = ensure_workflow_instance(dat)
        spec = workflow_instance.definition_version.specification
        if spec.get("schema_version") == 2:
            from workflows.graph_schema import visualization
            raw_template = visualization(spec)
        else:
            raw_template = spec.get("visualization") or {}
        template = _normalize_template(raw_template)
    except WorkflowError:
        # Compatibility fallback for deployments before workflow sync has run.
        template = load_dat_viewflow_template()
    overrides = {}
    workflow_data = {}

    if workflow_process is not None:
        raw_overrides = getattr(workflow_process, "workflow_config", {}) or {}
        raw_data = getattr(workflow_process, "workflow_data", {}) or {}
        if isinstance(raw_overrides, dict):
            overrides = raw_overrides
        if isinstance(raw_data, dict):
            workflow_data = raw_data

    merged = _apply_workflow_overrides(template, overrides)
    context = {
        "dat": _build_dat_context(dat),
        "workflow": workflow_data,
    }
    return _render_template_values(merged, context=context)


def _normalize_template(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}

    raw_layout = raw.get("layout")
    if not isinstance(raw_layout, dict):
        raw_layout = {}

    height = raw_layout.get("height", DEFAULT_WORKFLOW_TEMPLATE["layout"]["height"])
    padding = raw_layout.get("padding", DEFAULT_WORKFLOW_TEMPLATE["layout"]["padding"])
    try:
        height = int(height)
    except (TypeError, ValueError):
        height = DEFAULT_WORKFLOW_TEMPLATE["layout"]["height"]
    try:
        padding = int(padding)
    except (TypeError, ValueError):
        padding = DEFAULT_WORKFLOW_TEMPLATE["layout"]["padding"]
    if height < 180:
        height = 180
    if padding < 0:
        padding = 0

    raw_nodes = raw.get("nodes")
    if not isinstance(raw_nodes, list):
        raw_nodes = []

    nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for idx, entry in enumerate(raw_nodes):
        if not isinstance(entry, dict):
            continue
        node_id = str(entry.get("id") or f"step-{idx + 1}").strip()
        title = str(entry.get("title") or node_id).strip()
        variant = str(entry.get("variant") or "mid").strip().lower()
        if variant not in {"start", "mid", "end"}:
            variant = "mid"
        content = str(entry.get("content") or "").strip()
        scope = str(entry.get("scope") or "display").strip().lower()
        if scope not in {"section", "workflow", "display"}:
            scope = "display"
        section = str(entry.get("section") or "").strip()
        nodes.append(
            {
                "id": node_id,
                "title": title,
                "content": content,
                "variant": variant,
                "row": idx // 3,
                "col": idx % 3,
                "links": [],
                "scope": scope,
                "section": section,
            }
        )
        node_ids.add(node_id)

    for idx, entry in enumerate(raw_nodes):
        if idx >= len(nodes):
            continue
        if not isinstance(entry, dict):
            continue
        row = entry.get("row", nodes[idx]["row"])
        col = entry.get("col", nodes[idx]["col"])
        try:
            row = int(row)
        except (TypeError, ValueError):
            row = nodes[idx]["row"]
        try:
            col = int(col)
        except (TypeError, ValueError):
            col = nodes[idx]["col"]
        if row < 0:
            row = 0
        if col < 0:
            col = 0
        nodes[idx]["row"] = row
        nodes[idx]["col"] = col
        raw_links = entry.get("links")
        if not isinstance(raw_links, list):
            raw_links = []
        cleaned_links: list[str] = []
        for link in raw_links:
            link_id = str(link).strip()
            if not link_id:
                continue
            if link_id not in node_ids:
                continue
            if link_id == nodes[idx]["id"]:
                continue
            cleaned_links.append(link_id)
        nodes[idx]["links"] = cleaned_links

    if not nodes:
        nodes = [dict(entry) for entry in DEFAULT_WORKFLOW_TEMPLATE["nodes"]]

    return {
        "layout": {
            "height": height,
            "padding": padding,
        },
        "nodes": nodes,
    }


def _apply_workflow_overrides(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    template = _normalize_template(base)
    if not isinstance(overrides, dict):
        return template

    raw_layout = overrides.get("layout")
    if isinstance(raw_layout, dict):
        if "height" in raw_layout:
            try:
                template["layout"]["height"] = max(180, int(raw_layout["height"]))
            except (TypeError, ValueError):
                pass
        if "padding" in raw_layout:
            try:
                template["layout"]["padding"] = max(0, int(raw_layout["padding"]))
            except (TypeError, ValueError):
                pass

    raw_nodes = overrides.get("nodes")
    if not isinstance(raw_nodes, dict):
        return template

    by_id = {node["id"]: node for node in template["nodes"]}
    for node_id, node_override in raw_nodes.items():
        if node_id not in by_id:
            continue
        if not isinstance(node_override, dict):
            continue
        node = by_id[node_id]
        if "title" in node_override:
            node["title"] = str(node_override["title"] or "").strip() or node["title"]
        if "content" in node_override:
            node["content"] = str(node_override["content"] or "").strip()
        if "variant" in node_override:
            variant = str(node_override["variant"] or "").strip().lower()
            if variant in {"start", "mid", "end"}:
                node["variant"] = variant
        # Keep layout coordinates and graph links sourced from the template file.
        # DAT-level workflow_config can customize labels/content, but should not
        # override node placement; this guarantees template layout updates apply.

    return template


def _build_dat_context(dat) -> dict[str, Any]:
    application = getattr(dat, "application", None)
    owner = getattr(dat, "owner", None)
    return {
        "id": str(getattr(dat, "pk", "")),
        "reference": getattr(dat, "reference", "") or "",
        "title": getattr(dat, "title", "") or "",
        "status": getattr(dat, "status", "") or "",
        "status_label": getattr(dat, "get_status_display", lambda: "")() or "",
        "application_name": getattr(application, "name", "") if application else "",
        "owner_username": getattr(owner, "username", "") if owner else "",
    }


def _render_template_values(template: dict[str, Any], *, context: dict[str, Any]) -> dict[str, Any]:
    rendered = json.loads(json.dumps(template))
    for node in rendered.get("nodes", []):
        if isinstance(node.get("title"), str):
            node["title"] = _render_text(node["title"], context=context)
        if isinstance(node.get("content"), str):
            node["content"] = _render_text(node["content"], context=context)
    return rendered


def _render_text(value: str, *, context: dict[str, Any]) -> str:
    def _replace(match: re.Match[str]) -> str:
        path = match.group(1)
        resolved = _resolve_path(context, path)
        if resolved is None:
            return ""
        return str(resolved)

    return _PLACEHOLDER_PATTERN.sub(_replace, value)


def _resolve_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current
