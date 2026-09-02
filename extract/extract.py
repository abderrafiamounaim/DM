#!/usr/bin/env python3
# /// script
# dependencies = ["simple-salesforce", "psycopg2-binary", "python-dotenv", "pyyaml"]
# ///
"""
Source Salesforce -> PostgreSQL extraction script.

Fields (and an optional SOQL WHERE filter) per object are configured in scopes.yaml.
Records are staged as JSONB, one table per object — no assumption is made about
the target org's schema, so the same staging tables work for any downstream mapping.

Usage:
    uv run extract.py Account Contact
    uv run extract.py Opportunity --since 2026-01-01
"""

import logging
import sys
from datetime import datetime, timezone

import config
from db import ensure_table, get_connection, upsert_records
from sf_source import connect, fetch_all_records, fetch_records_since

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def extract_object(sf, database_url: str, object_type: str, scopes: dict, since_iso: str = None):
    logger.info("=" * 50)
    logger.info("Extracting: %s", object_type)
    logger.info("=" * 50)

    obj_cfg = config.get_object_config(scopes, object_type)
    fields, sf_filter = obj_cfg["fields"], obj_cfg["filter"]
    logger.info("[%s] %d fields configured in scopes.yaml.", object_type, len(fields))

    if since_iso:
        records = fetch_records_since(sf, object_type, fields, since_iso, sf_filter)
    else:
        records = fetch_all_records(sf, object_type, fields, sf_filter)

    conn = get_connection(database_url)
    try:
        ensure_table(conn, object_type)
        upsert_records(conn, object_type, records)
    finally:
        conn.close()

    logger.info("[%s] done.", object_type)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    since_iso = None
    args = sys.argv[1:]
    if "--since" in args:
        idx = args.index("--since")
        since_str = args[idx + 1]
        args = args[:idx] + args[idx + 2:]
        since_iso = datetime.strptime(since_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat()
        logger.info("Filtering to records modified on or after %s", since_iso)

    requested_objects = args
    scopes = config.load_scopes()

    sf = connect(config.SF_USERNAME, config.SF_PASSWORD, config.SF_TOKEN, config.SF_DOMAIN)

    failed = []
    for object_type in requested_objects:
        try:
            extract_object(sf, config.DATABASE_URL, object_type, scopes, since_iso)
        except Exception as e:
            logger.error("[%s] FAILED: %s", object_type, e, exc_info=True)
            failed.append(object_type)

    if failed:
        logger.error("Failed objects: %s", failed)
        sys.exit(1)
    else:
        logger.info("All objects extracted successfully.")


if __name__ == "__main__":
    main()
