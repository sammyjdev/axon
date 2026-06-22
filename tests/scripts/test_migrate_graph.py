from __future__ import annotations


class _FakeRepo:
    def __init__(self, nodes=None, edges=None):
        self._nodes = nodes or []
        self._edges = edges or []
        self.added_nodes = []
        self.added_edges = []

    async def all_nodes(self):
        return self._nodes

    async def all_edges(self):
        return self._edges

    async def add_node(self, node_id, node_type, *, label="", payload=None):
        self.added_nodes.append(node_id)

    async def add_edge(self, edge):
        self.added_edges.append((edge.source_id, edge.target_id, edge.type))


async def test_copy_graph_counts() -> None:
    from axon.core.edge import Edge
    from scripts.migrate_graph import copy_graph

    src = _FakeRepo(
        nodes=[{"id": "a", "type": "symbol", "label": "A", "payload": {}}],
        edges=[Edge(source_id="a", target_id="b", type="touches", payload=None)],
    )
    dst = _FakeRepo()
    n, e = await copy_graph(src, dst)
    assert (n, e) == (1, 1)
    assert dst.added_nodes == ["a"]
    assert dst.added_edges == [("a", "b", "touches")]
