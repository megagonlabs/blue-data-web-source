from __future__ import annotations

import json
import logging
import os
import uuid

from blue_data.source.graph.graph_source import GraphSource


class FileGraphSource(GraphSource):
    """File-backed graph storage — persists nodes and edges as a JSON file per database."""

    def __init__(self, name: str, directory: str = ".", properties: dict | None = None):
        super().__init__(name, properties=properties or {})
        self.logger = logging.getLogger(self.__class__.__name__)
        self._directory = os.path.abspath(directory)
        os.makedirs(self._directory, exist_ok=True)

    # ---------- persistence helpers ----------

    def _db_path(self, database: str) -> str:
        return os.path.join(self._directory, database + ".json")

    def _load(self, database: str) -> dict:
        path = self._db_path(database)
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {"nodes": {}, "edges": {}}

    def _save(self, database: str, data: dict) -> None:
        with open(self._db_path(database), "w") as f:
            json.dump(data, f)

    # ---------- node operations ----------

    def add_node(self, database, label, properties=None, node_id=None) -> str:
        node_id = node_id or str(uuid.uuid4())
        data = self._load(database)
        data["nodes"][node_id] = {
            "id": node_id,
            "label": label,
            "properties": properties or {},
        }
        self._save(database, data)
        return node_id

    def get_node(self, database, node_id) -> dict | None:
        return self._load(database)["nodes"].get(node_id)

    def delete_node(self, database, node_id) -> None:
        data = self._load(database)
        data["nodes"].pop(node_id, None)
        data["edges"] = {
            eid: e for eid, e in data["edges"].items()
            if e["source_id"] != node_id and e["target_id"] != node_id
        }
        self._save(database, data)

    # ---------- edge operations ----------

    def add_edge(self, database, source_id, edge_type, target_id, properties=None) -> str:
        edge_id = str(uuid.uuid4())
        data = self._load(database)
        data["edges"][edge_id] = {
            "id": edge_id,
            "source_id": source_id,
            "edge_type": edge_type,
            "target_id": target_id,
            "properties": properties or {},
        }
        self._save(database, data)
        return edge_id

    def delete_edge(self, database, source_id, edge_type, target_id) -> None:
        data = self._load(database)
        data["edges"] = {
            eid: e for eid, e in data["edges"].items()
            if not (
                e["source_id"] == source_id
                and e["edge_type"] == edge_type
                and e["target_id"] == target_id
            )
        }
        self._save(database, data)

    # ---------- traversal ----------

    def get_neighbors(self, database, node_id, edge_type=None, direction="both") -> list[dict]:
        data = self._load(database)
        results = []
        for edge in data["edges"].values():
            if edge_type and edge["edge_type"] != edge_type:
                continue
            if direction in ("out", "both") and edge["source_id"] == node_id:
                neighbor = data["nodes"].get(edge["target_id"])
                if neighbor:
                    results.append({"node": neighbor, "edge": edge, "direction": "out"})
            if direction in ("in", "both") and edge["target_id"] == node_id:
                neighbor = data["nodes"].get(edge["source_id"])
                if neighbor:
                    results.append({"node": neighbor, "edge": edge, "direction": "in"})
        return results

    # ---------- DataSource interface ----------

    def create_database(self, database, properties=None, overwrite=False) -> dict:
        path = self._db_path(database)
        if os.path.exists(path) and not overwrite:
            return {"status": "skipped"}
        self._save(database, {"nodes": {}, "edges": {}})
        return {"status": "success"}

    def fetch_databases(self) -> list[str]:
        return [f[:-5] for f in os.listdir(self._directory) if f.endswith(".json")]

    def fetch_database_collections(self, database) -> list[str]:
        return [database]

    def fetch_database_collection_entities(self, database, collection) -> dict:
        data = self._load(database)
        label_props: dict[str, dict] = {}
        for node in data["nodes"].values():
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
        data = self._load(database)
        relations: dict[str, dict] = {}
        for edge in data["edges"].values():
            edge_type = edge["edge_type"]
            if edge_type not in relations:
                source_label = (data["nodes"].get(edge["source_id"]) or {}).get("label", "")
                target_label = (data["nodes"].get(edge["target_id"]) or {}).get("label", "")
                relations[edge_type] = {
                    "properties": {"source_entity": source_label, "target_entity": target_label},
                    "contents": {"attributes": {}},
                }
        return relations

    def fetch_metadata(self) -> dict:
        return {"name": self.name, "type": "graph"}

    def fetch_database_metadata(self, database) -> dict:
        data = self._load(database)
        return {"node_count": len(data["nodes"]), "edge_count": len(data["edges"])}

    def fetch_database_schema(self, database) -> dict:
        data = self._load(database)
        labels = {n["label"] for n in data["nodes"].values()}
        edge_types = {e["edge_type"] for e in data["edges"].values()}
        return {"node_labels": sorted(labels), "edge_types": sorted(edge_types)}

    def fetch_database_collection_metadata(self, database, collection) -> dict:
        return self.fetch_database_metadata(database)

    def create_database_collection(self, database, collection, properties=None) -> dict:
        return {}

    def fetch_source_stats(self) -> dict:
        databases = self.fetch_databases()
        total_nodes = sum(len(self._load(db)["nodes"]) for db in databases)
        total_edges = sum(len(self._load(db)["edges"]) for db in databases)
        return {"total_nodes": total_nodes, "total_edges": total_edges}

    def fetch_database_stats(self, database) -> dict:
        return self.fetch_database_metadata(database)

    def fetch_collection_stats(self, database, collection, entities=None, relations=None, sample_limit=None) -> dict:
        data = self._load(database)
        return {
            "num_entities": len(entities or []),
            "num_relations": len(relations or []),
            "node_count": len(data["nodes"]),
            "edge_count": len(data["edges"]),
        }

    def fetch_entity_stats(self, database, collection, entity) -> dict:
        data = self._load(database)
        count = sum(1 for n in data["nodes"].values() if n["label"] == entity)
        return {"row_count": count, "node_count": count}

    def fetch_property_stats(self, database, collection, entity, property_name, sample_limit=None) -> dict:
        from collections import Counter
        data = self._load(database)
        values = [
            node["properties"][property_name]
            for node in data["nodes"].values()
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
