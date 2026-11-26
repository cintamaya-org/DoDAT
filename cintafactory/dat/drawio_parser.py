# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import base64
import html
import logging
import zlib
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Tuple
from urllib.parse import unquote
from defusedxml import ElementTree as DefusedElementTree


logger = logging.getLogger(__name__)

MAX_XML_CHARS = 8_000_000
MAX_INFLATED_BYTES = 8_000_000
MAX_MULTIFLUX_ROWS = 10_000

BRIQUE_COLUMNS = ("brique_id", "nom", "description")
FLUX_COLUMNS = (
    "statut",
    "flux_id",
    "source",
    "cible",
    "protocole",
    "port",
    "chiffrement",
    "authentification",
)
_MULTIFLUX_MARKER = "_multiflux"


class MultifluxExpansionLimitExceeded(ValueError):
    pass


def _strip_namespace(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _clean_model_xml(payload: str) -> str | None:
    if not payload:
        return None
    candidate = payload.strip()
    if not candidate:
        return None
    if len(candidate) > MAX_XML_CHARS:
        logger.debug("drawio parser: payload exceeds max size (%s chars)", len(candidate))
        return None
    for transform in (lambda value: value, html.unescape, unquote):
        try:
            transformed = transform(candidate)
        except Exception:
            continue
        if not transformed:
            continue
        if len(transformed) > MAX_XML_CHARS:
            continue
        if transformed.lstrip().startswith("<mxGraphModel"):
            return transformed.strip()
        index = transformed.find("<mxGraphModel")
        if index != -1:
            return transformed[index:].strip()
    return None


def _inflate_drawio_payload(encoded: str) -> str | None:
    if not encoded:
        return None
    try:
        raw = base64.b64decode(encoded)
    except Exception:
        return None
    for wbits in (-15, zlib.MAX_WBITS):
        try:
            decompressor = zlib.decompressobj(wbits)
            inflated = decompressor.decompress(raw, MAX_INFLATED_BYTES + 1)
        except Exception:
            continue
        if len(inflated) > MAX_INFLATED_BYTES or not decompressor.eof:
            continue
        try:
            decoded = inflated.decode("utf-8", errors="ignore")
        except Exception:
            decoded = ""
        if not decoded:
            continue
        cleaned = _clean_model_xml(unquote(decoded))
        if cleaned:
            return cleaned
    return None


def _extract_mxgraph_models(xml_payload: str) -> List[str]:
    if not xml_payload or not isinstance(xml_payload, str):
        return []
    content = xml_payload.strip()
    if not content:
        return []
    if len(content) > MAX_XML_CHARS:
        logger.debug("drawio parser: xml payload exceeds max size (%s chars)", len(content))
        return []
    try:
        root = DefusedElementTree.fromstring(content)
    except DefusedElementTree.ParseError:
        return []
    tag = _strip_namespace(root.tag)
    if tag == "mxGraphModel":
        return [content]
    if tag != "mxfile":
        return []
    models: List[str] = []
    for diagram in root.iter():
        if _strip_namespace(diagram.tag) != "diagram":
            continue
        raw_payload = (diagram.text or "").strip()
        if raw_payload:
            cleaned = _clean_model_xml(raw_payload) or _inflate_drawio_payload(raw_payload)
            if cleaned:
                models.append(cleaned)
                continue
        for child in diagram:
            if _strip_namespace(child.tag) != "mxGraphModel":
                continue
            models.append(DefusedElementTree.tostring(child, encoding="unicode"))
            break
    return models


def extract_drawio_pages(xml_payload: str) -> List[Dict[str, str]]:
    if not xml_payload or not isinstance(xml_payload, str):
        return []
    content = xml_payload.strip()
    if not content:
        return []
    if len(content) > MAX_XML_CHARS:
        logger.debug("drawio parser: xml payload exceeds max size (%s chars)", len(content))
        return []
    try:
        root = DefusedElementTree.fromstring(content)
    except DefusedElementTree.ParseError:
        return []
    tag = _strip_namespace(root.tag)
    if tag == "mxGraphModel":
        return [{"index": 0, "name": "Page 1", "xml": content}]
    if tag != "mxfile":
        return []
    pages: List[Dict[str, str]] = []
    index = 0
    for diagram in root.iter():
        if _strip_namespace(diagram.tag) != "diagram":
            continue
        name = diagram.get("name") or diagram.get("label") or f"Page {index + 1}"
        raw_payload = (diagram.text or "").strip()
        page_xml = ""
        if raw_payload:
            page_xml = _clean_model_xml(raw_payload) or _inflate_drawio_payload(raw_payload) or ""
        if not page_xml:
            for child in diagram:
                if _strip_namespace(child.tag) != "mxGraphModel":
                    continue
                page_xml = DefusedElementTree.tostring(child, encoding="unicode")
                break
        if page_xml:
            pages.append({"index": index, "name": name, "xml": page_xml})
        index += 1
    return pages


def _iter_drawio_objects(model_xml: str) -> List[Dict[str, str]]:
    if not model_xml:
        return []
    if len(model_xml) > MAX_XML_CHARS:
        logger.debug("drawio parser: model xml exceeds max size (%s chars)", len(model_xml))
        return []
    try:
        root = DefusedElementTree.fromstring(model_xml)
    except DefusedElementTree.ParseError as exc:
        logger.debug("drawio parser: invalid xml: %s", exc)
        return []
    objects: List[Dict[str, str]] = []
    for element in root.iter():
        if _strip_namespace(element.tag) != "object":
            continue
        attrs = {key.lower(): value for key, value in element.attrib.items()}
        attrs["objecttype"] = attrs.get("objecttype", "").strip().lower()
        cell = next((child.attrib for child in element if _strip_namespace(child.tag) == "mxCell"), {})
        attrs["source"] = attrs.get("source") or cell.get("source", "")
        attrs["target"] = attrs.get("target") or cell.get("target", "")
        attrs["parent"] = cell.get("parent", "")
        objects.append(attrs)
    return objects


def _page_fluxes(objects: List[Dict[str, str]]) -> Tuple[
    List[Dict[str, str]], List[Tuple[str, List[Dict[str, str]]]]
]:
    cartouches = {
        obj["id"]: (obj.get("label", "").strip().lower(), [])
        for obj in objects
        if obj["objecttype"] == "cartouchemultiflux" and obj.get("id")
    }
    fluxes = []
    for obj in objects:
        if obj["objecttype"] != "flux":
            continue
        cartouche = cartouches.get(obj["parent"])
        if cartouche is None:
            fluxes.append(obj)
        elif obj.get("protocole", "").strip() and obj.get("port", "").strip() and _multiflux_target(obj) is None:
            cartouche[1].append(obj)
    return fluxes, [(label, ports) for label, ports in cartouches.values() if label and ports]


def _multiflux_target(attrs: Dict[str, str]) -> Tuple[str, str] | None:
    protocol = attrs.get("protocole", "").strip().lower()
    if protocol == "multi":
        return "id", attrs.get("idflux", "").strip().lower()
    if protocol.startswith("#"):
        return "tag", protocol
    return None


def _split_numeric_suffix(value: str) -> Tuple[str, str]:
    prefix = value.rstrip("0123456789")
    return prefix, value[len(prefix):]


def _numeric_suffix_key(suffix: str) -> Tuple[int, str]:
    return len(suffix), suffix


def _cartouche_targets(
    label: str, suffixes_by_prefix: Dict[Tuple[str, int], List[str]]
) -> Iterable[Tuple[str, str]]:
    if label.startswith("#"):
        if len(label) > 1:
            yield "tag", label
        return
    if ".." not in label:
        yield "id", label
        return

    start, end = (value.strip() for value in label.split("..", 1))
    prefix, start_suffix = _split_numeric_suffix(start)
    end_prefix, end_suffix = _split_numeric_suffix(end)
    if prefix != end_prefix or not start_suffix or not end_suffix or ".." in end:
        return
    width = len(start_suffix)
    if end_suffix != (end_suffix.lstrip("0") or "0").zfill(width):
        return
    lower = _numeric_suffix_key(start_suffix)
    upper = _numeric_suffix_key(end_suffix)
    # An ID is either unpadded or padded to exactly the width of the first bound.
    for padding in (0, width):
        suffixes = suffixes_by_prefix.get((prefix, padding), [])
        first = bisect_left(suffixes, lower, key=_numeric_suffix_key)
        last = bisect_right(suffixes, upper, key=_numeric_suffix_key)
        for index in range(first, last):
            yield "id", prefix + suffixes[index]


def _resolve_multiflux_ports(
    cartouches: List[Tuple[str, List[Dict[str, str]]]],
    target_counts: Counter[Tuple[str, str]],
) -> Dict[Tuple[str, str], List[Dict[str, str]]]:
    if not cartouches or not target_counts:
        return {}

    suffixes_by_prefix: Dict[Tuple[str, int], List[str]] = defaultdict(list)
    for kind, value in target_counts:
        if kind == "id":
            prefix, suffix = _split_numeric_suffix(value)
            if suffix:
                padding = len(suffix) if len(suffix) > 1 and suffix.startswith("0") else 0
                suffixes_by_prefix[prefix, padding].append(suffix)
    for suffixes in suffixes_by_prefix.values():
        suffixes.sort(key=_numeric_suffix_key)

    matches: Dict[Tuple[str, str], List[Dict[str, str]]] = defaultdict(list)
    remaining = MAX_MULTIFLUX_ROWS
    for label, ports in cartouches:
        for target in _cartouche_targets(label, suffixes_by_prefix):
            count = target_counts[target]
            if count:
                remaining -= count * len(ports)
                if remaining < 0:
                    raise MultifluxExpansionLimitExceeded
                matches[target].extend(ports)
    return matches


def _build_brique_row(attrs: Dict[str, str]) -> Dict[str, str]:
    row = {key: "" for key in BRIQUE_COLUMNS}
    row["brique_id"] = attrs.get("idbrique") or ""
    row["nom"] = attrs.get("labelbrique") or ""
    row["description"] = attrs.get("commentaire") or attrs.get("description") or ""
    return row


def _build_flux_row(attrs: Dict[str, str], object_index: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    row = {key: "" for key in FLUX_COLUMNS}
    row["flux_id"] = attrs.get("idflux") or ""
    row["port"] = attrs.get("port") or ""
    protocole = attrs.get("protocole") or ""
    row["protocole"] = protocole
    protocole_norm = protocole.strip().lower()
    if protocole_norm == "https":
        row["chiffrement"] = "oui"
    elif protocole_norm == "http":
        row["chiffrement"] = "non"
    mecanisme_auth = (attrs.get("mecanismeauth") or "").strip().lower()
    if mecanisme_auth == "certificat":
        row["authentification"] = "oui"
    source_id = attrs.get("source") or ""
    target_id = attrs.get("target") or ""
    source_obj = object_index.get(source_id, {})
    target_obj = object_index.get(target_id, {})
    row["source"] = source_obj.get("labelbrique") or source_obj.get("idbrique") or ""
    row["cible"] = target_obj.get("labelbrique") or target_obj.get("idbrique") or ""
    return row


def _merge_rows(existing: Dict[str, str], incoming: Dict[str, str], columns: Iterable[str]) -> Dict[str, str]:
    merged = dict(existing)
    for column in columns:
        current = merged.get(column) or ""
        if current:
            continue
        candidate = incoming.get(column) or ""
        if candidate:
            merged[column] = candidate
    return merged


def _brique_key(row: Dict[str, str]) -> tuple:
    brique_id = (row.get("brique_id") or "").strip().lower()
    if brique_id:
        return ("id", brique_id)
    nom = (row.get("nom") or "").strip().lower()
    if nom:
        return ("nom", nom)
    description = (row.get("description") or "").strip().lower()
    if description:
        return ("desc", description)
    return ("row", "")


def _flux_key(row: Dict[str, str]) -> tuple:
    flux_id = (row.get("flux_id") or "").strip().lower()
    multiflux = row.get(_MULTIFLUX_MARKER)
    if flux_id and not multiflux:
        return ("id", flux_id)
    source = (row.get("source") or "").strip().lower()
    cible = (row.get("cible") or "").strip().lower()
    protocole = (row.get("protocole") or "").strip().lower()
    port = (row.get("port") or "").strip().lower()
    if multiflux:
        return ("multiflux", flux_id, source, cible, protocole, port)
    if source or cible or protocole or port:
        return ("tuple", source, cible, protocole, port)
    return ("row", "")


def _dedupe_rows(
    rows: List[Dict[str, str]], columns: Iterable[str], key_fn
) -> List[Dict[str, str]]:
    deduped: List[Dict[str, str]] = []
    index: Dict[tuple, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = key_fn(row)
        if key in index:
            existing_index = index[key]
            deduped[existing_index] = _merge_rows(deduped[existing_index], row, columns)
            continue
        index[key] = len(deduped)
        deduped.append(dict(row))
    return deduped


def dedupe_architecture_rows(
    briques: List[Dict[str, str]], fluxes: List[Dict[str, str]]
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    deduped_fluxes = _dedupe_rows(fluxes, FLUX_COLUMNS, _flux_key)
    for row in deduped_fluxes:
        row.pop(_MULTIFLUX_MARKER, None)
    return _dedupe_rows(briques, BRIQUE_COLUMNS, _brique_key), deduped_fluxes


def parse_architecture_diagram(
    xml_payload: str, *, deduplicate: bool = True
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Parse one draw.io file, keeping internal row identity when aggregating files."""
    briques: List[Dict[str, str]] = []
    fluxes: List[Dict[str, str]] = []
    pages = []
    cartouches = []
    target_counts: Counter[Tuple[str, str]] = Counter()
    for model_xml in _extract_mxgraph_models(xml_payload):
        objects = _iter_drawio_objects(model_xml)
        object_index = {obj["id"]: obj for obj in objects if obj.get("id")}
        page_fluxes, page_cartouches = _page_fluxes(objects)
        cartouches.extend(page_cartouches)
        briques.extend(_build_brique_row(obj) for obj in objects if obj["objecttype"] == "brique")
        flows = [(obj, _multiflux_target(obj)) for obj in page_fluxes]
        target_counts.update(target for _, target in flows if target is not None)
        pages.append((object_index, flows))

    ports_by_target = _resolve_multiflux_ports(cartouches, target_counts)
    for object_index, flows in pages:
        for obj, target in flows:
            if target is None:
                fluxes.append(_build_flux_row(obj, object_index))
                continue
            for port in ports_by_target.get(target, []):
                row = _build_flux_row(
                    dict(
                        obj,
                        protocole=port["protocole"],
                        port=port["port"],
                        mecanismeauth=port.get("mecanismeauth", "").strip() or obj.get("mecanismeauth", ""),
                    ),
                    object_index,
                )
                row[_MULTIFLUX_MARKER] = "1"
                fluxes.append(row)
    if deduplicate:
        return dedupe_architecture_rows(briques, fluxes)
    return briques, fluxes
