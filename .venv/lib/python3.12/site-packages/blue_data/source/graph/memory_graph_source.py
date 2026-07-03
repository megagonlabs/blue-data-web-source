from __future__ import annotations

import uuid
from collections import defaultdict

from blue_data.source.graph.graph_source import GraphSource


class MemoryGraphSource(GraphSource):
    """In-memory graph storage backend for local/dev/test use."""

    def __init__(self, name: str, properties: dict | None = None):
        super().__init__(name, properties=properties)
        self._nodes: dict[str, dict[str, dict]] = defaultdict(dict)
        self._edges: dict[str, dict[str, dict]] = defaultdict(dict)

    # ---------- node operations ----------

    def add_node(self, database, label, properties=None, node_id=None) -> str:
        node_id = node_id or str(uuid.uuid4())
        self._nodes[database][node_id] = {
            "id": node_id,
            "label": label,
            "properties": properties or {},
        }
        return node_id

    def get_node(self, database, node_id) -> dict | None:
        return self._nodes[database].get(node_id)

    def delete_node(self, database, node_id) -> None:
        self._nodes[database].pop(node_id, None)
        self._edges[database] = {
            eid: e for eid, e in self._edges[database].items()
            if e["source_id"] != node_id and e["target_id"] != node_id
        }

    # ---------- edge operations ----------

    def add_edge(self, database, source_id, edge_type, target_id, properties=None) -> str:
        edge_id = str(uuid.uuid4())
        self._edges[database][edge_id] = {
            "id": edge_id,
            "source_id": source_id,
            "edge_type": edge_type,
            "target_id": target_id,
            "properties": properties or {},
        }
        return edge_id

    def delete_edge(self, database, source_id, edge_type, target_id) -> None:
        self._edges[database] = {
            eid: e for eid, e in self._edges[database].items()
            if not (
                e["source_id"] == source_id
                and e["edge_type"] == edge_type
                and e["target_id"] == target_id
            )
        }

    # ---------- traversal ----------

    def get_neighbors(self, database, node_id, edge_type=None, direction="both") -> list[dict]:
        results = []
        for edge in self._edges[database].values():
            if edge_type and edge["edge_type"] != edge_type:
                continue
            if direction in ("out", "both") and edge["source_id"] == node_id:
                neighbor = self.get_node(database, edge["target_id"])
                if neighbor:
                    results.append({"node": neighbor, "edge": edge, "direction": "out"})
            if direction in ("in", "both") and edge["target_id"] == node_id:
                neighbor = self.get_node(database, edge["source_id"])
                if neighbor:
                    results.append({"node": neighbor, "edge": edge, "direction": "in"})
        return results

    # ---------- DataSource interface ----------

    def create_database(self, database, properties=None, overwrite=False) -> dict:
        if database in self._nodes and not overwrite:
            return {"status": "skipped"}
        self._nodes[database] = {}
        self._edges[database] = {}
        return {"status": "success"}

    def fetch_databases(self) -> list[str]:
        return list(set(list(self._nodes.keys()) + list(self._edges.keys())))

    def fetch_database_collections(self, database) -> list[str]:
        return [database]

    def fetch_database_collection_entities(self, database, collection) -> dict:
        label_props: dict[str, dict] = {}
        for node in self._nodes[database].values():
            label = node["label"]
            if label not in label_props:
                label_props[label] = {}
            for k, v in node["properties"].items():
                label_props[label].setdefault(k, type(v).__name__)

        return {
            label: {
                "properties": {},
                "contents": {
                    "attributes": {
                        prop: {"name": prop, "info": {"type": dtype, "values": []}}
                        for prop, dtype in props.items()
                    }
                },
            }
            for label, props in label_props.items()
        }

    def fetch_database_collection_relations(self, database, collection) -> dict:
        relations: dict[str, dict] = {}
        for edge in self._edges[database].values():
            edge_type = edge["edge_type"]
            if edge_type not in relations:
                source_label = (self._nodes[database].get(edge["source_id"]) or {}).get("label", "")
                target_label = (self._nodes[database].get(edge["target_id"]) or {}).get("label", "")
                relations[edge_type] = {
                    "properties": {"source_entity": source_label, "target_entity": target_label},
                    "contents": {"attributes": {}},
                }
        return relations

    def fetch_metadata(self) -> dict:
        return {"name": self.name, "type": "graph"}

    def fetch_database_metadata(self, database) -> dict:
        return {
            "node_count": len(self._nodes.get(database, {})),
            "edge_count": len(self._edges.get(database, {})),
        }

    def fetch_database_schema(self, database) -> dict:
        labels = {n["label"] for n in self._nodes[database].values()}
        edge_types = {e["edge_type"] for e in self._edges[database].values()}
        return {"node_labels": sorted(labels), "edge_types": sorted(edge_types)}

    def fetch_database_collection_metadata(self, database, collection) -> dict:
        return self.fetch_database_metadata(database)

    def create_database_collection(self, database, collection, properties=None) -> dict:
        return {}

    def fetch_source_stats(self) -> dict:
        return {
            "total_nodes": sum(len(n) for n in self._nodes.values()),
            "total_edges": sum(len(e) for e in self._edges.values()),
        }

    def fetch_database_stats(self, database) -> dict:
        return self.fetch_database_metadata(database)

    def fetch_collection_stats(self, database, collection, entities=None, relations=None, sample_limit=None) -> dict:
        return {
            "num_entities": len(entities or []),
            "num_relations": len(relations or []),
            "node_count": len(self._nodes.get(database, {})),
            "edge_count": len(self._edges.get(database, {})),
        }

    def fetch_entity_stats(self, database, collection, entity) -> dict:
        count = sum(1 for n in self._nodes[database].values() if n["label"] == entity)
        return {"row_count": count, "node_count": count}

    def fetch_property_stats(self, database, collection, entity, property_name, sample_limit=None) -> dict:
        from collections import Counter
        values = [
            node["properties"][property_name]
            for node in self._nodes[database].values()
            if node["label"] == entity and property_name in node["properties"]
        ]
        if sample_limit:
            values = values[:sample_limit]
        counts = Counter(str(v) for v in values)
        return {
            "count": len(values),
            "unique_count": len(counts),
            "most_common": [{"value": v, "count": c} for v, c in counts.most_common(10)],
        }
