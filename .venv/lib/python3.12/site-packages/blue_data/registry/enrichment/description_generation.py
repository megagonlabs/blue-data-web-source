from __future__ import annotations


class DescriptionGenerator:
    """Lightweight description generation scaffold."""

    def generate_attribute_description(self, attribute: dict) -> str:
        name = attribute.get("name", "unknown")
        props = attribute.get("properties", {})
        info = props.get("info", {})
        dtype = info.get("type", "unknown")
        return f"Attribute `{name}` with type `{dtype}`."
