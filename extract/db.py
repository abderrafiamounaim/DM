import json
import logging
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)


def get_connection(database_url: str):
    return psycopg2.connect(database_url)


def table_name(object_type: str) -> str:
    """Convert a Salesforce object API name to a safe PostgreSQL table name."""
    return "src_" + object_type.lower().replace("__c", "").replace("-", "_").replace(".", "_")


def ensure_table(conn, object_type: str):
    """
    Create the object staging table if it does not exist.

    Columns:
      source_id           — source org record ID (primary key for upsert)
      properties           — full raw record payload (queryable as JSONB)
      source_created_at    — CreatedDate from the source record
      source_updated_at    — LastModifiedDate from the source record
      extracted_at          — timestamp of this extraction run
    """
    tname = table_name(object_type)
    with conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {tname} (
                source_id         TEXT                     PRIMARY KEY,
                properties        JSONB                    NOT NULL,
                source_created_at TIMESTAMP WITH TIME ZONE,
                source_updated_at TIMESTAMP WITH TIME ZONE,
                extracted_at      TIMESTAMP WITH TIME ZONE NOT NULL
            );
        """)
    conn.commit()
    logger.info("Table '%s' ready.", tname)


def upsert_records(conn, object_type: str, records: list):
    """
    Upsert source records into the object staging table.
    Safe to rerun — never creates duplicates.
    """
    if not records:
        logger.info("[%s] no records to upsert.", object_type)
        return

    tname = table_name(object_type)
    extracted_at = datetime.now(timezone.utc)

    rows = [
        (
            str(r["Id"]),
            json.dumps(r).replace('\\u0000', ''),
            r.get("CreatedDate"),
            r.get("LastModifiedDate"),
            extracted_at,
        )
        for r in records
    ]

    sql = f"""
        INSERT INTO {tname} (source_id, properties, source_created_at, source_updated_at, extracted_at)
        VALUES %s
        ON CONFLICT (source_id) DO UPDATE SET
            properties         = EXCLUDED.properties,
            source_created_at  = EXCLUDED.source_created_at,
            source_updated_at  = EXCLUDED.source_updated_at,
            extracted_at       = EXCLUDED.extracted_at;
    """

    CHUNK = 1000
    total_upserted = 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        with conn.cursor() as cur:
            execute_values(cur, sql, chunk)
        conn.commit()
        total_upserted += len(chunk)

    logger.info("[%s] upserted %d/%d records into '%s'.", object_type, total_upserted, len(rows), tname)
