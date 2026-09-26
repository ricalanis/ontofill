"""Load and validate the schema files that define the cross-repo contract."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


def schema_dir() -> Path:
    packaged = Path(__file__).resolve().parent / "schemas"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[2] / "schemas"


def load_schema(name: str) -> dict:
    if not name.replace("-", "").isalnum():
        raise ValueError("invalid schema name")
    return json.loads((schema_dir() / f"{name}.schema.json").read_text(encoding="utf-8"))


def validate_document(name: str, document: object) -> None:
    registry = Registry()
    for path in schema_dir().glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    Draft202012Validator(
        load_schema(name), registry=registry, format_checker=FormatChecker()
    ).validate(document)
