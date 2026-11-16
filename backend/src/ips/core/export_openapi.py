"""Compare implemented API operations against the agreed OpenAPI contract."""

import argparse
import json
from pathlib import Path
from typing import Any

from ips.main import create_app


def _resolve(value: Any, document: dict[str, Any], seen: frozenset[str] = frozenset()) -> Any:
    if isinstance(value, list):
        return [_resolve(item, document, seen) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        ref = value["$ref"]
        if not ref.startswith("#/") or ref in seen:
            raise ValueError(f"Unsupported or recursive reference: {ref}")
        target: Any = document
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return _resolve(target, document, seen | {ref})
    ignored = {
        "title",
        "description",
        "examples",
        "example",
        "summary",
        "x-owner",
        "x-phase",
        "x-task",
    }
    result = {
        key: _resolve(item, document, seen)
        for key, item in sorted(value.items())
        if key not in ignored
    }
    if "const" in result:
        result["enum"] = [result.pop("const")]
    if isinstance(result.get("required"), list):
        if result["required"]:
            result["required"] = sorted(result["required"])
        else:
            del result["required"]
    return result


def _operations(document: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (path, method): op
        for path, methods in document.get("paths", {}).items()
        if path.startswith("/api/v1/")
        for method, op in methods.items()
        if method in {"get", "post", "put", "patch", "delete"}
    }


def compare_contracts(
    expected: dict[str, Any], actual: dict[str, Any], require_phase: str | None = None
) -> list[str]:
    errors: list[str] = []
    planned, implemented = _operations(expected), _operations(actual)
    for key, operation in implemented.items():
        label = f"{key[1].upper()} {key[0]}"
        if key not in planned:
            errors.append(f"{label}: undocumented operation")
            continue
        wanted = planned[key]
        if operation.get("operationId") != wanted.get("operationId"):
            errors.append(f"{label}: operationId differs")
        if operation.get("security", []) != wanted.get("security", []):
            errors.append(f"{label}: security differs")
        for section in ("parameters", "requestBody"):
            if _resolve(operation.get(section), actual) != _resolve(wanted.get(section), expected):
                errors.append(f"{label}: request {section} differs")
        actual_responses = operation.get("responses", {})
        expected_responses = wanted.get("responses", {})
        for status in sorted(set(actual_responses) | set(expected_responses)):
            if status not in expected_responses or status not in actual_responses:
                errors.append(f"{label}: status {status} differs")
            elif _resolve(actual_responses[status], actual) != _resolve(
                expected_responses[status], expected
            ):
                errors.append(f"{label}: response {status} differs")
    if require_phase:
        phases = {"M4"} if require_phase == "M4" else {"M4", "M5"}
        for key, operation in planned.items():
            if operation.get("x-phase") in phases and key not in implemented:
                errors.append(f"{key[1].upper()} {key[0]}: missing operation")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", required=True, type=Path)
    parser.add_argument("--require-phase", choices=["M4", "M5"])
    args = parser.parse_args()
    expected = json.loads(args.check.read_text())
    actual = create_app().openapi()
    problems = compare_contracts(expected, actual, args.require_phase)
    for problem in problems:
        print(problem)
    count = len(_operations(actual))
    print(
        f"Checked {count} implemented API operations; missing operations are allowed unless --require-phase is set."
    )
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
