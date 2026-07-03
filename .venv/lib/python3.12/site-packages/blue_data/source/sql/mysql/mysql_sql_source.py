import copy
import logging
import re

import mysql.connector as cpy

from blue_data.source.sql.sql_source import SQLSource
from blue_data.core.schema import DataSchema


class MySQLDBSource(SQLSource):
    """MySQL data source."""

    def __init__(self, name, properties=None):
        super().__init__(name, properties=properties)
        self.logger = logging.getLogger(self.__class__.__name__)
        self._initialize_connection_properties()
        self.connection = self._connect(**self.properties["connection"])

    # ---------- connection ----------

    def _initialize_connection_properties(self):
        connection = self.properties.get("connection", {})

        connection.setdefault("host", "localhost")
        connection.setdefault("port", 3306)
        connection.setdefault("protocol", "mysql")
        connection.setdefault("user", "root")
        connection.setdefault("password", "")
        connection.setdefault("database", "mysql")

        self.properties["connection"] = connection

    def _connect(self, **connection):
        config = copy.deepcopy(connection)
        config.pop("protocol", None)
        return cpy.connect(**config)

    def _disconnect(self):
        if getattr(self, "connection", None):
            self.connection.close()
        return None

    def _db_connect(self, database):
        config = copy.deepcopy(self.properties["connection"])
        config.pop("protocol", None)
        config["database"] = database
        return cpy.connect(**config)

    # ---------- source ----------

    def fetch_metadata(self):
        return {}

    def fetch_schema(self):
        return {}

    def fetch_source_stats(self):
        stats = {}

        try:
            cur = self.connection.cursor()
            cur.execute("SELECT VERSION();")
            stats["version"] = cur.fetchone()[0]
            cur.close()

        except Exception as e:
            self.logger.warning(f"Failed to collect MySQL source stats: {e}")
            stats["error"] = str(e)

        return stats

    # ---------- database ----------

    def fetch_databases(self):
        cur = self.connection.cursor()
        cur.execute("SHOW DATABASES;")

        system_databases = {
            "information_schema",
            "performance_schema",
            "sys",
            "mysql",
        }

        databases = [
            row[0]
            for row in cur.fetchall()
            if row[0] not in system_databases
        ]

        cur.close()
        return databases

    def fetch_database_metadata(self, database):
        return {}

    def fetch_database_schema(self, database):
        return {}

    def create_database(self, database, properties=None, overwrite=False):
        existing = self.fetch_databases()

        cur = self.connection.cursor()

        try:
            if database in existing:
                if overwrite:
                    cur.execute(f"DROP DATABASE IF EXISTS `{database}`;")
                else:
                    return {"status": "skipped"}

            cur.execute(f"CREATE DATABASE `{database}`;")
            self.connection.commit()
            return {"status": "success"}

        finally:
            cur.close()

    def fetch_database_stats(self, database):
        conn = self._db_connect(database)
        cur = conn.cursor()

        stats = {}

        try:
            cur.execute(
                """
                SELECT IFNULL(SUM(data_length + index_length), 0)
                FROM information_schema.tables
                WHERE table_schema = %s;
                """,
                (database,),
            )
            row = cur.fetchone()
            stats["size_bytes"] = row[0] if row else None

            cur.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_schema = %s
                  AND table_type = 'BASE TABLE';
                """,
                (database,),
            )
            stats["table_count"] = cur.fetchone()[0]

        except Exception as e:
            self.logger.warning(f"Error fetching database stats for {database}: {e}")
            stats["error"] = str(e)

        finally:
            cur.close()
            self._db_disconnect(conn)

        return stats

    # ---------- collection ----------

    def fetch_database_collections(self, database):
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

    # ---------- entities / relations ----------

    def fetch_database_collection_entities(self, database, collection):
        conn = self._db_connect(database)
        cur = conn.cursor()

        try:
            cur.execute(
                """
                SELECT table_name, column_name, data_type, column_type
                FROM information_schema.columns
                WHERE table_schema = %s
                ORDER BY table_name, ordinal_position;
                """,
                (database,),
            )

            rows = cur.fetchall()
            schema = DataSchema()

            for table_name, column_name, data_type, column_type in rows:
                if not schema.has_entity(table_name):
                    schema.add_entity(table_name)

                property_def = {
                    "type": data_type,
                    "values": [],
                }

                if data_type.lower() == "enum":
                    enum_values = re.findall(r"'(.*?)'", column_type)
                    property_def["enum"] = enum_values
                    property_def["values"] = enum_values

                schema.add_entity_property(
                    table_name,
                    column_name,
                    property_def,
                )

            return schema.get_entities()

        finally:
            cur.close()
            self._db_disconnect(conn)

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

        conn = self._db_connect(database)
        cur = conn.cursor()

        try:
            cur.execute("SHOW TABLES LIKE %s;", (entity,))
            exists = cur.fetchone() is not None

            if exists:
                if overwrite:
                    cur.execute(f"DROP TABLE IF EXISTS `{entity}`;")
                else:
                    return {"status": "skipped"}

            column_definitions = properties.get("cols_definition", [])
            if not column_definitions:
                raise ValueError("cols_definition is required")

            column_sql = []

            for col in column_definitions:
                col_sql = f"`{col['name']}`"
                if "type" in col:
                    col_sql += f" {col['type']}"
                if "misc" in col:
                    col_sql += f" {col['misc']}"
                column_sql.append(col_sql)

            primary_key = properties.get("primary_key", [])
            if primary_key:
                cols = ", ".join([f"`{col}`" for col in primary_key])
                column_sql.append(f"PRIMARY KEY ({cols})")

            foreign_keys = properties.get("foreign_keys", [])
            for fk in foreign_keys:
                source_cols = ", ".join(
                    [f"`{col}`" for col in fk["foreign_keys_source_columns"]]
                )
                target_cols = ", ".join(
                    [f"`{col}`" for col in fk["foreign_keys_target_columns"]]
                )
                target_table = fk["foreign_keys_target_table"]

                column_sql.append(
                    f"FOREIGN KEY ({source_cols}) "
                    f"REFERENCES `{target_table}` ({target_cols})"
                )

            query = f"CREATE TABLE `{entity}` ({', '.join(column_sql)});"
            cur.execute(query)
            conn.commit()

            return {"status": "success"}

        finally:
            cur.close()
            self._db_disconnect(conn)

    def create_database_collection_relation(
        self,
        database,
        collection,
        relation,
        properties=None,
        overwrite=False,
    ):
        return {"status": "registry_only"}

    # ---------- stats ----------

    def fetch_entity_stats(self, database, collection, entity):
        conn = self._db_connect(database)
        cur = conn.cursor()

        stats = {}

        try:
            cur.execute(f"SELECT COUNT(*) FROM `{entity}`;")
            stats["row_count"] = cur.fetchone()[0]

        except Exception as e:
            self.logger.warning(f"Failed to fetch entity stats for {entity}: {e}")
            stats["row_count"] = None
            stats["error"] = str(e)

        finally:
            cur.close()
            self._db_disconnect(conn)

        return stats

    def fetch_property_stats(
        self,
        database,
        collection,
        entity,
        property_name,
        sample_limit=10,
    ):
        conn = self._db_connect(database)
        cur = conn.cursor()

        column = f"`{property_name}`"

        try:
            cur.execute(
                """
                SELECT DATA_TYPE
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = %s
                  AND column_name = %s;
                """,
                (database, entity, property_name),
            )

            type_result = cur.fetchone()
            column_type = type_result[0] if type_result else None

            include_min_max = column_type in {
                "int",
                "integer",
                "bigint",
                "smallint",
                "tinyint",
                "mediumint",
                "decimal",
                "numeric",
                "float",
                "double",
                "real",
                "date",
                "datetime",
                "timestamp",
                "time",
                "year",
                "boolean",
                "bool",
                "enum",
            }

            cur.execute(
                f"""
                SELECT
                    COUNT({column}) AS non_null_count,
                    COUNT(DISTINCT {column}) AS distinct_count,
                    SUM(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END) AS null_count
                FROM `{entity}`;
                """
            )

            row = cur.fetchone()

            stats = {
                "count": row[0],
                "distinct_count": row[1],
                "null_count": row[2],
            }

            cur.execute(
                f"""
                SELECT {column}
                FROM `{entity}`
                WHERE {column} IS NOT NULL
                LIMIT {sample_limit};
                """
            )

            stats["sample_values"] = [r[0] for r in cur.fetchall()]

            if include_min_max:
                cur.execute(
                    f"""
                    SELECT MIN({column}), MAX({column})
                    FROM `{entity}`;
                    """
                )
                min_max = cur.fetchone()
                stats["min"] = min_max[0]
                stats["max"] = min_max[1]
            else:
                stats["min"] = None
                stats["max"] = None

            cur.execute(
                f"""
                SELECT {column}, COUNT(*) AS freq
                FROM `{entity}`
                WHERE {column} IS NOT NULL
                GROUP BY {column}
                ORDER BY freq DESC
                LIMIT 5;
                """
            )

            stats["most_common_vals"] = [r[0] for r in cur.fetchall()]

            return stats

        except Exception as e:
            self.logger.warning(
                f"Failed to fetch property stats for "
                f"{entity}.{property_name}: {e}"
            )
            return {}

        finally:
            cur.close()
            self._db_disconnect(conn)

