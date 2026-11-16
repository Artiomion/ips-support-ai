from ips.core.export_openapi import compare_contracts


def operation(schema):
    return {
        "operationId": "createThing",
        "requestBody": {"required": True, "content": {"application/json": {"schema": schema}}},
        "responses": {
            "201": {"description": "ok", "content": {"application/json": {"schema": schema}}}
        },
    }


def test_detects_request_and_response_schema_drift():
    expected = {"paths": {"/api/v1/things": {"post": operation({"type": "string"})}}}
    actual = {"paths": {"/api/v1/things": {"post": operation({"type": "integer"})}}}
    problems = compare_contracts(expected, actual)
    assert any("request" in item for item in problems)
    assert any("201" in item for item in problems)


def test_rejects_undocumented_api_operation():
    expected = {"paths": {}}
    actual = {"paths": {"/api/v1/things": {"post": operation({"type": "string"})}}}
    assert any("undocumented" in item for item in compare_contracts(expected, actual))


def test_unimplemented_operations_allowed_until_required():
    expected = {
        "paths": {"/api/v1/things": {"post": {**operation({"type": "string"}), "x-phase": "M4"}}}
    }
    assert compare_contracts(expected, {"paths": {}}) == []
    assert any(
        "missing" in item for item in compare_contracts(expected, {"paths": {}}, require_phase="M4")
    )


def test_detects_missing_declared_error_status():
    planned = operation({"type": "string"})
    planned["responses"]["401"] = {"description": "unauthorized"}
    expected = {"paths": {"/api/v1/things": {"post": planned}}}
    actual = {"paths": {"/api/v1/things": {"post": operation({"type": "string"})}}}
    assert any("401" in item for item in compare_contracts(expected, actual))


def test_schema_required_order_and_const_are_equivalent():
    expected_schema = {
        "type": "object",
        "required": ["a", "b"],
        "properties": {"a": {"type": "string", "enum": ["x"]}, "b": {"type": "string"}},
    }
    actual_schema = {
        "type": "object",
        "required": ["b", "a"],
        "properties": {"a": {"type": "string", "const": "x"}, "b": {"type": "string"}},
    }
    expected = {"paths": {"/api/v1/things": {"post": operation(expected_schema)}}}
    actual = {"paths": {"/api/v1/things": {"post": operation(actual_schema)}}}
    assert compare_contracts(expected, actual) == []


def test_detects_security_scheme_drift():
    planned = operation({"type": "string"})
    planned["security"] = [{"staffCookie": []}]
    observed = operation({"type": "string"})
    observed["security"] = []
    assert any(
        "security" in item
        for item in compare_contracts(
            {"paths": {"/api/v1/things": {"post": planned}}},
            {"paths": {"/api/v1/things": {"post": observed}}},
        )
    )
