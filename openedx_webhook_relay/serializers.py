"""
Serialization of openedx-events payloads to JSON-safe structures.

openedx-events data classes are ``attrs`` classes that can nest opaque keys
(``CourseKey``, ``UsageKey``, ...) and XBlock ``ScopeIds``, neither of which
are JSON-serializable by default. This module converts them into plain
dicts/lists/primitives.
"""

from typing import Any, Union

from opaque_keys import OpaqueKey
from xblock.fields import ScopeIds

MAX_SERIALIZATION_DEPTH = 15


def scope_ids_serializer(scope_ids: ScopeIds) -> dict:
    return {
        "block_type": scope_ids.block_type,
        "def_id": str(scope_ids.def_id),
        "usage_id": str(scope_ids.usage_id),
        "user_id": scope_ids.user_id,
    }


def object_serializer(obj: Any, depth: int = 0) -> Union[dict, list, Any]:
    """Recursively serialize an Open edX event object to JSON-safe structures."""
    if depth > MAX_SERIALIZATION_DEPTH:
        return "! Depth limit reached !"
    if isinstance(obj, (int, float, str, bool)) or obj is None:
        return obj
    if isinstance(obj, ScopeIds):
        return scope_ids_serializer(obj)
    if isinstance(obj, OpaqueKey):
        return str(obj)
    if isinstance(obj, (list, tuple, set)):
        return [object_serializer(item, depth + 1) for item in obj]

    if isinstance(obj, dict):
        dict_values = obj.copy()
    elif hasattr(obj, "__dict__"):
        dict_values = obj.__dict__.copy()
    elif hasattr(obj, "__str__"):
        return str(obj)
    else:
        return f"Unserializable {type(obj)}"

    return_value = {}
    for key, value in dict_values.items():
        if isinstance(key, str) and not key.startswith("_"):
            return_value[key] = object_serializer(value, depth + 1)
    return return_value


def value_serializer(inst, field, value):  # pylint: disable=unused-argument
    """``attrs.asdict(..., value_serializer=value_serializer)`` hook."""
    return object_serializer(value)
