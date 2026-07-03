import copy
import json
import logging

import numpy as np
import redis

from blue_data.source.vector.vector_source import VectorSource
from blue_data.core.schema import DataSchema


class RedisVectorSource(VectorSource):
    """Redis Vector data source using RedisJSON + RediSearch."""

    def __init__(self, name: str, properties: dict | None = None):
        super().__init__(name, properties=properties)
        self.logger = logging.getLogger(self.__class__.__name__)
        self._initialize_connection_properties()
        self.connection = self._connect(**self.properties["connection"])

    def _initialize_connection_properties(self):
        connection = self.properties.get("connection", {})

        connection.setdefault("host", "localhost")
        connection.setdefault("port", 6379)
        connection.setdefault("protocol", "vector")
        connection.setdefault("subprotocol", "redis")
        connection.setdefault("db", 0)

        scan_prefix = connection.get("scan_prefix")
        if not scan_prefix:
            raise Exception("RedisVectorSource requires 'scan_prefix'")

        if not scan_prefix.endswith(":"):
            scan_prefix += ":"

        connection["scan_prefix"] = scan_prefix
        self.properties["connection"] = connection

    def _connect(self, **connection):
        config = copy.deepcopy(connection)

        config.pop("protocol", None)
        config.pop("subprotocol", None)
        config.pop("scan_prefix", None)

        return redis.Redis(
            host=config.get("host", "localhost"),
            port=config.get("port", 6379),
            db=config.get("db", 0),
            password=config.get("password"),
            decode_responses=False,
        )

    def _disconnect(self):
        try:
            self.connection.close()
        except Exception:
            pass
        return None

    # ---------- source ----------

    def fetch_metadata(self):
        return {}

    def fetch_schema(self):
        return {}

    def fetch_vector_indices(self):
        return self.connection.execute_command("FT._LIST")

    def fetch_source_stats(self):
        stats = {}

        try:
            info = self.connection.info()
            stats["redis_version"] = info.get("redis_version")
            stats["connected_clients"] = info.get("connected_clients")
            stats["used_memory"] = info.get("used_memory_human")
            stats["total_keys"] = self.connection.dbsize()

        except Exception as e:
            self.logger.warning(f"Failed to fetch Redis source stats: {e}")
            stats["error"] = str(e)

        return stats

    # ---------- database ----------

    def fetch_databases(self):
        scan_prefix = self.properties["connection"].get("scan_prefix")

        if not scan_prefix:
            raise Exception("RedisVectorSource requires 'scan_prefix'")

        if not scan_prefix.endswith(":"):
            scan_prefix += ":"

        return [scan_prefix[:-1]]

    def fetch_database_metadata(self, database):
        return {}

    def fetch_database_schema(self, database):
        return {}

    # ---------- collection ----------

    def fetch_database_collections(self, database):
        scan_prefix = self.properties["connection"].get("scan_prefix")

        if not scan_prefix:
            raise Exception("RedisVectorSource requires 'scan_prefix'")

        if scan_prefix.startswith("PLATFORM"):
            raise Exception("Invalid scan_prefix: cannot use PLATFORM namespace")

        if not scan_prefix.endswith(":"):
            scan_prefix += ":"

        pattern = f"{scan_prefix}*"
        prefix_len = len(scan_prefix)

        cursor = 0
        collections = set()

        while True:
            cursor, keys = self.connection.scan(
                cursor=cursor,
                match=pattern,
                count=500,
            )

            for key in keys:
                key = self._decode_value(key)

                if not isinstance(key, str):
                    continue

                if not key.startswith(scan_prefix):
                    continue

                remainder = key[prefix_len:]
                if not remainder:
                    continue

                collection_name = remainder.split(":")[0]
                if collection_name:
                    collections.add(collection_name)

            if cursor == 0:
                break

        return list(collections)

    def fetch_database_collection_metadata(self, database, collection):
        return {}

    def fetch_database_collection_entities(
        self,
        database,
        collection,
        sample_limit=25,
    ):
        schema = DataSchema()
        return schema.get_entities()

    def fetch_database_collection_relations(self, database, collection):
        return {}

    def fetch_collection_stats(
        self,
        database,
        collection,
        entities=None,
        relations=None,
        sample_limit=None,
    ):
        entities = entities or []
        relations = relations or []

        return {
            "num_entities": len(entities),
            "num_relations": len(relations),
        }

    # ---------- helpers ----------

    def _decode_value(self, value):
        if isinstance(value, (bytes, bytearray)):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return value

        if isinstance(value, (list, tuple)):
            return [self._decode_value(item) for item in value]

        if isinstance(value, dict):
            return {
                self._decode_value(key): self._decode_value(val)
                for key, val in value.items()
            }

        return value

    def _ft_info(self, index_name):
        raw = self.connection.execute_command("FT.INFO", index_name)

        if isinstance(raw, dict):
            return self._decode_value(raw)

        if not isinstance(raw, (list, tuple)):
            return {"raw": self._decode_value(raw)}

        info = {}
        raw_items = list(raw)

        for index in range(0, len(raw_items), 2):
            key = self._decode_value(raw_items[index])
            value = raw_items[index + 1] if index + 1 < len(raw_items) else None
            info[key] = self._decode_value(value)

        return info

    def _vector_bytes(self, vector):
        if isinstance(vector, (bytes, bytearray)):
            return bytes(vector)

        if isinstance(vector, str):
            raise Exception("Vector must be bytes or a list of floats")

        return np.asarray(vector, dtype=np.float32).tobytes()

    def _build_create_index_command(self, query):
        index_name = query.get("index")
        scan_prefix = self.properties["connection"].get("scan_prefix")
        collection = query.get("collection")
        dimension = query.get("dim")

        if not index_name or not scan_prefix or not collection or dimension is None:
            raise Exception(
                "VECTOR.CREATE_INDEX requires index, scan_prefix, collection, dim"
            )

        prefix = f"{scan_prefix}{collection}:"

        vector_path = query.get("path", query.get("vector_path", "$.embedding"))
        vector_field = query.get("field", query.get("vector_field", "embedding"))
        algorithm = query.get("algorithm", "HNSW")
        metric = query.get("metric", query.get("distance_metric", "COSINE"))

        return [
            "FT.CREATE",
            index_name,
            "ON",
            "JSON",
            "PREFIX",
            "1",
            prefix,
            "SCHEMA",
            vector_path,
            "AS",
            vector_field,
            "VECTOR",
            algorithm,
            "6",
            "TYPE",
            "FLOAT32",
            "DIM",
            int(dimension),
            "DISTANCE_METRIC",
            metric,
        ]

    def _build_knn_command(self, query):
        index_name = query.get("index")
        if not index_name:
            raise Exception("VECTOR.KNN requires index")

        vector = query.get("vector", query.get("embedding"))

        if vector is None:
            vector = query.get("query_vector", query.get("query_embedding"))

        if vector is None:
            raise Exception("VECTOR.KNN requires query vector")

        index_dimension = query.get("index_dimension")
        if index_dimension is not None and len(vector) != index_dimension:
            raise Exception(
                f"Query vector dimension {len(vector)} does not match "
                f"index dimension {index_dimension}"
            )

        vector = self._vector_bytes(vector)

        k = int(query.get("k", 5))
        vector_field = query.get("field", query.get("vector_field", "embedding"))
        score_alias = query.get("score_alias", query.get("score_field", "score"))
        dialect = int(query.get("dialect", 2))
        filter_expression = query.get("filter", "*")
        return_fields = list(query.get("return_fields", []))

        if score_alias not in return_fields:
            return_fields.append(score_alias)

        search_query = (
            f"{filter_expression}=>[KNN {k} @{vector_field} $vec AS {score_alias}]"
        )

        command = [
            "FT.SEARCH",
            index_name,
            search_query,
            "PARAMS",
            2,
            "vec",
            vector,
            "SORTBY",
            score_alias,
        ]

        if return_fields:
            command.extend(["RETURN", len(return_fields), *return_fields])

        command.extend(["DIALECT", dialect])

        return command

    def _parse_search_results(self, response):
        if not isinstance(response, (list, tuple)) or len(response) == 0:
            return []

        rows = []
        items = list(response)

        for index in range(1, len(items), 2):
            record_id = self._decode_value(items[index])
            raw_fields = items[index + 1] if index + 1 < len(items) else []

            row = {"id": record_id}

            if isinstance(raw_fields, (list, tuple)):
                pairs = list(raw_fields)

                for field_index in range(0, len(pairs), 2):
                    name = self._decode_value(pairs[field_index])
                    value = (
                        pairs[field_index + 1]
                        if field_index + 1 < len(pairs)
                        else None
                    )

                    decoded_value = self._decode_value(value)

                    if name == "$" and isinstance(decoded_value, str):
                        try:
                            row["document"] = json.loads(decoded_value)
                            continue
                        except Exception:
                            pass

                    row[name] = decoded_value

            rows.append(row)

        return rows

    # ---------- primitive operations ----------

    def create_index(self, index_name: str, collection: str, dim: int, distance_metric: str = "COSINE") -> str:
        conn = self.properties["connection"]
        query = {
            "index": index_name,
            "collection": collection,
            "dim": dim,
            "distance_metric": distance_metric,
            "path": conn.get("vector_path", "$.embedding"),
            "field": conn.get("vector_field", "embedding"),
            "algorithm": conn.get("algorithm", "HNSW"),
        }
        try:
            return self.connection.execute_command(*self._build_create_index_command(query))
        except Exception as e:
            self.logger.error(f"RedisVector create_index failed: {e}")
            raise

    def upsert(self, collection: str, doc_id: str, doc: dict, embedding_field: str = "embedding") -> str:
        if embedding_field not in doc:
            raise Exception(f"Document must contain embedding field '{embedding_field}'")
        scan_prefix = self.properties["connection"].get("scan_prefix")
        key = f"{scan_prefix}{collection}:{doc_id}"
        try:
            return self.connection.json().set(key, "$", doc)
        except Exception as e:
            self.logger.error(f"RedisVector upsert failed: {e}")
            raise

    def delete(self, collection: str, doc_id: str) -> int:
        scan_prefix = self.properties["connection"].get("scan_prefix")
        key = f"{scan_prefix}{collection}:{doc_id}"
        try:
            return self.connection.delete(key)
        except Exception as e:
            self.logger.error(f"RedisVector delete failed: {e}")
            raise

    def knn(
        self,
        index_name: str,
        query_vector: list,
        k: int = 5,
        embedding_field: str = "embedding",
        score_alias: str = "score",
        return_fields: list[str] | None = None,
        filter_expr: str = "*",
    ) -> list[dict]:
        query = {
            "index": index_name,
            "vector": query_vector,
            "k": k,
            "field": embedding_field,
            "score_alias": score_alias,
            "return_fields": return_fields or [],
            "filter": filter_expr,
        }
        try:
            response = self.connection.execute_command(*self._build_knn_command(query))
            return self._parse_search_results(response)
        except Exception as e:
            self.logger.error(f"RedisVector knn failed: {e}")
            raise