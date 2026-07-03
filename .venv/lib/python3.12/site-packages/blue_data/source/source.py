class DataSource:
    """Base abstraction for all Blue data sources."""

    def __init__(self, name: str, properties: dict | None = None):
        self.name = name
        self.properties = properties or {}

    # ---------- source ----------
    def fetch_metadata(self) -> dict:
        return {}

    def fetch_schema(self) -> dict:
        return {}

    # ---------- database ----------
    def fetch_databases(self) -> list[str]:
        return []

    def fetch_database_metadata(self, database: str) -> dict:
        return {}

    def fetch_database_schema(self, database: str) -> dict:
        return {}

    def create_database(self, database: str, properties: dict | None = None) -> dict:
        return {}

    # ---------- collection ----------
    def fetch_database_collections(self, database: str) -> list[str]:
        return []

    def fetch_database_collection_metadata(
        self,
        database: str,
        collection: str,
    ) -> dict:
        return {}

    def fetch_database_collection_entities(
        self,
        database: str,
        collection: str,
    ) -> dict:
        return {}

    def fetch_database_collection_relations(
        self,
        database: str,
        collection: str,
    ) -> dict:
        return {}

    def create_database_collection(
        self,
        database: str,
        collection: str,
        properties: dict | None = None,
    ) -> dict:
        return {}

    # ---------- entity / relation ----------
    def create_database_collection_entity(
        self,
        database: str,
        collection: str,
        entity: str,
        properties: dict | None = None,
    ) -> dict:
        return {}

    def create_database_collection_relation(
        self,
        database: str,
        collection: str,
        relation: str,
        properties: dict | None = None,
    ) -> dict:
        return {}

    # ---------- query ----------
    def execute_query(
        self,
        query,
        database: str | None = None,
        collection: str | None = None,
        optional_properties: dict | None = None,
    ) -> list[dict]:
        return []

    # ---------- stats ----------
    def fetch_source_stats(self) -> dict | None:
        return None

    def fetch_database_stats(self, database: str) -> dict | None:
        return None

    def fetch_collection_stats(
        self,
        database: str,
        collection: str,
        entities: list | None = None,
        relations: list | None = None,
        sample_limit: int | None = None,
    ) -> dict | None:
        return None

    def fetch_entity_stats(
        self,
        database: str,
        collection: str,
        entity: str,
    ) -> dict | None:
        return None

    def fetch_property_stats(
        self,
        database: str,
        collection: str,
        entity: str,
        property_name: str,
        sample_limit: int | None = None,
    ) -> dict | None:
        return None