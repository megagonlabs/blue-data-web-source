import copy
import logging

import psycopg2

from blue_data.source.sql.sql_source import SQLSource
from blue_data.core.schema import DataSchema


class PostgresDBSource(SQLSource):
    """PostgreSQL data source."""

    def __init__(self, name, properties=None):
        super().__init__(name, properties=properties)
        self.logger = logging.getLogger(self.__class__.__name__)
        self._initialize_connection_properties()
        self.connection = self._connect(**self.properties["connection"])

    # ---------- connection ----------

    def _initialize_connection_properties(self):
        connection = self.properties.get("connection", {})

        connection.setdefault("host", "localhost")
        connection.setdefault("port", 5432)
        connection.setdefault("protocol", "postgres")
        connection.setdefault("user", "postgres")
        connection.setdefault("password", "")
        connection.setdefault("database", "postgres")

        self.properties["connection"] = connection

    def _connect(self, **connection):
        config = copy.deepcopy(connection)
        config.pop("protocol", None)
        return psycopg2.connect(**config)

    def _disconnect(self):
        if getattr(self, "connection", None):
            self.connection.close()
        return None

    def _db_connect(self, database):
        config = copy.deepcopy(self.properties["connection"])
        config.pop("protocol", None)
        config["database"] = database
        return psycopg2.connect(**config)

    # ---------- source ----------

    def fetch_metadata(self):
        return {}

    def fetch_schema(self):
        return {}

    def fetch_source_stats(self):
        stats = {}

        try:
            with self.connection.cursor() as cur:
                cur.execute("SELECT version();")
                stats["version"] = cur.fetchone()[0]

                cur.execute(
                    "SELECT datname FROM pg_database WHERE datistemplate = false;"
                )
                stats["database_count"] = len(cur.fetchall())

                cur.execute("SELECT now() - pg_postmaster_start_time();")
                stats["uptime"] = str(cur.fetchone()[0])

        except Exception as e:
            self.logger.warning(f"Failed to collect PostgreSQL source stats: {e}")
            stats["error"] = str(e)

        return stats

    # ---------- database ----------

    def fetch_databases(self):
        query = "SELECT datname FROM pg_database WHERE datistemplate = false;"
        cursor = self.connection.cursor()
        cursor.execute(query)
        return [row[0] for row in cursor.fetchall()]

    def fetch_database_metadata(self, database):
        return {}

    def fetch_database_schema(self, database):
        return {}

    def create_database(self, database, properties=None, overwrite=False):
        properties = properties or {}

        existing = self.fetch_databases()

        if database in existing:
            if overwrite:
                self.connection.commit()
                self.connection.autocommit = True
                with self.connection.cursor() as cur:
                    cur.execute(
                        """
                        SELECT pg_terminate_backend(pid)
                        FROM pg_stat_activity
                        WHERE datname = %s AND pid <> pg_backend_pid();
                        """,
                        (database,),
                    )
                    cur.execute(f'DROP DATABASE IF EXISTS "{database}";')
            else:
                return {"status": "skipped"}

        self.connection.commit()
        self.connection.autocommit = True
        with self.connection.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{database}";')

        return {"status": "success"}

    def fetch_database_stats(self, database):
        conn = self._db_connect(database)
        cur = conn.cursor()
        stats = {}

        try:
            cur.execute("SELECT pg_database_size(%s);", (database,))
            row = cur.fetchone()
            stats["size_bytes"] = row[0] if row else None

            cur.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                  AND table_type = 'BASE TABLE';
                """
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
        conn = self._db_connect(database)
        cur = conn.cursor()

        try:
            cur.execute(
                """
                SELECT schema_name
                FROM information_schema.schemata
                WHERE schema_name NOT IN ('pg_catalog', 'information_schema',
                                          'pg_toast', 'pg_temp_1', 'pg_toast_temp_1');
                """
            )
            return [row[0] for row in cur.fetchall()]

        finally:
            cur.close()
            self._db_disconnect(conn)

    def fetch_database_collection_metadata(self, database, collection):
        return {}

    def create_database_collection(
        self,
        database,
        collection,
        properties=None,
        overwrite=False,
    ):
        conn = self._db_connect(database)
        cur = conn.cursor()

        try:
            cur.execute(
                """
                SELECT schema_name
                FROM information_schema.schemata
                WHERE schema_name = %s;
                """,
                (collection,),
            )
            exists = cur.fetchone() is not None

            if exists:
                if overwrite:
                    cur.execute(f'DROP SCHEMA IF EXISTS "{collection}" CASCADE;')
                else:
                    return {"status": "skipped"}

            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{collection}";')
            conn.commit()
            return {"status": "success"}

        finally:
            cur.close()
            self._db_disconnect(conn)

    # ---------- entities / relations ----------

    def fetch_enum_types(self, conn):
        query = """
        SELECT
            n.nspname AS schema,
            t.typname AS type_name,
            e.enumlabel AS enum_value
        FROM pg_type t
        JOIN pg_enum e ON t.oid = e.enumtypid
        JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY t.typname, e.enumsortorder;
        """

        cur = conn.cursor()
        cur.execute(query)

        enum_types = {}

        for schema, type_name, enum_value in cur.fetchall():
            enum_types.setdefault(type_name, []).append(enum_value)

        cur.close()
        return enum_types

    def fetch_database_collection_entities(
        self,
        database,
        collection,
        max_distinct=50,
        max_ratio=0.1,
        max_length=100,
    ):
        conn = self._db_connect(database)
        cur = conn.cursor()

        try:
            cur.execute(
                """
                SELECT table_name, column_name, data_type, udt_name
                FROM information_schema.columns
                WHERE table_schema = %s
                ORDER BY table_name, ordinal_position;
                """,
                (collection,),
            )

            rows = cur.fetchall()
            enum_types = self.fetch_enum_types(conn)

            schema = DataSchema()

            for table_name, column_name, data_type, udt_name in rows:
                if not schema.has_entity(table_name):
                    schema.add_entity(table_name)

                property_def = {"type": data_type, "values": []}

                if udt_name in enum_types:
                    property_def["enum"] = enum_types[udt_name]
                    property_def["values"] = enum_types[udt_name]

                if data_type.lower() in {
                    "character varying",
                    "varchar",
                    "character",
                    "text",
                    "name",
                }:
                    try:
                        cur.execute(
                            f"""
                            SELECT COUNT(DISTINCT "{column_name}"), COUNT(*)
                            FROM "{collection}"."{table_name}"
                            WHERE "{column_name}" IS NOT NULL;
                            """
                        )
                        distinct_count, total_count = cur.fetchone()

                        cur.execute(
                            f"""
                            SELECT AVG(LENGTH("{column_name}"))
                            FROM "{collection}"."{table_name}"
                            WHERE "{column_name}" IS NOT NULL;
                            """
                        )
                        avg_length = cur.fetchone()[0] or 0

                        is_categorical = (
                            distinct_count <= max_distinct
                            and (
                                total_count == 0
                                or distinct_count / total_count <= max_ratio
                            )
                            and avg_length <= max_length
                        )

                        if is_categorical:
                            cur.execute(
                                f"""
                                SELECT DISTINCT "{column_name}"
                                FROM "{collection}"."{table_name}"
                                WHERE "{column_name}" IS NOT NULL
                                LIMIT {max_distinct};
                                """
                            )
                            property_def["values"] = [row[0] for row in cur.fetchall()]

                    except Exception as e:
                        self.logger.warning(
                            f"Could not sample values for "
                            f"{collection}.{table_name}.{column_name}: {e}"
                        )

                schema.add_entity_property(table_name, column_name, property_def)

            return schema.get_entities()

        finally:
            cur.close()
            self._db_disconnect(conn)

    def fetch_database_collection_relations(self, database, collection):
        conn = self._db_connect(database)
        cur = conn.cursor()

        try:
            cur.execute(
                """
                SELECT
                    tc.constraint_name,
                    tc.table_name AS source_table,
                    kcu.column_name AS source_column,
                    ccu.table_name AS target_table,
                    ccu.column_name AS target_column
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                  ON ccu.constraint_name = tc.constraint_name
                 AND ccu.table_schema = tc.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = %s;
                """,
                (collection,),
            )

            relations = {}

            for (
                constraint_name,
                source_table,
                source_column,
                target_table,
                target_column,
            ) in cur.fetchall():
                relation_name = constraint_name

                relations[relation_name] = {
                    "properties": {
                        "constraint_name": constraint_name,
                        "source_entity": source_table,
                        "target_entity": target_table,
                        "foreign_keys_source_columns": [source_column],
                        "foreign_keys_target_columns": [target_column],
                    },
                    "contents": {
                        "attributes": {
                            source_column: {
                                "name": source_column,
                                "info": {"type": "FOREIGN_KEY", "values": []},
                            }
                        }
                    },
                }

            return relations

        finally:
            cur.close()
            self._db_disconnect(conn)

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
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s AND table_name = %s;
                """,
                (collection, entity),
            )
            exists = cur.fetchone() is not None

            if exists:
                if overwrite:
                    cur.execute(f'DROP TABLE IF EXISTS "{collection}"."{entity}" CASCADE;')
                else:
                    return {"status": "skipped"}

            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{collection}";')

            column_definitions = properties.get("cols_definition", [])
            if not column_definitions:
                raise ValueError("cols_definition is required")

            column_sql = []

            for col in column_definitions:
                col_sql = f'"{col["name"]}"'
                if "type" in col:
                    col_sql += f' {col["type"]}'
                if "misc" in col:
                    col_sql += f' {col["misc"]}'
                column_sql.append(col_sql)

            primary_key = properties.get("primary_key", [])
            if primary_key:
                cols = ", ".join([f'"{col}"' for col in primary_key])
                column_sql.append(f"PRIMARY KEY ({cols})")

            foreign_keys = properties.get("foreign_keys", [])
            for fk in foreign_keys:
                source_cols = ", ".join(
                    [f'"{col}"' for col in fk["foreign_keys_source_columns"]]
                )
                target_cols = ", ".join(
                    [f'"{col}"' for col in fk["foreign_keys_target_columns"]]
                )
                target_table = fk["foreign_keys_target_table"]
                column_sql.append(
                    f'FOREIGN KEY ({source_cols}) '
                    f'REFERENCES "{collection}"."{target_table}" ({target_cols})'
                )

            query = (
                f'CREATE TABLE "{collection}"."{entity}" '
                f'({", ".join(column_sql)});'
            )

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

    def fetch_entity_stats(
        self,
        database,
        collection,
        entity,
        row_sample_size=100,
    ):
        conn = self._db_connect(database)
        cur = conn.cursor()

        stats = {
            "row_count": None,
            "row_samples": [],
            "row_sample_size": 0,
            "row_samples_aligned": False,
        }

        try:
            cur.execute(f'SELECT COUNT(*) FROM "{collection}"."{entity}";')
            stats["row_count"] = cur.fetchone()[0]

            if stats["row_count"] and stats["row_count"] > 0:
                cur.execute(
                    f"""
                    SELECT *
                    FROM "{collection}"."{entity}"
                    TABLESAMPLE SYSTEM (1)
                    LIMIT {row_sample_size};
                    """
                )
                rows = cur.fetchall()

                if not rows:
                    cur.execute(
                        f"""
                        SELECT *
                        FROM "{collection}"."{entity}"
                        ORDER BY RANDOM()
                        LIMIT {row_sample_size};
                        """
                    )
                    rows = cur.fetchall()

                if rows:
                    columns = [desc[0] for desc in cur.description]
                    stats["row_samples"] = [
                        {columns[i]: row[i] for i in range(len(columns))}
                        for row in rows
                    ]
                    stats["row_sample_size"] = len(stats["row_samples"])
                    stats["row_samples_aligned"] = (
                        len(columns) >= 2 and len(rows) >= 2
                    )

        except Exception as e:
            self.logger.warning(
                f"Failed to fetch entity stats for {collection}.{entity}: {e}"
            )

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

        column = f'"{property_name}"'

        try:
            cur.execute(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = %s
                  AND column_name = %s;
                """,
                (collection, entity, property_name),
            )
            type_result = cur.fetchone()
            column_type = type_result[0] if type_result else None

            include_min_max = column_type in {
                "integer",
                "bigint",
                "smallint",
                "numeric",
                "real",
                "double precision",
                "date",
                "timestamp without time zone",
                "timestamp with time zone",
                "boolean",
            }

            query = f"""
                SELECT
                    COUNT({column}) AS non_null_count,
                    COUNT(DISTINCT {column}) AS distinct_count,
                    COUNT(*) FILTER (WHERE {column} IS NULL) AS null_count,
                    ARRAY(
                        SELECT DISTINCT {column}
                        FROM "{collection}"."{entity}"
                        WHERE {column} IS NOT NULL
                        LIMIT {sample_limit}
                    )::text[] AS sample_values
            """

            if include_min_max:
                query += f""",
                    MIN({column})::text AS min_value,
                    MAX({column})::text AS max_value
                """
            else:
                query += ", NULL AS min_value, NULL AS max_value"

            query += f' FROM "{collection}"."{entity}";'

            cur.execute(query)
            row = cur.fetchone()

            stats = {
                "count": row[0],
                "distinct_count": row[1],
                "null_count": row[2],
                "sample_values": row[3],
                "min": row[4],
                "max": row[5],
            }

            cur.execute(
                """
                SELECT most_common_vals
                FROM pg_stats
                WHERE schemaname = %s
                  AND tablename = %s
                  AND attname = %s;
                """,
                (collection, entity, property_name),
            )
            mc_row = cur.fetchone()
            stats["most_common_vals"] = mc_row[0] if mc_row and mc_row[0] else []

            return stats

        except Exception as e:
            self.logger.warning(
                f"Failed to fetch property stats for "
                f"{collection}.{entity}.{property_name}: {e}"
            )
            return {}

        finally:
            cur.close()
            self._db_disconnect(conn)

