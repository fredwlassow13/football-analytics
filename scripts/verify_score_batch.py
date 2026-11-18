"""Run score reconciliation for explicitly selected stored matches."""

import argparse
import json
from pathlib import Path

from sqlalchemy import create_engine

from football_analytics.ingestion.match_score_batch import verify_match_scores


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--match-ids", required=True, nargs="+", type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if any(match_id <= 0 for match_id in args.match_ids):
        parser.error("--match-ids must contain positive integers")
    if len(args.match_ids) != len(set(args.match_ids)):
        parser.error("--match-ids must not contain duplicates")

    engine = create_engine(args.database_url)
    try:
        with engine.connect() as connection:
            result = verify_match_scores(connection, args.match_ids)
    finally:
        engine.dispose()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "scope": result.scope,
                "requested": len(result.requested_match_ids),
                "success": result.success_count,
                "review": result.review_count,
                "failed": result.failed_count,
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if result.failed_count:
        raise SystemExit(1)
    if result.review_count:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
