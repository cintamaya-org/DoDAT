# SPDX-License-Identifier: AGPL-3.0-only
"""Strict, executable-code-free contract for DAT approval graphs."""

from __future__ import annotations

import re

from .exceptions import WorkflowConfigurationError

MAX_NODES = 100
MAX_DEPTH = 12
IDENTIFIER = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
POLICIES = {
    "missing_assignee": "block",
    "on_content_change": "invalidate_affected",
    "request_changes_requires_comment": True,
}
SECTION_REFERENT = "section-referent"


def approval_roles(node):
    requirements = node.get("approvers", {})
    return tuple(requirements.get("all_roles", requirements.get("any_role", ())))


def is_referent_gate(node):
    return node.get("approvers", {}).get("section_referent") is True


def refusal_policy(node):
    return node.get("on_reject", {"mode": "close"})


def referenced_roles(spec):
    return sorted({
        role for node in spec["nodes"]
        for role in (*approval_roles(node), *refusal_policy(node).get("retry_roles", ()))
    })


def topological_nodes(spec):
    pending = {node["id"]: node for node in spec["nodes"]}
    result = []
    done = set()
    while pending:
        ready = [node for node in pending.values() if set(node.get("after", ())) <= done]
        if not ready:
            raise WorkflowConfigurationError("Workflow dependencies contain a cycle.")
        for node in ready:
            result.append(node)
            done.add(node["id"])
            del pending[node["id"]]
    return result


def descendants(spec, node_ids):
    result = set(node_ids)
    for node in topological_nodes(spec):
        if result.intersection(node.get("after", ())):
            result.add(node["id"])
    return result


def _fail(message):
    raise WorkflowConfigurationError(message)


