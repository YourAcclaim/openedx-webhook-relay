"""Unit tests for openedx-events payload serialization."""

from opaque_keys.edx.keys import CourseKey
from xblock.fields import ScopeIds

from openedx_webhook_relay.serializers import object_serializer, scope_ids_serializer


def test_object_serializer_primitives():
    assert object_serializer(1) == 1
    assert object_serializer("x") == "x"
    assert object_serializer(True) is True
    assert object_serializer(None) is None


def test_object_serializer_collections():
    assert object_serializer([1, "a", True]) == [1, "a", True]
    assert object_serializer((1, 2)) == [1, 2]
    assert set(object_serializer({1, 2})) == {1, 2}


def test_object_serializer_opaque_key():
    key = CourseKey.from_string("course-v1:Org+Course+Run")
    assert object_serializer(key) == str(key)


def test_scope_ids_serializer():
    scope_ids = ScopeIds("user123", "html", "def-id", "usage-id")
    result = scope_ids_serializer(scope_ids)
    assert result == {
        "block_type": "html",
        "def_id": "def-id",
        "usage_id": "usage-id",
        "user_id": "user123",
    }
    assert object_serializer(scope_ids) == result


def test_object_serializer_nested_object_with_dunder_dict():
    class Nested:
        def __init__(self):
            self.visible = "yes"
            self._hidden = "no"

    class Outer:
        def __init__(self):
            self.nested = Nested()
            self.values = [1, 2, 3]

    result = object_serializer(Outer())
    assert result == {"nested": {"visible": "yes"}, "values": [1, 2, 3]}


def test_object_serializer_depth_limit():
    class Wrapper:
        def __init__(self, inner=None):
            self.inner = inner

    root = None
    for _ in range(20):
        root = Wrapper(root)

    result = object_serializer(root)
    # Walk down until we hit the depth-limit sentinel instead of a dict.
    depth_limited = False
    node = result
    for _ in range(25):
        if isinstance(node, dict) and "inner" in node:
            node = node["inner"]
        else:
            depth_limited = node == "! Depth limit reached !"
            break
    assert depth_limited


def test_object_serializer_unserializable_falls_back_to_str():
    class NoDict:
        __slots__ = ()

        def __str__(self):
            return "stringified"

    assert object_serializer(NoDict()) == "stringified"
