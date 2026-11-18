"""Read-only reconciliation of goal evidence and recorded match score."""

import argparse
import json
from pathlib import Path

from sqlalchemy import create_engine

from football_analytics.ingestion.match_score_verification import verify_match_score


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--match-id", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if args.match_id <= 0:
        parser.error("--match-id must be positive")

    engine = create_engine(args.database_url)
    try:
        with engine.connect() as connection:
            report = verify_match_score(connection, match_id=args.match_id)
    finally:
        engine.dispose()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = {key: value for key, value in report.items() if key != "reconciliation"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
