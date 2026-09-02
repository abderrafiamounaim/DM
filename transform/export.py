#!/usr/bin/env python3
# /// script
# dependencies = ["psycopg2-binary", "python-dotenv"]
# ///
"""
Run a SQL file against the PostgreSQL staging database and export the results to CSV.

Usage:
    uv run export.py sql/example_accounts.sql
    uv run export.py sql/example_accounts.sql --out output/accounts.csv
"""

import csv
import logging
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Load .env from the extract/ folder (sibling module, holds DATABASE_URL)
_env_path = Path(__file__).parent.parent / "extract" / ".env"
load_dotenv(_env_path)

DATABASE_URL = os.environ["DATABASE_URL"]


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    sql_file = None
    out_file = None
    i = 0
    while i < len(args):
        if args[i] == "--out" and i + 1 < len(args):
            out_file = Path(args[i + 1])
            i += 2
        else:
            sql_file = Path(args[i])
            i += 1

    if sql_file is None:
        logger.error("No SQL file provided.")
        sys.exit(1)

    if not sql_file.is_absolute():
        sql_file = Path(__file__).parent / sql_file

    if not sql_file.exists():
        logger.error("SQL file not found: %s", sql_file)
        sys.exit(1)

    if out_file is None:
        out_file = Path(__file__).parent / "output" / f"{sql_file.stem}.csv"

    out_file.parent.mkdir(parents=True, exist_ok=True)

    sql = sql_file.read_text(encoding="utf-8")
    logger.info("Running: %s", sql_file.name)

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
    finally:
        conn.close()

    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)

    logger.info("Exported %d rows to %s", len(rows), out_file)


if __name__ == "__main__":
    main()
