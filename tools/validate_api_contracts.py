"""Validate actual HTTP smoke payloads against OpenAPI (requires yaml/jsonschema).

Run with var/dj-runtime/bin/python after tools/api_smoke.py. No network refs.
"""
import json
from pathlib import Path
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


def main():
    root = Path(__file__).resolve().parents[1]
    spec = yaml.safe_load((root / "contracts/openapi.yaml").read_text())
    query = json.loads((root / "contracts/query-spec-v2.schema.json").read_text())
    base = "https://contracts.invalid/openapi.yaml"
    registry = Registry().with_resources([
        (base, Resource.from_contents(spec, default_specification=DRAFT202012)),
        ("https://contracts.invalid/query-spec-v2.schema.json", Resource.from_contents(query)),
    ])
    for schema in spec["components"]["schemas"].values():
        Draft202012Validator.check_schema(schema)
    Draft202012Validator.check_schema(query)
    payloads = json.loads((root / "var/api-smoke.json").read_text())
    for name, schema in (("draft_request", "DraftBody"), ("draft_response", "DraftSummary"),
                         ("confirm_request", "CreateTaskRequest"), ("task_response", "TaskSummary")):
        Draft202012Validator({"$ref": f"{base}#/components/schemas/{schema}"}, registry=registry).validate(payloads[name])
    print("PASS: actual HTTP payloads match OpenAPI and QuerySpec v2")


if __name__ == "__main__":
    main()
