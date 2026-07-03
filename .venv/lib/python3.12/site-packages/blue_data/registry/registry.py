from __future__ import annotations

import json
from typing import Any

from blue_data.source.json.memory_json_source import MemoryJSONSource
from blue_data.core.utils.utils import deep_merge


class Registry:
    """Generic registry abstraction backed by a JSONSource."""

    def __init__(self, name="REGISTRY", store=None):
        self.name = name
        self.type = "record"
        self.store = store or MemoryJSONSource()

        if self.store.get("$") in (None, {}):
            self.store.set("$", {"contents": {}})

    def register_record(
        self,
        name,
        type,
        scope="/",
        description="",
        properties=None,
        created_by=None,
        icon=None,
        rebuild: bool | None = None,
    ):
        record = {
            "name": name,
            "type": type,
            "scope": scope,
            "description": description,
            "created_by": created_by,
            "properties": properties or {},
            "icon": icon,
            "contents": {},
        }

        key = self._record_key(name, type, scope)
        self.store.set(key, record)
        self._on_record_changed(record, rebuild=rebuild)
        return record

    def get_record(self, name, type, scope="/"):
        return self.store.get(self._record_key(name, type, scope))

    
    
    def list_records(
        self,
        record_type: str | None = None,
        scope: str | None = None,
        recursive: bool = False,
    ):
        records = [
            value
            for key, value in self.store.data.items()
            if (
                key != "$"
                and isinstance(value, dict)
                and "name" in value
                and "type" in value
                and "scope" in value
            )
        ]

        if record_type is not None:
            records = [r for r in records if r.get("type") == record_type]

        if scope is not None:
            if recursive:
                records = [
                    r
                    for r in records
                    if r.get("scope") == scope
                    or r.get("scope", "").startswith(scope.rstrip("/") + "/")
                ]
            else:
                records = [r for r in records if r.get("scope") == scope]

        return records
        

    def deregister_record(self, name, type, scope="/", rebuild: bool | None = None):
        self.store.delete(self._record_key(name, type, scope))
        self._on_record_deleted(name, type, scope, rebuild=rebuild)

    def _record_key(self, name, type, scope="/"):
        return f"{scope.rstrip('/')}/{type}/{name}"

    def get_record_properties(self, name, type, scope="/"):
        record = self.get_record(name, type, scope)
        return (record or {}).get("properties", {})

    def get_record_property(self, name, type, scope, key):
        return self.get_record_properties(name, type, scope).get(key)

    def set_record_property(self, name, type, scope, key, value, rebuild: bool | None = None):
        record = self.get_record(name, type, scope)
        if not record:
            raise ValueError(f"Record not found: {scope}/{type}/{name}")

        record.setdefault("properties", {})
        record["properties"][key] = value

        self.store.set(self._record_key(name, type, scope), record)
        self._on_record_changed(record, rebuild=rebuild)
        return record

    def get_record_description(self, name, type, scope="/"):
        record = self.get_record(name, type, scope)
        return (record or {}).get("description")

    def set_record_description(self, name, type, scope, description, rebuild: bool | None = None):
        record = self.get_record(name, type, scope)
        if not record:
            raise ValueError(f"Record not found: {scope}/{type}/{name}")

        record["description"] = description
        self.store.set(self._record_key(name, type, scope), record)
        self._on_record_changed(record, rebuild=rebuild)
        return record

    def update_record(
        self,
        name,
        type,
        scope="/",
        description=None,
        properties=None,
        icon=None,
        rebuild: bool | None = None,
    ):
        original = self.get_record(name, type, scope)

        if not original:
            return self.register_record(
                name=name,
                type=type,
                scope=scope,
                description=description or "",
                properties=properties or {},
                icon=icon,
                rebuild=rebuild,
            )

        updated = dict(original)

        if description is not None:
            updated["description"] = description

        if icon is not None:
            updated["icon"] = icon

        if properties is not None:
            updated["properties"] = deep_merge(
                updated.get("properties") or {}, properties
            )

        self.store.set(self._record_key(name, type, scope), updated)
        self._on_record_changed(updated, rebuild=rebuild)
        return original, updated

    def deregister(self, record, rebuild: bool | None = None):
        if not record:
            return None

        return self.deregister_record(
            record["name"],
            record["type"],
            record["scope"],
            rebuild=rebuild,
        )


    def filter_record_contents(
        self,
        name,
        type,
        scope,
        filter_type=None,
        filter_name=None,
        single=False,
    ):
        parent_key = self._record_key(name, type, scope)
        parent_prefix = parent_key.rstrip("/") + "/"

        matches = []

        for key, value in self.store.data.items():
            if key == "$" or not isinstance(value, dict):
                continue

            if not key.startswith(parent_prefix):
                continue

            if filter_type is not None and value.get("type") != filter_type:
                continue

            if filter_name is not None and value.get("name") != filter_name:
                continue

            matches.append(value)

        if single:
            return matches[0] if matches else None

        return matches


    def dumps(self):
        return json.dumps(self.list_records(recursive=True), indent=2)


    def dump(self, output_file):
        with open(output_file, "w") as fp:
            json.dump(self.list_records(recursive=True), fp, indent=2)


    def loads(self, input_string):
        records = json.loads(input_string)
        self._load_records(records)


    def load(self, input_file):
        with open(input_file, "r") as fp:
            records = json.load(fp)
        self._load_records(records)


    def _load_records(self, records):
        for record in records:
            self.register_record_json(record, rebuild=False)
        self.build_index()

    def delete_record_property(self, name, type, scope, key, rebuild: bool | None = None):
        record = self.get_record(name, type, scope)
        if not record:
            return None

        record.setdefault("properties", {})
        deleted = record["properties"].pop(key, None)

        self.store.set(self._record_key(name, type, scope), record)
        self._on_record_changed(record, rebuild=rebuild)
        return deleted
    
    def filter_records_by_properties(
        self,
        record_type: str | None = None,
        scope: str | None = None,
        properties: dict | None = None,
        recursive: bool = False,
        partial_match: bool = False,
    ):
        def match_props(record_props, filter_props):
            for key, expected in filter_props.items():
                actual = record_props.get(key)

                if isinstance(expected, dict):
                    if not isinstance(actual, dict):
                        return False
                    if not match_props(actual, expected):
                        return False
                else:
                    if partial_match:
                        if actual is None or str(expected) not in str(actual):
                            return False
                    else:
                        if actual != expected:
                            return False

            return True

        records = self.list_records(
            record_type=record_type,
            scope=scope,
            recursive=recursive,
        )

        if not properties:
            return records

        return [
            record
            for record in records
            if match_props(record.get("properties", {}) or {}, properties)
        ]
    
    def register_record_json(
        self,
        record: dict,
        recursive: bool = True,
        rebuild: bool | None = None,
    ):
        registered = self.register_record(
            name=record["name"],
            type=record.get("type", "default"),
            scope=record.get("scope", "/"),
            description=record.get("description", ""),
            properties=record.get("properties", {}),
            created_by=record.get("created_by"),
            icon=record.get("icon"),
            rebuild=rebuild,
        )

        if recursive:
            contents = record.get("contents", {}) or {}

            for records_by_name in contents.values():
                for child_record in records_by_name.values():
                    self.register_record_json(
                        child_record,
                        recursive=True,
                        rebuild=rebuild,
                    )

        return registered
    
    def get_record_contents(self, name, type, scope="/"):
        return self.filter_record_contents(
            name=name,
            type=type,
            scope=scope,
        )
    
    def parse_path(self, path: str):
        parts = path.split("/")[1:]
        parsed = {}

        keys = parts[::2]
        values = parts[1::2]

        for key, value in zip(keys, values):
            parsed[key] = value

        return parsed


    def get_contents(self):
        return self.store.get("$") or {"contents": {}}


    def get_records(self):
        records = []

        for value in self.store.data.values():
            if (
                isinstance(value, dict)
                and "name" in value
                and "type" in value
                and "scope" in value
            ):
                record = dict(value)
                record.pop("contents", None)
                records.append(record)

        return records
    
    encodings = {".": "__DOT__", "*": "__STAR__", "?": "__Q__"}

    def _encode(self, s):
        if not isinstance(s, str):
            return s

        for key, value in self.encodings.items():
            s = s.replace(key, value)

        return s


    def _decode(self, s):
        if not isinstance(s, str):
            return s

        for key, value in self.encodings.items():
            s = s.replace(value, key)

        return s


    def _encode_dict(self, obj):
        if isinstance(obj, dict):
            return {
                self._encode(key): self._encode_dict(value)
                for key, value in obj.items()
            }

        if isinstance(obj, list):
            return [self._encode_dict(value) for value in obj]

        return obj


    def _decode_dict(self, obj):
        if isinstance(obj, dict):
            return {
                self._decode(key): self._decode_dict(value)
                for key, value in obj.items()
            }

        if isinstance(obj, list):
            return [self._decode_dict(value) for value in obj]

        return obj


    def _extract_shortname(self, name: str):
        return str(name).split("/")[-1]


    def _derive_scope_from_name(self, name: str, full: bool = False):
        parts = str(name).split("/")

        if not full:
            parts = parts[:-1]

        if not parts:
            return "/"

        scope = ""
        for part in parts:
            if part:
                scope += f"/{self.type}/{part}"

        return scope or "/"

    
    def update_record_json(self, record: dict, rebuild: bool | None = None):
        return self.update_record(
            name=record["name"],
            type=record["type"],
            scope=record.get("scope", "/"),
            description=record.get("description"),
            properties=record.get("properties"),
            icon=record.get("icon"),
            rebuild=rebuild,
        )


    def build_index(self) -> None:
        """Bulk re-index all records. Override in subclasses that maintain a search index."""
        for record in self.list_records():
            self._on_record_changed(record, rebuild=True)

    # ---------- extension hooks ----------

    def _on_record_changed(self, record: dict, rebuild: bool | None = None) -> None:
        """Called after every record create/update. Override in subclasses.

        rebuild=True forces indexing; rebuild=False suppresses it;
        rebuild=None defers to the subclass default (e.g. DataRegistry._rebuild).
        """

    def _on_record_deleted(self, name: str, type: str, scope: str, rebuild: bool | None = None) -> None:
        """Called after every record delete. Override in subclasses."""

    def get_record_data(self, name: str, type: str, scope: str = "/", key: str | None = None):
        """Return a full record or a specific top-level key within it."""
        record = self.get_record(name, type, scope)
        if key is None or record is None:
            return record
        return record.get(key)

    def set_record_data(self, name: str, type: str, scope: str = "/", key: str | None = None, value=None, rebuild: bool | None = None):
        """Set an arbitrary top-level key on a record (or replace the whole record when key is None)."""
        record = self.get_record(name, type, scope)

        if record is None:
            raise KeyError(f"Record not found: {type}/{name} in {scope}")

        if key is None:
            record["data"] = value or {}
        else:
            record[key] = value

        self.store.set(self._record_key(name, type, scope), record)
        self._on_record_changed(record, rebuild=rebuild)
        return record

    def delete_record_data(self, name: str, type: str, scope: str = "/", key: str | None = None, rebuild: bool | None = None):
        """Delete an arbitrary top-level key from a record (defaults to the 'data' key)."""
        record = self.get_record(name, type, scope)

        if record is None:
            raise KeyError(f"Record not found: {type}/{name} in {scope}")

        record.pop(key or "data", None)

        self.store.set(self._record_key(name, type, scope), record)
        self._on_record_changed(record, rebuild=rebuild)
        return record

   