"""CLI — run a single source file through the ingestion pipeline.

    python -m data.ingestion.run <file> --adapter workday
    python -m data.ingestion.run <file> --adapter engagement_survey:peakon

Writes into the active canonical DB (PULSESCORE_DB env override honored) and prints the
per-stage report. Validation failures exit non-zero with the full error list; the human
review queue (ambiguous / unknown-person records) is printed but never auto-merged.
"""
from __future__ import annotations

import argparse
import sys

from ..db import get_connection
from .adapters import get_adapter
from .pipeline import run_ingestion
from .validate import ValidationError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="data.ingestion.run", description=__doc__)
    parser.add_argument("file", help="source export to ingest")
    parser.add_argument("--adapter", required=True,
                        help="adapter name (e.g. workday or engagement_survey:peakon)")
    parser.add_argument("--min-rows", type=int, default=1,
                        help="fail if fewer than this many rows resolve (volume sanity)")
    args = parser.parse_args(argv)

    adapter = get_adapter(args.adapter)
    conn = get_connection()
    try:
        report = run_ingestion(conn, args.file, adapter, min_rows=args.min_rows)
    except ValidationError as exc:
        print(f"VALIDATION FAILED — nothing written ({len(exc.errors)} error(s)):",
              file=sys.stderr)
        for err in exc.errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    print(report.summary())
    for item in report.review_queue:
        print(f"  REVIEW [{item.reason}] candidates={item.candidate_tokens}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
