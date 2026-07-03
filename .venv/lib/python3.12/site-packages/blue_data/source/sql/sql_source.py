import json
from abc import ABC, abstractmethod

import numpy as np
import pandas as pd

from blue_data.source.source import DataSource


class SQLSource(DataSource, ABC):
    """Base class for SQL data sources."""

    @abstractmethod
    def _db_connect(self, database):
        raise NotImplementedError

    def _db_disconnect(self, connection):
        if connection:
            connection.close()

    def fetch_collection_stats(
        self,
        database,
        collection,
        entities=None,
        relations=None,
        sample_limit=None,
    ):
        return {
            "num_entities": len(entities or []),
            "num_relations": len(relations or []),
        }

    def execute_query(
        self,
        query,
        database=None,
        collection=None,
        optional_properties=None,
    ):
        optional_properties = optional_properties or {}
        if database is None:
            raise Exception("No database provided")
        conn = self._db_connect(database)
        cur = conn.cursor()
        try:
            cur.execute(query)
            result = []
            if cur.description:
                data = cur.fetchall()
                columns = [desc[0] for desc in cur.description]
                df = pd.DataFrame(data, columns=columns)
                df.fillna(value=np.nan, inplace=True)
                result = json.loads(df.to_json(orient="records"))
            if optional_properties.get("commit"):
                conn.commit()
            return result
        finally:
            cur.close()
            self._db_disconnect(conn)
