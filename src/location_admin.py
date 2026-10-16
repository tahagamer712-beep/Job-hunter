"""
Location Tree admin operations (multi-user redesign) — add/move/verify/
delete nodes at any level (side/sub-region/major-area/sub-neighborhood).
Operates directly on data/location_tree.json.

Section 3.2 invariant preserved: promoting a node to 'verified' is always an
explicit admin action — never automatic.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .location_tree import DEFAULT_TREE_PATH


def _load(path: Path = DEFAULT_TREE_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict, path: Path = DEFAULT_TREE_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_nodes(path: Path = DEFAULT_TREE_PATH) -> list:
    return _load(path).get("nodes", [])


def find_node(node_id: str, path: Path = DEFAULT_TREE_PATH) -> Optional[dict]:
    for n in list_nodes(path):
        if n["id"] == node_id:
            return n
    return None


def add_node(node_id: str, parent_id: Optional[str], name_en: str, name_ar: str,
             aliases: list, status: str = "ambiguous", confidence: float = 0.5,
             path: Path = DEFAULT_TREE_PATH) -> dict:
    if find_node(node_id, path) is not None:
        raise ValueError(f"Node id '{node_id}' already exists.")
    if parent_id is not None and find_node(parent_id, path) is None:
        raise ValueError(f"Parent node '{parent_id}' does not exist.")

    data = _load(path)
    entry = {
        "id": node_id, "parent_id": parent_id, "name_ar": name_ar, "name_en": name_en,
        "aliases": list(dict.fromkeys(aliases + [name_en, name_ar])),
        "status": status, "confidence": confidence,
    }
    data["nodes"].append(entry)
    _save(data, path)
    return entry


def _ancestor_ids(node_id: str, nodes_by_id: dict) -> list:
    result = []
    node = nodes_by_id.get(node_id)
    while node and node.get("parent_id"):
        result.append(node["parent_id"])
        node = nodes_by_id.get(node["parent_id"])
    return result


def move_node(node_id: str, new_parent_id: Optional[str], path: Path = DEFAULT_TREE_PATH) -> dict:
    if new_parent_id is not None and find_node(new_parent_id, path) is None:
        raise ValueError(f"Parent node '{new_parent_id}' does not exist.")
    if new_parent_id == node_id:
        raise ValueError("A node cannot be its own parent.")

    data = _load(path)
    nodes_by_id = {n["id"]: n for n in data["nodes"]}
    if new_parent_id is not None and node_id in _ancestor_ids(new_parent_id, nodes_by_id):
        raise ValueError(
            f"Cannot move '{node_id}' under '{new_parent_id}' — '{new_parent_id}' is already "
            f"a descendant of '{node_id}'; this would create a cycle in the tree."
        )

    for n in data["nodes"]:
        if n["id"] == node_id:
            n["parent_id"] = new_parent_id
            _save(data, path)
            return n
    raise ValueError(f"Node '{node_id}' not found.")


def set_status(node_id: str, new_status: str, path: Path = DEFAULT_TREE_PATH) -> dict:
    if new_status not in ("verified", "likely", "ambiguous", "unknown"):
        raise ValueError("Invalid status.")
    data = _load(path)
    for n in data["nodes"]:
        if n["id"] == node_id:
            n["status"] = new_status
            _save(data, path)
            return n
    raise ValueError(f"Node '{node_id}' not found.")


def add_alias(node_id: str, alias: str, path: Path = DEFAULT_TREE_PATH) -> dict:
    data = _load(path)
    for n in data["nodes"]:
        if n["id"] == node_id:
            if alias not in n["aliases"]:
                n["aliases"].append(alias)
                _save(data, path)
            return n
    raise ValueError(f"Node '{node_id}' not found.")


def delete_node(node_id: str, reparent_children_to: Optional[str] = None,
                 path: Path = DEFAULT_TREE_PATH) -> bool:
    data = _load(path)
    children = [n for n in data["nodes"] if n.get("parent_id") == node_id]
    if children and reparent_children_to is None:
        raise ValueError(
            f"Node '{node_id}' has {len(children)} child area(s) — reparent them first "
            f"(pass reparent_children_to) before deleting."
        )
    if reparent_children_to is not None and find_node(reparent_children_to, path) is None:
        raise ValueError(f"reparent_children_to '{reparent_children_to}' does not exist.")

    for n in children:
        n["parent_id"] = reparent_children_to

    before = len(data["nodes"])
    data["nodes"] = [n for n in data["nodes"] if n["id"] != node_id]
    if len(data["nodes"]) == before:
        return False
    _save(data, path)
    return True


def merge_nodes(source_id: str, target_id: str, path: Path = DEFAULT_TREE_PATH) -> dict:
    source = find_node(source_id, path)
    target = find_node(target_id, path)
    if source is None or target is None:
        raise ValueError("Both source and target nodes must exist.")
    if source_id == target_id:
        raise ValueError("Cannot merge a node into itself.")

    data = _load(path)
    nodes_by_id = {n["id"]: n for n in data["nodes"]}
    if source_id in _ancestor_ids(target_id, nodes_by_id):
        raise ValueError(
            f"Cannot merge '{source_id}' into '{target_id}' — '{target_id}' is a descendant "
            f"of '{source_id}'; reparenting its children onto it would create a cycle."
        )

    for n in data["nodes"]:
        if n["id"] == target_id:
            for alias in source["aliases"]:
                if alias not in n["aliases"]:
                    n["aliases"].append(alias)
        if n.get("parent_id") == source_id:
            n["parent_id"] = target_id
    data["nodes"] = [n for n in data["nodes"] if n["id"] != source_id]
    _save(data, path)
    return find_node(target_id, path)
