"""DEAD CODE — Neo4j/Cypher graph namespacing.

Unused since T4.4 closed dec-101: the structural code graph moved to SQLite
(``SessionStore`` nodes/edges) and the Neo4j backend was dropped. Kept only
for historical reference; no runtime path imports this module.
"""

from __future__ import annotations

import re


def namespace_prefix(project_slug: str) -> str:
    slug = project_slug.strip().lower().replace("-", "_")
    slug = re.sub(r"[^a-z0-9_]", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    if not slug:
        raise ValueError("project_slug não pode ser vazio")
    return f"{slug}__"


def transform_cypher(cypher: str, project_slug: str) -> str:
    prefix = namespace_prefix(project_slug)
    out: list[str] = []
    quote: str | None = None
    brace_depth = 0
    square_depth = 0
    i = 0
    while i < len(cypher):
        char = cypher[i]
        if quote:
            out.append(char)
            if char == "\\" and i + 1 < len(cypher):
                i += 1
                out.append(cypher[i])
            elif char == quote:
                quote = None
            i += 1
            continue

        if char in {"'", '"'}:
            quote = char
            out.append(char)
            i += 1
            continue
        if char == "{":
            brace_depth += 1
            out.append(char)
            i += 1
            continue
        if char == "}":
            brace_depth = max(0, brace_depth - 1)
            out.append(char)
            i += 1
            continue
        if char == "[":
            square_depth += 1
            out.append(char)
            i += 1
            continue
        if char == "]":
            square_depth = max(0, square_depth - 1)
            out.append(char)
            i += 1
            continue

        if char == ":" and brace_depth == 0 and square_depth == 0:
            match = re.match(r":([A-Za-z_][A-Za-z0-9_]*)", cypher[i:])
            if match:
                label = match.group(1)
                out.append(f":{prefix}{label}" if not label.startswith(prefix) else f":{label}")
                i += len(label) + 1
                continue

        out.append(char)
        i += 1
    return "".join(out)


def _cypher_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _node_name_expr(alias: str) -> str:
    return f"coalesce({alias}.name, {alias}.id, {alias}.full_name, {alias}.symbol)"


def neighbors_query(node: str, project_slug: str, depth: int) -> str:
    prefix = namespace_prefix(project_slug)
    safe_depth = max(1, int(depth))
    node_value = _cypher_string(node)
    prefix_value = _cypher_string(prefix)
    return (
        "MATCH (n) "
        f"WHERE any(label IN labels(n) WHERE label STARTS WITH {prefix_value}) "
        f"AND {_node_name_expr('n')} = {node_value} "
        f"MATCH p=(n)-[*1..{safe_depth}]-(m) "
        "WHERE all(x IN nodes(p) WHERE "
        f"any(label IN labels(x) WHERE label STARTS WITH {prefix_value})) "
        "RETURN "
        f"{_node_name_expr('n')} AS node, "
        f"{_node_name_expr('m')} AS neighbor, "
        "labels(m) AS labels "
        "LIMIT 50"
    )


def path_query(from_node: str, to_node: str, project_slug: str) -> str:
    prefix = namespace_prefix(project_slug)
    from_value = _cypher_string(from_node)
    to_value = _cypher_string(to_node)
    prefix_value = _cypher_string(prefix)
    return (
        "MATCH (from), (to) "
        f"WHERE any(label IN labels(from) WHERE label STARTS WITH {prefix_value}) "
        f"AND any(label IN labels(to) WHERE label STARTS WITH {prefix_value}) "
        f"AND {_node_name_expr('from')} = {from_value} "
        f"AND {_node_name_expr('to')} = {to_value} "
        "MATCH p=shortestPath((from)-[*..10]-(to)) "
        "WHERE all(x IN nodes(p) WHERE "
        f"any(label IN labels(x) WHERE label STARTS WITH {prefix_value})) "
        "RETURN [x IN nodes(p) | "
        "coalesce(x.name, x.id, x.full_name, x.symbol)] AS path "
        "LIMIT 1"
    )
