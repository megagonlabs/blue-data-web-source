from __future__ import annotations

import json
import logging
import os
import shutil

import networkx as nx
from pgmpy.inference import VariableElimination

from blue_data.source.network.network_source import NetworkSource
from blue_data.source.network.bn.bn_utils import load_pgmpy_model
from blue_data.core.schema import DataSchema


class BayesianNetworkSource(NetworkSource):
    """Bayesian network source — probabilistic graphical model backend using pgmpy."""

    def __init__(self, name: str, properties: dict | None = None):
        super().__init__(name, properties=properties or {})
        self.logger = logging.getLogger(self.__class__.__name__)
        self._cache: dict = {}
        self._initialize_connection_properties()

    # ---------- connection ----------

    def _initialize_connection_properties(self):
        connection = self.properties.get("connection", {})
        connection.setdefault("protocol", "bn")
        connection.setdefault("variant", "pgmpy")
        connection.setdefault("source_directory", ".")
        self.properties["connection"] = connection

    def _disconnect(self):
        self._cache.clear()

    # ---------- path helpers ----------

    def _get_source_directory(self) -> str:
        source_directory = self.properties["connection"].get("source_directory", ".")
        absolute = os.path.abspath(source_directory)
        os.makedirs(absolute, exist_ok=True)
        return absolute

    def _get_database_path(self, database: str) -> str:
        return os.path.join(self._get_source_directory(), database or "default")

    def _get_collection_path(self, database: str, collection: str) -> str:
        return os.path.join(self._get_database_path(database), collection)

    def _get_database_collections(self, database: str) -> list[str]:
        database_path = self._get_database_path(database)
        if not os.path.exists(database_path):
            return []
        return sorted(
            item for item in os.listdir(database_path)
            if os.path.isdir(os.path.join(database_path, item))
            and os.path.exists(os.path.join(database_path, item, "model.bif"))
        )

    # ---------- model loading / caching ----------

    def _load_model(self, database: str, collection: str):
        db = database or "default"
        self._cache.setdefault(db, {})
        if collection in self._cache[db]:
            return self._cache[db][collection]["model"]

        collection_path = self._get_collection_path(db, collection)
        if not os.path.exists(collection_path):
            raise FileNotFoundError(f"BN model not found: {collection_path}")

        model = load_pgmpy_model(collection_path)

        gs_path = os.path.join(collection_path, "graph_structure.json")
        graph_structure = None
        if os.path.exists(gs_path):
            with open(gs_path) as f:
                graph_structure = json.load(f)

        self._cache[db][collection] = {"model": model, "graph_structure": graph_structure}
        return model

    def _build_graph_structure_from_model(self, model) -> dict:
        gs: dict = {"description": "", "nodes": {}, "edges": {}, "markov_blanket": {}}

        for node in model.nodes():
            cpd = model.get_cpds(node)
            states = cpd.state_names.get(node, [])
            gs["nodes"][node] = {
                "name": node,
                "states": states,
                "description": "",
                "states_description": {s: "" for s in states},
            }

        for parent, child in model.edges():
            gs["edges"].setdefault(parent, []).append(child)

        for node in model.nodes():
            mb = model.get_markov_blanket(node)
            gs["markov_blanket"][node] = list(mb) if mb else []

        return gs

    def _load_graph_structure(self, database: str, collection: str) -> dict:
        db = database or "default"
        self._cache.setdefault(db, {})

        if collection in self._cache[db]:
            gs = self._cache[db][collection].get("graph_structure")
            if gs:
                return gs

        model = self._load_model(db, collection)
        gs = self._cache[db][collection].get("graph_structure")

        if not gs:
            gs = self._build_graph_structure_from_model(model)
            self._cache[db][collection]["graph_structure"] = gs

        return gs

    # ---------- NetworkSource interface ----------

    def fetch_nodes(self, network: str) -> list[str]:
        gs = self._load_graph_structure("default", network)
        return list(gs.get("nodes", {}).keys())

    def fetch_edges(self, network: str) -> list[dict]:
        gs = self._load_graph_structure("default", network)
        return [
            {"source": parent, "target": child}
            for parent, children in gs.get("edges", {}).items()
            for child in children
        ]

    def infer(self, network: str, evidence: dict) -> dict:
        results = self.execute_query(query=evidence, database="default", collection=network)
        return results[0] if results else {}

    def create_network(self, network: str, properties: dict | None = None) -> dict:
        return self.create_database_collection("default", network, properties=properties)

    # ---------- database ----------

    def fetch_databases(self) -> list[str]:
        source_dir = self._get_source_directory()
        dbs = [
            item for item in os.listdir(source_dir)
            if os.path.isdir(os.path.join(source_dir, item))
        ] if os.path.exists(source_dir) else []
        if "default" not in dbs:
            dbs.append("default")
        return sorted(dbs)

    def fetch_database_metadata(self, database) -> dict:
        database_path = self._get_database_path(database)
        if not os.path.exists(database_path):
            return {}
        collections = self.fetch_database_collections(database)
        return {
            "database": database,
            "collection_count": len(collections),
            "database_path": database_path,
            "description": (
                f"Bayesian Network database '{database}' with "
                f"{len(collections)} collection(s): {', '.join(collections)}"
            ),
        }

    def create_database(self, database, properties=None, overwrite=False) -> dict:
        db = database or "default"
        database_path = self._get_database_path(db)
        if os.path.exists(database_path):
            if overwrite:
                shutil.rmtree(database_path)
                self._cache.pop(db, None)
            else:
                return {"status": "skipped"}
        os.makedirs(database_path, exist_ok=True)
        return {"status": "success"}

    # ---------- collection ----------

    def fetch_database_collections(self, database) -> list[str]:
        db = database or "default"
        cached = set(self._cache.get(db, {}).keys())
        scanned = set(self._get_database_collections(db))
        return sorted(cached | scanned)

    def fetch_database_collection_metadata(self, database, collection) -> dict:
        db = database or "default"
        try:
            gs = self._load_graph_structure(db, collection)
        except Exception:
            return {}
        if not gs:
            return {}
        nodes = gs.get("nodes", {})
        edges = gs.get("edges", {})
        node_count = len(nodes)
        edge_count = sum(len(c) for c in edges.values())
        description = gs.get("description") or (
            f"Bayesian Network '{collection}' with {node_count} nodes and {edge_count} edges"
        )
        return {
            "description": description,
            "collection": collection,
            "node_count": node_count,
            "edge_count": edge_count,
            "graph_structure": gs,
        }

    def create_database_collection(self, database, collection, properties=None, overwrite=False) -> dict:
        return {"status": "not_applicable"}

    # ---------- entities / relations ----------

    def fetch_database_collection_entities(self, database, collection) -> dict:
        db = database or "default"
        try:
            gs = self._load_graph_structure(db, collection)
        except Exception:
            return {}
        if not gs:
            return {}

        schema = DataSchema()
        nodes = gs.get("nodes", {})
        edges = gs.get("edges", {})
        markov_blankets = gs.get("markov_blanket", {})

        for node_name, node_info in nodes.items():
            if not schema.has_entity(node_name):
                schema.add_entity(node_name)
            entity_obj = schema.entities[node_name]
            if "description" in node_info:
                entity_obj["description"] = node_info["description"]

            graph_knowledge = {}
            parents = [p for p, children in edges.items() if node_name in children]
            if parents:
                graph_knowledge["parents"] = parents
            if node_name in edges:
                graph_knowledge["children"] = edges[node_name]
            if node_name in markov_blankets:
                graph_knowledge["markov_blanket"] = markov_blankets[node_name]
            if graph_knowledge:
                entity_obj["properties"]["graph_structure"] = graph_knowledge

        return schema.get_entities()

    def fetch_database_collection_relations(self, database, collection) -> dict:
        return {}

    def create_database_collection_entity(self, database, collection, entity, properties=None, overwrite=False) -> dict:
        return {"status": "not_applicable"}

    def create_database_collection_relation(self, database, collection, relation, properties=None, overwrite=False) -> dict:
        return {"status": "not_applicable"}

    # ---------- execute_query / inference ----------

    def execute_query(self, query, database=None, collection=None, optional_properties=None):
        optional_properties = optional_properties or {}
        db = database or "default"

        if collection is None:
            raise Exception("No collection (BN model) provided")

        query_dict = json.loads(query) if isinstance(query, str) else query

        target_node = query_dict.get("target_node")
        target_state = query_dict.get("target_state")
        context = query_dict.get("context", {})

        if not target_node:
            raise ValueError("Query must include 'target_node'")
        if not target_state:
            raise ValueError("Query must include 'target_state'")

        model = self._load_model(db, collection)
        model_nodes = set(model.nodes())

        if target_node not in model_nodes:
            raise ValueError(f"Target node '{target_node}' not found in model")

        evidence = {}
        for node_name, state_name in context.items():
            if node_name not in model_nodes:
                raise ValueError(f"Context node '{node_name}' not found in model")
            cpd = model.get_cpds(node_name)
            node_states = cpd.state_names.get(node_name, [])
            if state_name not in node_states:
                raise ValueError(f"State '{state_name}' not found in node '{node_name}'")
            evidence[node_name] = state_name

        try:
            inference = VariableElimination(model)
            query_result = inference.query(variables=[target_node], evidence=evidence or None)
            state_names = list(query_result.state_names[target_node])

            if target_state not in state_names:
                raise ValueError(f"State '{target_state}' not found in node '{target_node}'")

            state_index = state_names.index(target_state)
            probability = float(query_result.values[state_index])

            result = {
                "target_node": target_node,
                "target_state": target_state,
                "probability": probability,
                "probability_percent": round(probability * 100, 2),
                "context": context,
                "all_state_probabilities": {
                    state: float(query_result.values[i])
                    for i, state in enumerate(state_names)
                },
            }

            if optional_properties.get("explanation"):
                result["explanation"] = self._generate_explanation(
                    db, collection, target_node, target_state, context,
                    structured=optional_properties.get("structured_explanation", False),
                    max_num_paths=optional_properties.get("max_num_paths", 50),
                    probability=probability,
                )

            return [result]

        except Exception as e:
            self.logger.error(f"Inference failed: {e}")
            raise Exception(f"Inference failed: {e}")

    # ---------- explanation ----------

    def _generate_explanation(self, database, collection, target_node, target_state, context,
                               structured=False, max_num_paths=50, probability=None):
        try:
            gs = self._load_graph_structure(database, collection)
        except Exception:
            return "Explanation not available."
        if not gs:
            return "Explanation not available."

        nodes = gs.get("nodes", {})
        edges = gs.get("edges", {})

        G = nx.DiGraph()
        for parent, children in edges.items():
            for child in children:
                G.add_edge(parent, child)

        reasoning_paths = []
        for context_node, context_state in context.items():
            if context_node in G and target_node in G:
                try:
                    for path in nx.all_simple_paths(G, context_node, target_node, cutoff=10):
                        if len(reasoning_paths) >= max_num_paths:
                            break
                        reasoning_paths.append({
                            "start_node": context_node,
                            "start_state": context_state,
                            "path": path,
                        })
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    pass
            if len(reasoning_paths) >= max_num_paths:
                break

        if structured:
            result: dict = {
                "reasoning_paths": reasoning_paths,
                "target_node": target_node,
                "target_state": target_state,
                "context": context,
            }
            if probability is not None:
                result["probability"] = probability
                result["probability_percent"] = round(probability * 100, 2)
            return result

        parts = []
        if context:
            parts.append("Given the evidence:")
            for ctx_node, ctx_state in context.items():
                node_desc = nodes.get(ctx_node, {}).get("description", ctx_node).strip().strip(".")
                parts.append(f"  - {ctx_node} ({node_desc}) is {ctx_state}")

        if reasoning_paths:
            parts.append(f"\nThe probability of {target_node} being {target_state} is influenced through:")
            for i, path_info in enumerate(reasoning_paths, 1):
                parts.append(f"  Path {i}: {' → '.join(path_info['path'])}")

        target_desc = nodes.get(target_node, {}).get("description", target_node).strip().strip(".")
        if probability is not None:
            parts.append(
                f"\nProbability of {target_node} ({target_desc}) being {target_state}: "
                f"{round(probability * 100, 2)}% ({probability:.6f})."
            )

        return "\n".join(parts) if parts else "Explanation not available."

    # ---------- stats ----------

    def fetch_source_stats(self) -> dict:
        return {}

    def fetch_database_stats(self, database) -> dict:
        return {}

    def fetch_collection_stats(self, database, collection, entities=None, relations=None, sample_limit=None) -> dict:
        return {}

    def fetch_entity_stats(self, database, collection, entity) -> dict:
        return {}

    def fetch_property_stats(self, database, collection, entity, property_name, sample_limit=None) -> dict:
        return {}
