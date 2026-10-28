"""Bounded JSON and the bundled JSON Schema subset, with no external schema resolution."""

import json
import math
import os
import re
import tempfile
from importlib.resources import files

MAX_BYTES = 1_048_576
MAX_DEPTH = 64
KEYWORDS = {
    "$schema",
    "$id",
    "title",
    "description",
    "type",
    "properties",
    "required",
    "items",
    "const",
    "enum",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "pattern",
}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f"Invalid JSON constant {value}")


def _bounds(value, depth=0):
    if isinstance(value, (dict, list)):
        if depth >= MAX_DEPTH:
            raise ValueError("Mission JSON exceeds depth 64")
        if isinstance(value, dict):
            for key in value:
                key.encode("utf-8", errors="strict")
        for child in value.values() if isinstance(value, dict) else value:
            _bounds(child, depth + 1)
    elif isinstance(value, (float, int)) and not isinstance(value, bool):
        try:
            if not math.isfinite(value):
                raise ValueError("Non-finite JSON number")
        except OverflowError as exc:
            raise ValueError("Non-finite JSON number") from exc
    elif isinstance(value, str):
        value.encode("utf-8", errors="strict")


def parse(data):
    if len(data) > MAX_BYTES:
        raise ValueError("Mission JSON exceeds 1 MiB")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)
        if not isinstance(value, dict):
            raise ValueError("Mission JSON root must be an object")  # noqa: TRY004 -- invalid document value
        _bounds(value)
        return value
    except (UnicodeError, RecursionError, OverflowError) as exc:
        raise ValueError("Invalid mission JSON") from exc


def encode(value):
    _bounds(value)
    data = (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    parse(data)
    return data


def resource(name):
    if re.fullmatch(r"[a-z0-9.-]+\.json", name) is None:
        raise ValueError("Invalid resource name")
    return parse(files(__package__).joinpath("data", "v1", name).read_bytes())


def write_atomic(path, value):
    data = encode(value)
    path = os.path.abspath(path)
    fd, temporary = tempfile.mkstemp(prefix=".mission-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _check_schema(schema):
    if set(schema) - KEYWORDS:
        raise ValueError(f"Unsupported schema keywords: {sorted(set(schema) - KEYWORDS)}")
    for child in schema.get("properties", {}).values():
        _check_schema(child)
    if "items" in schema:
        _check_schema(schema["items"])


def _type(value, kind):
    checks = {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "null": lambda: value is None,
        "boolean": lambda: isinstance(value, bool),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": lambda: (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value == int(value)
        ),
    }
    if kind not in checks:
        raise ValueError(f"Unsupported schema type {kind}")
    return checks[kind]()


def validate(schema, value):
    """Return stable JSON-pointer/code/message issues; preserve unknown optional object members."""
    _check_schema(schema)
    issues = []

    def visit(s, v, path):
        def fail(code, message):
            issues.append({"path": path, "code": code, "message": message})

        kinds = s.get("type")
        if kinds is not None and not any(_type(v, k) for k in ([kinds] if isinstance(kinds, str) else kinds)):
            fail("type", f"Expected {kinds}")
            return
        if "const" in s and v != s["const"]:
            fail("const", "Unsupported contract value")
        if "enum" in s and v not in s["enum"]:
            fail("enum", "Unsupported choice")
        if isinstance(v, dict):
            escape = lambda key: key.replace("~", "~0").replace("/", "~1")
            for key in s.get("required", []):
                if key not in v:
                    issues.append(
                        {"path": path + "/" + escape(key), "code": "required", "message": "Missing field"}
                    )
            for key, child in s.get("properties", {}).items():
                if key in v:
                    visit(child, v[key], path + "/" + escape(key))
        elif isinstance(v, list):
            if not s.get("minItems", 0) <= len(v) <= s.get("maxItems", len(v)):
                fail("items", "Array length is out of bounds")
            if "items" in s:
                for i, child in enumerate(v):
                    visit(s["items"], child, path + "/" + str(i))
        elif isinstance(v, str):
            if not s.get("minLength", 0) <= len(v) <= s.get("maxLength", len(v)):
                fail("length", "String length is out of bounds")
            if "pattern" in s and re.search(s["pattern"], v) is None:
                fail("pattern", "Invalid field syntax")
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            if not math.isfinite(v):
                fail("finite", "A finite number is required")
            if "minimum" in s and v < s["minimum"]:
                fail("minimum", "Below minimum")
            if "maximum" in s and v > s["maximum"]:
                fail("maximum", "Above maximum")

    visit(schema, value, "")
    return issues
