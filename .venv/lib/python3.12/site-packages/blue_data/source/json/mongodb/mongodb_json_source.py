import logging

from pymongo import MongoClient

from blue_data.source.json.json_source import JSONSource
from blue_data.core.schema import DataSchema


class MongoDBSource(JSONSource):
    """MongoDB data source."""

    def __init__(self, name, properties=None):
        super().__init__(name, properties=properties)
        self.logger = logging.getLogger(self.__class__.__name__)
        self._schema_cache = {}
        self._initialize_connection_properties()
        self.connection = self._connect(**self.properties["connection"])

    def _initialize_connection_properties(self):
        connection = self.properties.get("connection", {})
        connection.setdefault("host", "localhost")
        connection.setdefault("port", 27017)
        connection.setdefault("protocol", "mongodb")
        self.properties["connection"] = connection

    def _connect(self, **connection):
        protocol = connection.get("protocol", "mongodb")
        host = connection.get("host", "localhost")
        port = connection.get("port", 27017)

        username = connection.get("username") or connection.get("user")
        password = connection.get("password")

        if username and password:
            url = f"{protocol}://{username}:{password}@{host}:{port}"
        else:
            url = f"{protocol}://{host}:{port}"

        return MongoClient(url)

    def _disconnect(self):
        if getattr(self, "connection", None):
            self.connection.close()
        return None

    def fetch_metadata(self):
        return {}

    def fetch_schema(self):
        return {}

    def fetch_databases(self):
        system_dbs = {"admin", "config", "local"}
        return [
            db
            for db in self.connection.list_database_names()
            if db not in system_dbs
        ]

    def fetch_database_metadata(self, database):
        return {}

    def fetch_database_schema(self, database):
        return {}

    def create_database(self, database, properties=None, overwrite=False):
        if database in self.fetch_databases() and not overwrite:
            return {"status": "skipped"}

        # MongoDB creates databases lazily.
        self.connection[database]["_blue_metadata"].insert_one(
            {"_blue_created": True}
        )
        return {"status": "success"}

    def fetch_database_collections(self, database):
        # Keep Blue's registry convention: collection namespace is "public".
        return ["public"]

    def fetch_database_collection_metadata(self, database, collection):
        return {}

    def create_database_collection(
        self,
        database,
        collection,
        properties=None,
        overwrite=False,
    ):
        return {"status": "registry_only"}

    def fetch_database_collection_entities(
        self,
        database,
        collection,
        sample_limit=10,
    ):
        db = self.connection[database]
        collection_names = db.list_collection_names()

        schema = {}

        for coll_name in collection_names:
            if coll_name == "_blue_metadata":
                continue

            coll = db[coll_name]
            sample_docs = list(coll.find().limit(sample_limit))

            attributes = {}

            for doc in sample_docs:
                for key, value in doc.items():
                    if key not in attributes:
                        attributes[key] = {
                            "type": type(value).__name__,
                            "values": [],
                        }

            schema[coll_name] = {
                "properties": {"sample_count": len(sample_docs)},
                "contents": {"attributes": attributes},
            }

        return schema

    def fetch_database_collection_relations(self, database, collection):
        return {}

    def create_database_collection_entity(
        self,
        database,
        collection,
        entity,
        properties=None,
        overwrite=False,
    ):
        properties = properties or {}
        db = self.connection[database]

        if entity in db.list_collection_names():
            if overwrite:
                db.drop_collection(entity)
            else:
                return {"status": "skipped"}

        db.create_collection(entity)

        sample_document = properties.get("sample_document")
        if sample_document:
            db[entity].insert_one(sample_document)

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

    def extract_schema(self, sample, schema=None, source=None):
        if schema is None:
            schema = DataSchema()

        if source is None:
            source = schema.add_entity("ROOT")

        if isinstance(sample, dict):
            for key, value in sample.items():
                if isinstance(value, list):
                    target = schema.add_entity(key)
                    schema.add_relation(source, source + ":" + target, target)
                    if value:
                        self.extract_schema(value[0], schema=schema, source=target)

                elif isinstance(value, dict):
                    target = schema.add_entity(key)
                    schema.add_relation(source, source + ":" + target, target)
                    self.extract_schema(value, schema=schema, source=target)

                else:
                    schema.add_entity_property(
                        source,
                        key,
                        {"type": value.__class__.__name__, "values": []},
                    )

        return schema

    # ---------- JSONSource interface ----------

    def get(self, path: str | None = None):
        raise NotImplementedError("MongoDBSource does not implement JSONSource.get")

    def set(self, path: str | None, value) -> None:
        raise NotImplementedError("MongoDBSource does not implement JSONSource.set")

    def delete(self, path: str | None = None) -> None:
        raise NotImplementedError("MongoDBSource does not implement JSONSource.delete")

    def execute_query(
        self,
        query,
        database=None,
        collection=None,
        optional_properties=None,
    ):
        """Execute a compound MongoDB operation.

        query must be a dict with:
            op         – "FIND", "INSERT_ONE", or "DELETE_MANY"
            filter     – query filter document (FIND, DELETE_MANY)
            document   – document to insert (INSERT_ONE)
        """
        if database is None:
            raise Exception("No database provided")

        if collection is None:
            raise Exception("No collection provided")

        if not isinstance(query, dict):
            raise ValueError(
                "MongoDBSource.execute_query expects query as a dict "
                "with 'op' and 'filter' / 'document'"
            )

        db = self.connection[database]
        col = db[collection]

        op = (query.get("op") or "").upper()

        if op == "FIND":
            filter_doc = query.get("filter") or {}
            cursor = col.find(filter_doc)
            result = []
            for doc in cursor:
                if "_id" in doc:
                    doc["_id"] = str(doc["_id"])
                result.append(doc)
            return result

        if op == "INSERT_ONE":
            document = query.get("document") or {}
            res = col.insert_one(document)
            return [{"inserted_id": str(res.inserted_id)}]

        if op == "DELETE_MANY":
            filter_doc = query.get("filter") or {}
            res = col.delete_many(filter_doc)
            return [{"deleted_count": res.deleted_count}]

        raise ValueError(f"Unsupported MongoDB operation: {op!r}. Supported: FIND, INSERT_ONE, DELETE_MANY")

    def fetch_source_stats(self):
        stats = {}

        try:
            stats["database_count"] = len(self.fetch_databases())
            stats["server_info"] = self.connection.server_info()

        except Exception as e:
            self.logger.warning(f"Failed to fetch MongoDB source stats: {e}")
            stats["error"] = str(e)

        return stats

    def fetch_database_stats(self, database):
        stats = {}

        try:
            db = self.connection[database]
            command_stats = db.command("dbstats")
            stats["collection_count"] = len(db.list_collection_names())
            stats["size_bytes"] = command_stats.get("dataSize")
            stats["storage_size_bytes"] = command_stats.get("storageSize")

        except Exception as e:
            self.logger.warning(f"Failed to fetch MongoDB database stats: {e}")
            stats["error"] = str(e)

        return stats

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

    def fetch_entity_stats(self, database, collection, entity):
        stats = {}

        try:
            coll = self.connection[database][entity]
            stats["document_count"] = coll.count_documents({})
            stats["row_count"] = stats["document_count"]

        except Exception as e:
            self.logger.warning(f"Failed to fetch MongoDB entity stats: {e}")
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
            coll = self.connection[database][entity]

            non_null_filter = {property_name: {"$exists": True, "$ne": None}}

            stats["count"] = coll.count_documents(non_null_filter)
            stats["null_count"] = coll.count_documents(
                {
                    "$or": [
                        {property_name: {"$exists": False}},
                        {property_name: None},
                    ]
                }
            )

            distinct_values = coll.distinct(property_name, non_null_filter)
            stats["distinct_count"] = len(distinct_values)
            stats["sample_values"] = distinct_values[:sample_limit]

            try:
                values = [
                    v
                    for v in distinct_values
                    if isinstance(v, (int, float, str))
                ]
                stats["min"] = min(values) if values else None
                stats["max"] = max(values) if values else None
            except Exception:
                stats["min"] = None
                stats["max"] = None

            stats["most_common_vals"] = []

        except Exception as e:
            self.logger.warning(
                f"Failed to fetch MongoDB property stats for "
                f"{entity}.{property_name}: {e}"
            )
            return {}

        return stats