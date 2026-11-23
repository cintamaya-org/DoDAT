# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only presentation of a DAT's pinned workflow, including historical versions."""

from collections import Counter, defaultdict

from .graph_engine import SATISFIED_STATUSES, attempt_display_tone, refusal_links
from .graph_schema import approval_roles, is_referent_gate, topological_nodes
from .services import ensure_workflow_instance


def _graph_steps(instance, graph):
    from users.models import Role

    spec = instance.definition_version.specification
    if graph:
        nodes = graph["nodes"]
    else:
        attempts = {a.node_id: a for a in instance.node_attempts.filter(is_current=True)}
        names = dict(Role.objects.values_list("slug", "name"))
        nodes = [
            {**node, "attempt": attempts.get(node["id"]), "section_referent": is_referent_gate(node),
             "role_names": [names.get(slug, slug) for slug in approval_roles(node)]}
            for node in topological_nodes(spec)
        ]
    returns = {link["from"]: link["to"] for link in refusal_links(instance)}
    columns = {}
    steps = []
    for node in nodes:
        key = node["id"]
        after = node.get("after", [])
        columns[key] = max((columns[parent] + 1 for parent in after), default=0)
        attempt = node["attempt"]
        status = attempt.status if attempt else "unknown"
        tone = attempt_display_tone(attempt) if attempt else "unknown"
        labels = {"completed": "Terminé", "skipped": "Ignoré", "waiting": "En attente",
                  "ready": "Soumission attendue" if node["type"] == "submission" else "Approbation attendue",
                  "refusal_blocked": "Refusé · reprise requise", "rejected": "Refusé", "cancelled": "Sans suite"}
        label = "Bloqué" if tone == "blocked" and status == "waiting" else labels.get(status, "Non renseigné")
        roles = node["role_names"]
        if node["section_referent"]:
            lane = "Référent de section"
        elif roles:
            lane = roles[0]
        else:
            lane = "Éditeurs autorisés" if node["type"] == "submission" else "Jalons du dossier"
        steps.append({"id": key, "name": node.get("name", key), "after": after, "column": columns[key],
                      "lane": lane, "roles": " + ".join(roles), "status": label, "tone": tone,
                      "return_to": returns.get(key), "detail": node})
    return steps


def _legacy_visual_steps(template, statuses, sections):
    nodes = template["nodes"]
    incoming = defaultdict(list)
    keys = {node["id"]: f"historique-{index}" for index, node in enumerate(nodes, 1)}
    columns = {value: index for index, value in enumerate(sorted({node["col"] for node in nodes}))}
    for node in nodes:
        for target in node["links"]:
            if target in keys:
                incoming[target].append(keys[node["id"]])
    by_section = {row["slug"]: row for row in sections}
    by_id = {step["id"]: step for step in statuses}
    tones = {"validated": "completed", "blocked": "blocked", "review": "current", "in_progress": "current"}
    steps = []
    for node in sorted(nodes, key=lambda item: (item["col"], item["row"])):
        key = node["id"]
        status = by_id.get(key, {})
        section = by_section.get(node["section"], {})
        lane = section.get("responsible_display") or "Dossier"
        steps.append({"id": keys[key], "name": node["title"], "after": incoming[key],
                      "column": columns[node["col"]], "lane": lane, "roles": "",
                      "status": status.get("state_label") or "Non renseigné",
                      "tone": tones.get(status.get("tone"), "unknown"),
                      "description": status.get("description", "")})
    return steps


def _legacy_steps(instance):
    spec = instance.definition_version.specification
    states = sorted(spec["steps"], key=lambda step: step.get("order", 0))
    parents = defaultdict(list)
    for transition in spec.get("transitions", []):
        for source in transition["sources"]:
            incoming = parents[transition["target"]]
            if source not in incoming:
                incoming.append(source)
    steps = []
    for column, state in enumerate(states):
        key = state["status"]
        current = key == instance.current_state
        roles = [role for rule in state.get("permissions", ()) if rule["permission"] == "write" for role in rule["roles"]]
        steps.append({"id": key, "name": state["name"], "after": parents[key], "column": column,
                      "lane": ", ".join(roles) or "Dossier", "roles": ", ".join(roles),
                      "status": "État du dossier" if current else "État du circuit",
                      "tone": "current" if current else "unknown", "description": state.get("description", "")})
    return steps


def _layout(steps, lanes):
    columns = Counter(step["column"] for step in steps)
    lane_slots = Counter((step["lane"], step["column"]) for step in steps)
    bands = []
    tops = {}
    top = 24
    if lanes:
        for lane in dict.fromkeys(step["lane"] for step in steps):
            height = 124 * max(count for (role, _), count in lane_slots.items() if role == lane)
            tops[lane] = top
            bands.append({"label": lane, "top": top, "height": height})
            top += height
    height = top + 24 if lanes else max(320, max(columns.values()) * 154 + 64)
    placed = {}
    slots = Counter()
    for step in steps:
        col = step["column"]
        lane = step["lane"]
        slot = (col, lane) if lanes else col
        x = 176 + col * 222 if lanes else 20 + col * 152
        y = tops[lane] + slots[slot] * 124 + 16 if lanes else (height - columns[col] * 154) // 2 + slots[slot] * 154 + 20
        placed[step["id"]] = {**step, "x": x, "y": y}
        slots[slot] += 1
    width = max(node["x"] for node in placed.values()) + (202 if lanes else 160)
    return {"key": "lanes" if lanes else "stepper", "width": width, "height": height,
            "nodes": list(placed.values()), "edges": _edges(placed, lanes), "bands": bands}


def _edges(placed, lanes):
    edges = []
    for step in placed.values():
        links = [(parent, step["id"], False) for parent in step["after"]]
        if step.get("return_to"):
            links.append((step["id"], step["return_to"], True))
        for source, target, returned in links:
            a, b = placed[source], placed[target]
            if returned or b["column"] <= a["column"]:
                offset = 86 if lanes else 70
                x, y, end_x, end_y = a["x"] + offset, a["y"], b["x"] + offset, b["y"]
                bend = max(2, min(y, end_y) - 40)
                controls = f"{x+25},{bend} {end_x-25},{bend}"
            else:
                x = a["x"] + (172 if lanes else 70)
                end_x = b["x"] + (0 if lanes else 70)
                y, end_y = a["y"] + (44 if lanes else 20), b["y"] + (44 if lanes else 20)
                mid = (x + end_x) // 2
                controls = f"{mid},{y} {mid},{end_y}"
            style = ""
            if returned:
                style = "review-return"
            elif a["tone"] in SATISFIED_STATUSES:
                style = "review-complete-edge" if b["tone"] in SATISFIED_STATUSES else "review-open-edge"
            edges.append({"source": source, "target": target, "style": style, "returned": returned,
                          "path": f"M{x},{y} C{controls} {end_x},{end_y}"})
    return edges


def review_diagram(dat, graph, *, legacy_template=None, statuses=(), sections=()):
    instance = graph["instance"] if graph else ensure_workflow_instance(dat)
    spec = instance.definition_version.specification
    if spec.get("schema_version") == 2:
        steps = _graph_steps(instance, graph)
    elif legacy_template and legacy_template.get("nodes"):
        steps = _legacy_visual_steps(legacy_template, statuses, sections)
    else:
        steps = _legacy_steps(instance)
    return {"name": spec.get("name") or "Circuit de validation", "steps": steps,
            "layouts": [_layout(steps, lanes) for lanes in (False, True)] if steps else [],
            "has_returns": any(step.get("return_to") for step in steps)}
