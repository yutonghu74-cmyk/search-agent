"""Review interpretation JSON; does not call an LLM or execute the request."""
import argparse
import json
from pathlib import Path

from dsh_retrieval.intent_review import IntentReviewService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--interpretation", type=Path, help="Explicit candidate JSON, not automatically inferred")
    args = parser.parse_args()
    proposal = json.loads(args.interpretation.read_text()) if args.interpretation else None
    print(json.dumps(IntentReviewService().review(args.request, proposal), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
