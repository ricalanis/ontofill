"""Safe, ontology-bound mappings from sandbox-parsed nested records."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_MAX_PATH_LENGTH = 512
_MAX_PATH_SEGMENTS = 24


@dataclass(frozen=True)
class RecordField:
    """One scalar in a nested source record and its exact structural selector."""

    property_id: str
    value: str | int | float | bool
    selector: str


@dataclass(frozen=True)
class RecordInstance:
    """A class instance found in one bounded source record."""

    class_id: str
    identifier: str | int | float | bool
    row_number: int
    selector: str
    fields: tuple[RecordField, ...]
    gaps: tuple[str, ...]


def record_mapping_model_schema(ontology: dict, tdd: dict) -> dict:
    """Schema for model-proposed class and property JSON Pointer paths."""
    property_ids = [item["id"] for item in ontology["properties"]]
    properties = {item["id"]: item for item in ontology["properties"]}
    target_classes = {
        properties[property_id]["domain"]
        for property_id in tdd.get("target_fields", [])
        if property_id in properties
    }
    relevant_classes = set(target_classes)
    for relation in ontology.get("relations", []):
        if relation["domain"] in target_classes or relation["range"] in target_classes:
            relevant_classes.update((relation["domain"], relation["range"]))
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["classes"],
        "properties": {
            "classes": {
                "type": "array",
                "minItems": 1,
                "maxItems": len(relevant_classes),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "class_id",
                        "collection_path",
                        "identifier_property",
                        "properties",
                    ],
                    "properties": {
                        "class_id": {"enum": sorted(relevant_classes)},
                        "collection_path": {"type": "string", "maxLength": _MAX_PATH_LENGTH},
                        "identifier_property": {"enum": property_ids},
                        "properties": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": len(property_ids),
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["property_id", "path"],
                                "properties": {
                                    "property_id": {"enum": property_ids},
                                    "path": {"type": "string", "maxLength": _MAX_PATH_LENGTH},
                                },
                            },
                        },
                    },
                },
            }
        },
    }


def validate_record_mapping(mapping: dict, ontology: dict, tdd: dict) -> None:
    """Reject paths and ontology references that cannot safely emit observations."""
    if not isinstance(mapping, dict) or set(mapping) != {"classes"}:
        raise ValueError("record mapping must contain only classes")
    classes = {item["id"]: item for item in ontology["classes"]}
    properties = {item["id"]: item for item in ontology["properties"]}
    if not isinstance(mapping["classes"], list) or not mapping["classes"]:
        raise ValueError("record mapping must name at least one class")
    if len(mapping["classes"]) > len(classes):
        raise ValueError("record mapping contains too many classes")

    mapped_classes: dict[str, dict[str, str]] = {}
    requested = set(tdd.get("target_fields", []))
    if not requested <= set(properties):
        raise ValueError("TDD target field is absent from the ontology")
    target_classes = {properties[property_id]["domain"] for property_id in requested}
    relevant_classes = set(target_classes)
    for relation in ontology.get("relations", []):
        if relation["domain"] in target_classes or relation["range"] in target_classes:
            relevant_classes.update((relation["domain"], relation["range"]))
    allowed = requested | {
        item["identifier_property"] for item in classes.values() if item.get("identifier_property")
    }
    for relation in ontology.get("relations", []):
        match = relation.get("match")
        if match:
            allowed.update((match["domain_property"], match["range_property"]))

    for item in mapping["classes"]:
        if not isinstance(item, dict) or set(item) != {
            "class_id",
            "collection_path",
            "identifier_property",
            "properties",
        }:
            raise ValueError("class mapping has an invalid shape")
        class_id = item["class_id"]
        if class_id not in relevant_classes or class_id in mapped_classes:
            raise ValueError("record mapping names an unknown or duplicate class")
        identity_property = classes[class_id]["identifier_property"]
        if item["identifier_property"] != identity_property:
            raise ValueError("record mapping identifier must match the ontology class")
        collection_path = item["collection_path"]
        _pointer_tokens(collection_path)
        if not isinstance(item["properties"], list) or not item["properties"]:
            raise ValueError("class mapping must contain properties")
        field_paths: dict[str, str] = {}
        for field in item["properties"]:
            if not isinstance(field, dict) or set(field) != {"property_id", "path"}:
                raise ValueError("property mapping has an invalid shape")
            property_id, path = field["property_id"], field["path"]
            if property_id in field_paths:
                raise ValueError("record mapping repeats a property")
            prop = properties.get(property_id)
            if prop is None or prop["domain"] != class_id:
                raise ValueError("record mapping property is outside its class")
            if property_id not in allowed:
                raise ValueError("record mapping property is outside the approved TDD")
            _pointer_tokens(path)
            field_paths[property_id] = path
        if identity_property not in field_paths:
            raise ValueError("record mapping omits the class identifier property")
        mapped_classes[class_id] = field_paths

    mapped_targets = {
        property_id
        for fields in mapped_classes.values()
        for property_id in fields
        if property_id in requested
    }
    if not mapped_targets:
        raise ValueError("record mapping does not include an approved target field")

    for relation in ontology.get("relations", []):
        match = relation.get("match")
        if not match:
            continue
        domain_fields = mapped_classes.get(relation["domain"])
        range_fields = mapped_classes.get(relation["range"])
        if (
            domain_fields is not None
            and range_fields is not None
            and (
                match["domain_property"] not in domain_fields
                or match["range_property"] not in range_fields
            )
        ):
            raise ValueError("record mapping omits a declared relation match property")


def record_shape(records: Sequence[dict], *, sample_size: int = 3) -> list[dict]:
    """Return structural input shape without copying source values into a cache key."""
    return [_shape(record.get("value")) for record in records[:sample_size]]


def extract_record_instances(records: Sequence[dict], mapping: dict) -> list[RecordInstance]:
    """Apply validated JSON Pointer paths and keep a receipt for every emitted field."""
    results: list[RecordInstance] = []
    for source_record in records:
        if not isinstance(source_record, Mapping):
            continue
        row_number = source_record.get("row_number")
        record_path = source_record.get("path")
        root = source_record.get("value")
        if (
            isinstance(row_number, bool)
            or not isinstance(row_number, int)
            or row_number < 1
            or not isinstance(record_path, str)
            or not isinstance(root, dict)
        ):
            continue
        for class_map in mapping["classes"]:
            class_id = class_map["class_id"]
            for instance, instance_path in _pointer_values(root, class_map["collection_path"]):
                if not isinstance(instance, dict):
                    continue
                fields: list[RecordField] = []
                gaps: list[str] = []
                raw_fields: dict[str, tuple[Any, ...]] = {}
                actual_paths: dict[str, str] = {}
                for field_map in class_map["properties"]:
                    matches = _pointer_values(instance, field_map["path"])
                    scalar_matches = [
                        (value, path)
                        for value, path in matches
                        if value is not None and not isinstance(value, (dict, list))
                    ]
                    raw_fields[field_map["property_id"]] = tuple(
                        value for value, _path in scalar_matches
                    )
                    actual_paths[field_map["property_id"]] = (
                        scalar_matches[0][1] if len(scalar_matches) == 1 else ""
                    )
                identity_value = raw_fields[class_map["identifier_property"]]
                if len(identity_value) != 1:
                    gaps.append("missing_or_ambiguous_identifier")
                    continue
                for field_map in class_map["properties"]:
                    property_id = field_map["property_id"]
                    values = raw_fields[property_id]
                    if len(values) == 1:
                        selector = _join_pointer(
                            _join_pointer(record_path, instance_path), actual_paths[property_id]
                        )
                        fields.append(RecordField(property_id, values[0], selector))
                    elif len(values) > 1:
                        gaps.append(f"ambiguous_multiple_values:{property_id}")
                identifier = identity_value[0]
                if not isinstance(identifier, (str, int, float, bool)):
                    gaps.append("invalid_identifier_type")
                    continue
                results.append(
                    RecordInstance(
                        class_id=class_id,
                        identifier=identifier,
                        row_number=row_number,
                        selector=_join_pointer(record_path, instance_path),
                        fields=tuple(fields),
                        gaps=tuple(sorted(set(gaps))),
                    )
                )
    return results


def validate_record_mapping_against_records(
    mapping: dict, records: Sequence[dict], ontology: dict, tdd: dict
) -> None:
    """Require generated paths to select sampled objects and an observed target value."""
    identity_by_class = {item["id"]: item["identifier_property"] for item in ontology["classes"]}
    target_ids = set(tdd.get("target_fields", []))
    relation_property_ids = {
        property_id
        for relation in ontology.get("relations", [])
        if relation.get("match")
        for property_id in (
            relation["match"]["domain_property"],
            relation["match"]["range_property"],
        )
    }
    found_target = False
    found_class_identity: set[str] = set()
    for source_record in records[:3]:
        if not isinstance(source_record, Mapping) or not isinstance(
            source_record.get("value"), dict
        ):
            continue
        root = source_record["value"]
        for class_map in mapping["classes"]:
            instances = [
                value
                for value, _path in _pointer_values(root, class_map["collection_path"])
                if isinstance(value, dict)
            ]
            if not instances:
                continue
            for instance in instances:
                found_identity = False
                for field_map in class_map["properties"]:
                    property_id = field_map["property_id"]
                    scalar_values = [
                        value
                        for value, _path in _pointer_values(instance, field_map["path"])
                        if value is not None and not isinstance(value, (dict, list))
                    ]
                    if property_id == identity_by_class[class_map["class_id"]]:
                        found_identity = len(scalar_values) == 1
                    if property_id in target_ids and len(scalar_values) == 1:
                        found_target = True
                    if property_id in target_ids and len(scalar_values) > 1:
                        raise ValueError(
                            f"record mapping target `{property_id}` has multiple scalar values"
                        )
                    if property_id in relation_property_ids and len(scalar_values) > 1:
                        raise ValueError(
                            f"record mapping relation property `{property_id}` is multi-valued"
                        )
                if found_identity:
                    found_class_identity.add(class_map["class_id"])
    if found_class_identity != {item["class_id"] for item in mapping["classes"]}:
        raise ValueError("record mapping identifier path is missing or ambiguous in the samples")
    if not found_target:
        raise ValueError("record mapping paths find no scalar property in the supplied samples")


def _pointer_tokens(pointer: object) -> tuple[str, ...]:
    if not isinstance(pointer, str) or len(pointer) > _MAX_PATH_LENGTH:
        raise ValueError("record mapping path must be a bounded JSON Pointer")
    if pointer == "":
        return ()
    if not pointer.startswith("/"):
        raise ValueError("record mapping path must be a JSON Pointer")
    tokens = pointer[1:].split("/")
    if len(tokens) > _MAX_PATH_SEGMENTS:
        raise ValueError("record mapping path is too deep")
    for token in tokens:
        index = 0
        while index < len(token):
            if token[index] == "~":
                if index + 1 >= len(token) or token[index + 1] not in "01":
                    raise ValueError("record mapping path has invalid JSON Pointer escaping")
                index += 2
            else:
                index += 1
    return tuple(token.replace("~1", "/").replace("~0", "~") for token in tokens)


def _pointer_values(root: Any, pointer: str) -> list[tuple[Any, str]]:
    tokens = _pointer_tokens(pointer)
    current: list[tuple[Any, tuple[str, ...]]] = [(root, ())]
    for token in tokens:
        next_values: list[tuple[Any, tuple[str, ...]]] = []
        for value, actual_tokens in current:
            if token == "*" and isinstance(value, list):
                next_values.extend(
                    (child, (*actual_tokens, str(index))) for index, child in enumerate(value)
                )
            elif isinstance(value, dict) and token in value:
                next_values.append((value[token], (*actual_tokens, token)))
            elif isinstance(value, list) and token.isdecimal():
                index = int(token)
                if index < len(value):
                    next_values.append((value[index], (*actual_tokens, token)))
        current = next_values
        if not current:
            break
    return [(value, _pointer_from_tokens(path)) for value, path in current]


def _pointer_from_tokens(tokens: tuple[str, ...]) -> str:
    return "".join("/" + token.replace("~", "~0").replace("/", "~1") for token in tokens)


def _join_pointer(base: str, child: str) -> str:
    if not child:
        return base
    if not base:
        return child
    if base.startswith("$line/"):
        return base.rstrip("/") + child
    return base.rstrip("/") + child


def _shape(value: Any, depth: int = 0) -> dict:
    if depth >= 10:
        return {"type": "depth_limit"}
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {
                key: _shape(value[key], depth + 1) for key in sorted(value) if isinstance(key, str)
            },
        }
    if isinstance(value, list):
        item_shapes = {
            json.dumps(shape, ensure_ascii=False, sort_keys=True): shape
            for shape in (_shape(item, depth + 1) for item in value[:3])
        }
        return {"type": "array", "items": [item_shapes[key] for key in sorted(item_shapes)]}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    return {"type": "unsupported"}