def _keys(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        _fail(f"Invalid {label}: expected an object with keys {sorted(allowed)}.")


def _strings(value, label, *, empty=False):
    if not isinstance(value, list) or (not value and not empty):
        _fail(f"{label} must be {'a' if empty else 'a nonempty'} list.")
    if len(value) > MAX_NODES:
        _fail(f"{label} exceeds {MAX_NODES} identifiers.")
    if any(not isinstance(item, str) or not IDENTIFIER.fullmatch(item) for item in value):
        _fail(f"{label} contains an invalid identifier.")
    if len(value) != len(set(value)):
        _fail(f"{label} contains duplicates.")


def validate_target(target):
    _keys(target, {"dat", "section", "subsection", "part"}, "target")
    if target == {"dat": True} and type(target["dat"]) is bool:
        return
    if "dat" in target or not target.get("section"):
        _fail("Target requires either dat=true or a section identifier.")
    if "part" in target and "subsection" not in target:
        _fail("Part targets require a subsection identifier.")
    for value in target.values():
        if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", value):
            _fail("Invalid target identifier.")


def validate_condition(condition, depth=0):
    if depth > MAX_DEPTH:
        _fail("Readiness expression is too deeply nested.")
    _keys(condition, {"all", "any", "not", "required_content_complete", "no_open_reserves"}, "readiness")
    if len(condition) != 1:
        _fail("Readiness requires exactly one operator.")
    operator, value = next(iter(condition.items()))
    if operator in {"all", "any"}:
        if not isinstance(value, list) or not value or len(value) > MAX_NODES:
            _fail("Readiness groups require a bounded, nonempty list.")
        for child in value:
            validate_condition(child, depth + 1)
    elif operator == "not":
        validate_condition(value, depth + 1)
    elif value is not True:
        _fail(f"{operator} must be true.")


def validate_graph(spec, *, role_slugs=None, targets=None, referent_sections=None):
    """Validate structure before any database write; optional reference checks at publish."""
    _keys(spec, {"schema_version", "engine_version", "code", "name", "policies", "nodes", "complete_when"}, "workflow")
    if type(spec.get("schema_version")) is not int or spec.get("schema_version") != 2 or type(spec.get("engine_version", 1)) is not int or spec.get("engine_version", 1) != 1:
        _fail("Supported workflow schema_version=2, engine_version=1.")
    if not isinstance(spec.get("code"), str) or not IDENTIFIER.fullmatch(spec["code"]):
        _fail("Invalid workflow code.")
    if not isinstance(spec.get("name", spec["code"]), str) or len(spec.get("name", "")) > 128:
        _fail("Workflow name must be a string of at most 128 characters.")
    policies = spec.get("policies", {})
    _keys(policies, POLICIES, "policies")
    if any(value != POLICIES[key] or type(value) is not type(POLICIES[key]) for key, value in policies.items()):
        _fail("Unsupported workflow policy value.")
    nodes = spec.get("nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_NODES:
        _fail(f"Workflow requires 1..{MAX_NODES} nodes.")
    ids = []
    for node in nodes:
        _keys(node, {"id", "name", "type", "target", "after", "approvers", "ready_when", "on_request_changes", "on_reject"}, "node")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not IDENTIFIER.fullmatch(node_id):
            _fail("Invalid node ID.")
        ids.append(node_id)
        if node.get("type") not in {"submission", "approval", "milestone"}:
            _fail(f"Unknown node type at {node_id}.")
        if not isinstance(node.get("name", node_id), str) or len(node.get("name", "")) > 200:
            _fail(f"Invalid node name at {node_id}.")
        validate_target(node.get("target"))
        _strings(node.get("after", []), "after", empty=True)
        if node["type"] == "approval":
            approvers = node.get("approvers")
            _keys(approvers, {"all_roles", "any_role", "section_referent"}, "approvers")
            if len(approvers) != 1:
                _fail("Approval requires all_roles, any_role or section_referent.")
            if "section_referent" in approvers:
                section = node["target"].get("section")
                if not is_referent_gate(node) or not section or section == "validation":
                    _fail("section_referent must be true and requires a section target.")
                if referent_sections is not None and section not in referent_sections:
                    _fail(f"Section {section} has no configurable referent.")
            else:
                _strings(next(iter(approvers.values())), "approval roles")
        elif "approvers" in node:
            _fail("Only approval nodes declare approvers.")
        if "ready_when" in node:
            validate_condition(node["ready_when"])
        if "on_request_changes" in node:
            if node["type"] != "approval":
                _fail("Only approval nodes support request changes.")
            route = node["on_request_changes"]
            _keys(route, {"restart_at"}, "correction route")
            if not isinstance(route.get("restart_at"), str):
                _fail("Correction route requires restart_at.")
        if "on_reject" in node:
            if node["type"] != "approval":
                _fail("Only approval nodes support refusal.")
            route = node["on_reject"]
            _keys(route, {"mode", "restart_at", "retry_roles"}, "refusal policy")
            mode = route.get("mode")
            if mode == "return":
                _keys(route, {"mode", "restart_at"}, "return policy")
                if not isinstance(route.get("restart_at"), str):
                    _fail("Return policy requires restart_at.")
            elif mode == "block":
                _keys(route, {"mode", "retry_roles"}, "block policy")
                _strings(route.get("retry_roles"), "retry roles")
            elif mode == "close":
                _keys(route, {"mode"}, "close policy")
            else:
                _fail("Refusal policy requires mode return, block or close.")
    if len(ids) != len(set(ids)):
        _fail("Node IDs must be unique.")
    for node in nodes:
        if set(node.get("after", ())) - set(ids):
            _fail(f"Unknown dependency at {node['id']}.")
    topological_nodes(spec)
    for node in nodes:
        restart = node.get("on_request_changes", {}).get("restart_at")
        if restart is not None and (restart not in ids or node["id"] not in descendants(spec, {restart})):
            _fail("restart_at must identify this node or one of its ancestors.")
        restart = refusal_policy(node).get("restart_at")
        if restart is not None and (restart == node["id"] or restart not in ids or node["id"] not in descendants(spec, {restart})):
            _fail("Refusal restart_at must identify an ancestor of this node.")
    completion = spec.get("complete_when")
    _keys(completion, {"all"}, "completion")
    if "all" not in completion:
        _fail("Completion requires all.")
    _strings(completion["all"], "completion nodes")
    if set(completion["all"]) - set(ids):
        _fail("Unknown completion node.")
    # Every configured branch must be required by the final outcome.
    required = set(completion["all"])
    for node in reversed(topological_nodes(spec)):
        if node["id"] in required:
            required.update(node.get("after", ()))
    if required != set(ids):
        _fail("Every node must contribute to workflow completion.")
    if role_slugs is not None:
        missing = set(referenced_roles(spec)) - set(role_slugs)
        if missing:
            _fail(f"Workflow references roles that do not exist: {', '.join(sorted(missing))}.")
    if targets is not None:
        for node in nodes:
            target = node["target"]
            if target.get("dat"):
                continue
            key = (target["section"], target.get("subsection"), target.get("part"))
            if key not in targets:
                _fail(f"Unknown content target at {node['id']}: {key}.")


SUMMARY_STATES = (
    ("nouvelle_demande", "Nouveau besoin", "initial", ("editable", "owner_editable")),
    ("en_cours", "En cours", "in_progress", ("editable", "owner_editable", "reviewable")),
    ("reserve", "Corrections attendues", "in_progress", ("editable", "owner_editable", "requires_corrections")),
    ("valider", "Validé", "completed", ("terminal", "approved")),
    ("refuse", "Refusé", "completed", ("terminal", "rejected")),
)

TERMINAL_STATES = frozenset(code for code, _, _, capabilities in SUMMARY_STATES if "terminal" in capabilities)


def summary_metadata(state):
    for code, name, lane, capabilities in SUMMARY_STATES:
        if code == state:
            return {"status": code, "name": name, "lane": lane, "capabilities": capabilities}
    _fail(f"Unknown workflow summary state: {state}.")


def visualization(spec):
    """The displayed dependency graph is derived from the executable JSON graph."""
    depths = {}
    lanes = {}
    nodes = []
    for node in topological_nodes(spec):
        depth = max((depths[parent] + 1 for parent in node.get("after", [])), default=0)
        depths[node["id"]] = depth
        lane = lanes.get(depth, 0)
        lanes[depth] = lane + 1
        nodes.append({
            "id": node["id"], "title": node.get("name", node["id"]),
            "content": ", ".join(approval_roles(node)), "scope": "workflow", "section": node["target"].get("section", ""),
            "variant": "start" if not node.get("after") else "end" if node["id"] in spec["complete_when"]["all"] else "mid",
            "row": depth, "col": lane,
            "links": [child["id"] for child in spec["nodes"] if node["id"] in child.get("after", [])],
        })
    return {"layout": {"height": max(300, (max(depths.values()) + 1) * 100), "padding": 44}, "nodes": nodes}
