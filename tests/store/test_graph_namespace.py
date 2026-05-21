from __future__ import annotations

from axon.store.graph_namespace import neighbors_query, path_query, transform_cypher


def test_transform_cypher_adds_prefix_to_simple_labels() -> None:
    cypher = "CREATE (f:Function {name: 'main'}) RETURN f"

    transformed = transform_cypher(cypher, "rpg-master-ai")

    assert ":rpg_master_ai__Function" in transformed


def test_transform_cypher_adds_prefix_to_multiple_labels() -> None:
    cypher = "MERGE (m:Module:File {name: 'app.py'})-[:CONTAINS]->(f:Function)"

    transformed = transform_cypher(cypher, "prometheus")

    assert ":prometheus__Module:prometheus__File" in transformed
    assert ":prometheus__Function" in transformed
    assert "[:CONTAINS]" in transformed


def test_transform_cypher_does_not_change_properties() -> None:
    cypher = "CREATE (f:Function {kind: 'Function', label: 'Module', path: 'src/app.py'})"

    transformed = transform_cypher(cypher, "rpg-master-ai")

    assert "{kind: 'Function', label: 'Module', path: 'src/app.py'}" in transformed
    assert "rpg_master_ai__Module" not in transformed


def test_neighbors_query_filters_by_namespace() -> None:
    query = neighbors_query("main", "rpg-master-ai", 2)

    assert "rpg_master_ai__" in query
    assert "STARTS WITH 'rpg_master_ai__'" in query
    assert "[*1..2]" in query
    assert "'main'" in query


def test_path_query_filters_by_namespace() -> None:
    query = path_query("Controller", "Service", "prometheus")

    assert "STARTS WITH 'prometheus__'" in query
    assert "shortestPath" in query
    assert "'Controller'" in query
    assert "'Service'" in query


def test_project_namespaces_are_distinct_in_queries() -> None:
    rpg_query = neighbors_query("main", "rpg-master-ai", 1)
    prometheus_query = neighbors_query("main", "prometheus", 1)

    assert "rpg_master_ai__" in rpg_query
    assert "prometheus__" not in rpg_query
    assert "prometheus__" in prometheus_query
    assert "rpg_master_ai__" not in prometheus_query
