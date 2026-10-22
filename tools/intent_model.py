"""Explicit real model call using existing DSH configuration; prints no secrets."""
import argparse
import json

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.intent_model import IntentModelClient, load_dsh_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--yaml-python", help="Existing interpreter with YAML support, if required")
    args = parser.parse_args()
    try:
        result = IntentModelClient(load_dsh_config(args.profile, args.credentials, yaml_python=args.yaml_python)).interpret(args.request)
    except ServiceError as error:
        print(json.dumps({"status": "FAILED", "code": error.code, "message": error.message}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
