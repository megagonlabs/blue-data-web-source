import logging

from neo4j import GraphDatabase

from blue_data.source.graph.graph_source import GraphSource
from blue_data.core.schema import DataSchema


NATIVE_NODE_PROPERTIES_QUERY = """
CALL db.schema.nodeTypeProperties()
YIELD nodeType, propertyName, propertyTypes
RETURN
  nodeType AS label,
  collect({
    property: propertyName,
    type: propertyTypes[0]
  }) AS properties
ORDER BY label
""".strip()


NATIVE_REL_TOPOLOGY_QUERY = """
CALL db.schema.visualization()
""".strip()


NATIVE_REL_PROPERTIES_QUERY = """
CALL db.schema.relTypeProperties()
YIELD relType, propertyName, propertyTypes
RETURN
  relType AS type,
  collect({
    property: propertyName,
    type: propertyTypes[0]
  }) AS properties
ORDER BY type
""".strip()


class NEO4JSource(GraphSource):
    """Neo4j graph database source."""

    def __init__(self, name, properties=None):
        super().__init__(name, properties=properties)
        self.logger = logging.getLogger(self.__class__.__name__)
        self._apoc_available = None
        self._schema_cache = {}
        self._initialize_connection_properties()
        self.connection = self._connect(**self.properties["connection"])

    # ---------- connection ----------

    def _initialize_connection_properties(self):
        connection = self.properties.get("connection", {})

        connection.setdefault("host", "localhost")
        connection.setdefault("port", 7687)
        connection.setdefault("protocol", "bolt")
        connection.setdefault("user", "neo4j")
        connection.setdefault("password", "")

        self.properties["connection"] = connection

    def _connect(self, **connection):
        protocol = connection.get("protocol", "bolt")
        host = connection.get("host", "localhost")
        port = connection.get("port", 7687)
        user = connection.get("user", "neo4j")
        password = connection.get("password", "")

        uri = f"{protocol}://{host}:{port}"
        return GraphDatabase.driver(uri, auth=(user, password))

    def _disconnect(self):
        if getattr(self, "connection", None):
            self.connection.close()
        return None

    def _run_query(
        self,
        query,
        database=None,
        params=None,
        read=True,
    ):
        params = params or {}

        access_mode = "READ" if read else "WRITE"

        with self.connection.session(
            database=database,
            default_access_mode=access_mode,
        ) as session:
            result = session.run(query, params)
            return [dict(record) for record in result]

    # ---------- source ----------

    def has_apoc(self):
        if self._apoc_available is None:
            try:
                result = self._run_query(
                    """
                    SHOW PROCEDURES YIELD name
                    WHERE name STARTS WITH 'apoc.'
                    RETURN count(*) > 0 AS available
                    """,
                    read=True,
                )
                self._apoc_available = result[0]["available"] if result else False
            except Exception as e:
                self.logger.warning(f"Failed to check APOC availability: {e}")
                self._apoc_available = False

        return self._apoc_available

    def fetch_metadata(self):
        version = ""
        edition = ""

        try:
            result = self._run_query(
                """
                CALL dbms.components()
                YIELD versions, edition
                RETURN versions[0] AS version, edition
                """,
                read=True,
            )

            if result:
                version = result[0].get("version", "")
                edition = result[0].get("edition", "")

        except Exception as e:
            self.logger.warning(f"Failed to fetch Neo4j source metadata: {e}")

        description = "Neo4j graph database"

        if version:
            description += f" v{version}"

        if edition:
            description += f" ({edition})"

        return {
            "description": description,
            "source_type": "neo4j",
            "neo4j_version": version,
            "neo4j_edition": edition,
            "apoc_available": self.has_apoc(),
        }

    def fetch_schema(self):
        return {}

    def fetch_source_stats(self):
        stats = {}

        try:
            databases = self.fetch_databases()
            stats["database_count"] = len(databases)
            metadata = self.fetch_metadata()
            stats.update(metadata)

        except Exception as e:
            self.logger.warning(f"Failed to fetch Neo4j source stats: {e}")
            stats["error"] = str(e)

        return stats

    # ---------- database ----------

    def fetch_databases(self):
        result = self._run_query("SHOW DATABASES;", read=True)
        return [record["name"] for record in result if "name" in record]

    def fetch_database_metadata(self, database):
        total_nodes = 0
        total_rels = 0

        try:
            result = self._run_query(
                "MATCH (n) RETURN count(n) AS cnt",
                database=database,
                read=True,
            )
            total_nodes = result[0]["cnt"] if result else 0

            result = self._run_query(
                "MATCH ()-[r]->() RETURN count(r) AS cnt",
                database=database,
                read=True,
            )
            total_rels = result[0]["cnt"] if result else 0

        except Exception as e:
            self.logger.warning(
                f"Failed to fetch Neo4j database metadata for {database}: {e}"
            )

        return {
            "description": (
                f"Neo4j database '{database}' with "
                f"{total_nodes} nodes and {total_rels} relationships"
            ),
            "node_count": total_nodes,
            "relationship_count": total_rels,
        }

    def fetch_database_schema(self, database):
        return {}

    def create_database(self, database, properties=None, overwrite=False):
        databases = self.fetch_databases()

        if database in databases:
            if overwrite:
                self._run_query(
                    f"DROP DATABASE `{database}` IF EXISTS",
                    read=False,
                )
            else:
                return {"status": "skipped"}

        self._run_query(
            f"CREATE DATABASE `{database}` IF NOT EXISTS",
            read=False,
        )

        return {"status": "success"}

    def fetch_database_stats(self, database):
        metadata = self.fetch_database_metadata(database)
        return {
            "node_count": metadata.get("node_count"),
            "relationship_count": metadata.get("relationship_count"),
        }

    # ---------- collection ----------

    def fetch_database_collections(self, database):
        return [database]

    def fetch_database_collection_metadata(self, database, collection):
        schema = self._fetch_and_extract_schema(database, collection)
        entities = schema.get_entities()
        relations = schema.get_relations()

        node_types = {}
        for label, entity in entities.items():
            props = {}

            for attr_name, attr_info in (
                entity.get("contents", {})
                .get("attributes", {})
                .items()
            ):
                info = attr_info.get("info", {})
                props[attr_name] = info.get("type", "STRING")

            node_types[label] = props

        relationship_types = {}
        for _, rel in relations.items():
            rel_name = rel.get("name", "")

            relationship_types.setdefault(
                rel_name,
                {
                    "properties": {},
                    "connectivity": [],
                },
            )

            relationship_types[rel_name]["connectivity"].append(
                {
                    "from": rel.get("source", ""),
                    "to": rel.get("target", ""),
                }
            )

            for prop_name, prop_info in rel.get("properties", {}).items():
                if isinstance(prop_info, dict):
                    relationship_types[rel_name]["properties"][prop_name] = (
                        prop_info.get("type", "STRING")
                    )
                else:
                    relationship_types[rel_name]["properties"][prop_name] = str(
                        prop_info
                    )

        node_type_count = len(node_types)
        rel_type_count = len(relationship_types)

        total_nodes = 0
        total_rels = 0
        node_count_by_type = {}
        rel_count_by_type = {}

        try:
            result = self._run_query(
                "MATCH (n) RETURN count(n) AS cnt",
                database=database,
                read=True,
            )
            total_nodes = result[0]["cnt"] if result else 0

            result = self._run_query(
                "MATCH ()-[r]->() RETURN count(r) AS cnt",
                database=database,
                read=True,
            )
            total_rels = result[0]["cnt"] if result else 0

            stats_limit = 50

            result = self._run_query(
                """
                MATCH (n)
                RETURN labels(n)[0] AS label, count(n) AS cnt
                ORDER BY cnt DESC
                LIMIT $limit
                """,
                database=database,
                params={"limit": stats_limit + 1},
                read=True,
            )
            rows = [row for row in result if row.get("label")]
            node_count_by_type = {
                row["label"]: row["cnt"]
                for row in rows[:stats_limit]
            }

            if len(rows) > stats_limit:
                node_count_by_type["_truncated"] = True

            result = self._run_query(
                """
                MATCH ()-[r]->()
                RETURN type(r) AS type, count(r) AS cnt
                ORDER BY cnt DESC
                LIMIT $limit
                """,
                database=database,
                params={"limit": stats_limit + 1},
                read=True,
            )
            rows = [row for row in result if row.get("type")]
            rel_count_by_type = {
                row["type"]: row["cnt"]
                for row in rows[:stats_limit]
            }

            if len(rows) > stats_limit:
                rel_count_by_type["_truncated"] = True

        except Exception as e:
            self.logger.warning(
                f"Failed to fetch Neo4j collection stats for "
                f"{database}.{collection}: {e}"
            )

        return {
            "description": (
                f"Graph database with {total_nodes} nodes "
                f"({node_type_count} types) and {total_rels} relationships "
                f"({rel_type_count} types)"
            ),
            "collection_type": "neo4j",
            "schema": {
                "node_types": node_types,
                "relationship_types": relationship_types,
            },
            "statistics": {
                "node_count": total_nodes,
                "relationship_count": total_rels,
                "node_type_count": node_type_count,
                "relationship_type_count": rel_type_count,
                "node_count_by_type": node_count_by_type,
                "relationship_count_by_type": rel_count_by_type,
            },
        }

    def create_database_collection(
        self,
        database,
        collection,
        properties=None,
        overwrite=False,
    ):
        return {"status": "registry_only"}

    def fetch_collection_stats(
        self,
        database,
        collection,
        entities=None,
        relations=None,
        sample_limit=None,
    ):
        metadata = self.fetch_database_collection_metadata(database, collection)
        statistics = metadata.get("statistics", {})

        return {
            "num_entities": len(entities or []),
            "num_relations": len(relations or []),
            **statistics,
        }

    # ---------- entities / relations ----------

    def fetch_database_collection_entities(self, database, collection):
        schema = self._fetch_and_extract_schema(database, collection)
        return schema.get_entities()

    def fetch_database_collection_relations(self, database, collection):
        schema = self._fetch_and_extract_schema(database, collection)
        return schema.get_relations()

    def create_database_collection_entity(
        self,
        database,
        collection,
        entity,
        properties=None,
        overwrite=False,
    ):
        properties = properties or {}

        if overwrite:
            self._run_query(
                f"MATCH (n:`{entity}`) DETACH DELETE n",
                database=database,
                read=False,
            )

        sample_properties = properties.get("sample_properties", {})

        self._run_query(
            f"CREATE (n:`{entity}` $props)",
            database=database,
            params={"props": sample_properties},
            read=False,
        )

        self._schema_cache.pop((database, collection), None)
        return {"status": "success"}

    def create_database_collection_relation(
        self,
        database,
        collection,
        relation,
        properties=None,
        overwrite=False,
    ):
        return {"status": "registry_only"}

    def fetch_entity_stats(self, database, collection, entity):
        stats = {}

        try:
            result = self._run_query(
                f"MATCH (n:`{entity}`) RETURN count(n) AS cnt",
                database=database,
                read=True,
            )
            stats["row_count"] = result[0]["cnt"] if result else 0
            stats["node_count"] = stats["row_count"]

        except Exception as e:
            self.logger.warning(f"Failed to fetch Neo4j entity stats: {e}")
            stats["row_count"] = None
            stats["error"] = str(e)

        return stats

    def fetch_property_stats(
        self,
        database,
        collection,
        entity,
        property_name,
        sample_limit=10,
    ):
        stats = {}

        try:
            result = self._run_query(
                f"""
                MATCH (n:`{entity}`)
                WITH n.`{property_name}` AS value
                RETURN
                    count(value) AS count,
                    count(DISTINCT value) AS distinct_count,
                    collect(DISTINCT value)[0..$sample_limit] AS sample_values,
                    min(value) AS min,
                    max(value) AS max
                """,
                database=database,
                params={"sample_limit": sample_limit},
                read=True,
            )

            if result:
                row = result[0]
                stats["count"] = row.get("count")
                stats["distinct_count"] = row.get("distinct_count")
                stats["sample_values"] = row.get("sample_values", [])
                stats["min"] = row.get("min")
                stats["max"] = row.get("max")

            result = self._run_query(
                f"""
                MATCH (n:`{entity}`)
                WHERE n.`{property_name}` IS NOT NULL
                RETURN n.`{property_name}` AS value, count(*) AS freq
                ORDER BY freq DESC
                LIMIT 5
                """,
                database=database,
                read=True,
            )

            stats["most_common_vals"] = [row["value"] for row in result]
            stats.setdefault("null_count", None)

        except Exception as e:
            self.logger.warning(
                f"Failed to fetch Neo4j property stats for "
                f"{entity}.{property_name}: {e}"
            )
            return {}

        return stats

    # ---------- graph primitives ----------

    def add_node(self, database: str, label: str, properties: dict | None = None, node_id: str | None = None) -> str:
        props = dict(properties or {})
        if node_id is not None:
            props["_id"] = node_id
        lbl = self._clean_neo4j_identifier(label)
        result = self._run_query(
            f"CREATE (n:`{lbl}` $props) RETURN n._id AS cid, elementId(n) AS eid",
            database=database, params={"props": props}, read=False,
        )
        if result:
            row = result[0]
            return str(row["cid"] if row.get("cid") is not None else row["eid"])
        raise RuntimeError("add_node returned no result")

    def get_node(self, database: str, node_id: str) -> dict | None:
        result = self._run_query(
            "MATCH (n) WHERE n._id = $nid OR elementId(n) = $nid "
            "RETURN n, labels(n) AS labels LIMIT 1",
            database=database, params={"nid": node_id}, read=True,
        )
        if not result:
            return None
        row = result[0]
        node_obj = row["n"]
        return {
            "node_id": node_id,
            "label": list(node_obj.labels)[0] if node_obj.labels else None,
            "properties": dict(node_obj),
        }

    def delete_node(self, database: str, node_id: str) -> None:
        self._run_query(
            "MATCH (n) WHERE n._id = $nid OR elementId(n) = $nid DETACH DELETE n",
            database=database, params={"nid": node_id}, read=False,
        )

    def add_edge(self, database: str, source_id: str, edge_type: str, target_id: str, properties: dict | None = None) -> str:
        rel_type = self._clean_neo4j_identifier(edge_type)
        result = self._run_query(
            f"MATCH (a) WHERE a._id = $src OR elementId(a) = $src "
            f"MATCH (b) WHERE b._id = $tgt OR elementId(b) = $tgt "
            f"CREATE (a)-[r:`{rel_type}` $props]->(b) RETURN elementId(r) AS eid",
            database=database,
            params={"src": source_id, "tgt": target_id, "props": properties or {}},
            read=False,
        )
        if result:
            return str(result[0]["eid"])
        raise RuntimeError("add_edge returned no result")

    def delete_edge(self, database: str, source_id: str, edge_type: str, target_id: str) -> None:
        rel_type = self._clean_neo4j_identifier(edge_type)
        self._run_query(
            f"MATCH (a)-[r:`{rel_type}`]->(b) "
            f"WHERE (a._id = $src OR elementId(a) = $src) "
            f"AND (b._id = $tgt OR elementId(b) = $tgt) DELETE r",
            database=database, params={"src": source_id, "tgt": target_id}, read=False,
        )

    def get_neighbors(self, database: str, node_id: str, edge_type: str | None = None, direction: str = "both") -> list[dict]:
        rel_spec = f":`{self._clean_neo4j_identifier(edge_type)}`" if edge_type else ""
        if direction == "out":
            pattern = f"-[r{rel_spec}]->"
        elif direction == "in":
            pattern = f"<-[r{rel_spec}]-"
        else:
            pattern = f"-[r{rel_spec}]-"
        result = self._run_query(
            f"MATCH (n){pattern}(m) WHERE n._id = $nid OR elementId(n) = $nid "
            f"RETURN m, labels(m) AS labels, type(r) AS edge_type, r AS edge",
            database=database, params={"nid": node_id}, read=True,
        )
        neighbors = []
        for row in result:
            node_obj = row["m"]
            edge_obj = row.get("edge")
            neighbors.append({
                "node": {
                    "label": list(node_obj.labels)[0] if node_obj.labels else None,
                    "properties": dict(node_obj),
                },
                "edge_type": row.get("edge_type"),
                "edge_properties": dict(edge_obj) if edge_obj else {},
            })
        return neighbors

    # ---------- query ----------

    def execute_query(
        self,
        query,
        database=None,
        collection=None,
        optional_properties=None,
    ):
        optional_properties = optional_properties or {}

        params = optional_properties.get("params")
        read = optional_properties.get("read", True)

        return self._run_query(
            query,
            database=database,
            params=params,
            read=read,
        )

    # ---------- internal helpers ----------

    @staticmethod
    def _clean_neo4j_identifier(name):
        if name and isinstance(name, str):
            name = name.strip("`:")
        return name

    def _fetch_and_extract_schema(self, database, collection):
        cache_key = (database, collection)

        if cache_key not in self._schema_cache:
            self._schema_cache[cache_key] = self._extract_schema(database)

        return self._schema_cache[cache_key]

    def _extract_schema(self, database):
        nodes_result = self._run_query(
            NATIVE_NODE_PROPERTIES_QUERY,
            database=database,
            read=True,
        )
        topology_result = self._run_query(
            NATIVE_REL_TOPOLOGY_QUERY,
            database=database,
            read=True,
        )
        rel_props_result = self._run_query(
            NATIVE_REL_PROPERTIES_QUERY,
            database=database,
            read=True,
        )

        schema = DataSchema()

        for node in nodes_result:
            label = self._clean_neo4j_identifier(node["label"])
            schema.add_entity(label)

            for prop in node.get("properties", []):
                prop_name = prop.get("property")
                if prop_name:
                    schema.add_entity_property(
                        label,
                        prop_name,
                        {"type": prop.get("type", "STRING"), "values": []},
                    )

        rel_properties = {}
        for rel in rel_props_result:
            clean_type = self._clean_neo4j_identifier(rel["type"])
            rel_properties[clean_type] = rel.get("properties", [])

        rels_list = (
            topology_result[0].get("relationships", [])
            if topology_result
            else []
        )

        for rel in rels_list:
            start = self._clean_neo4j_identifier(
                rel.get("from", {}).get("type")
            )
            rel_type = self._clean_neo4j_identifier(rel.get("type"))
            end = self._clean_neo4j_identifier(
                rel.get("to", {}).get("type")
            )

            if not start or not rel_type or not end:
                continue

            key = schema.add_relation(start, rel_type, end)

            for prop in rel_properties.get(rel_type, []):
                prop_name = prop.get("property")
                if prop_name:
                    schema.add_relation_property(
                        key,
                        prop_name,
                        {"type": prop.get("type", "STRING"), "values": []},
                    )

        return schema