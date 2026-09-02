
#!/usr/bin/env python3
# /// script
# dependencies = ["simple-salesforce", "python-dotenv"]
# ///
"""
Salesforce SOQL export tool.

Runs one or more .soql query files against Salesforce and saves results as CSV
files with the same name in the output/ directory.

Credentials are read from ./.env (this module's own — typically the SOURCE org,
distinct from the target org credentials used by ../load/.env).

Usage:
    uv run export.py                                          # run all queries/
    uv run export.py queries/opps.soql
    uv run export.py queries/query_a.soql queries/cases.soql
"""

import csv
import logging
import re
import sys
from pathlib import Path

from dotenv import dotenv_values
from simple_salesforce import Salesforce
from simple_salesforce.exceptions import SalesforceGeneralError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
ENV_FILE = SCRIPT_DIR / ".env"
QUERIES_DIR = SCRIPT_DIR / "queries"
OUTPUT_DIR = SCRIPT_DIR / "output"


def load_credentials() -> dict:
    logger.info("Loading credentials from: %s", ENV_FILE.resolve())
    env = dotenv_values(ENV_FILE)
    # Session-based auth (SSO / OAuth) takes priority over username+password
    has_session = env.get("SF_SESSION_ID") and env.get("SF_INSTANCE_URL")
    has_password = env.get("SF_USERNAME") and env.get("SF_PASSWORD") and env.get("SF_SECURITY_TOKEN")
    if not has_session and not has_password:
        raise ValueError(
            "Provide either SF_SESSION_ID + SF_INSTANCE_URL (SSO/OAuth) "
            "or SF_USERNAME + SF_PASSWORD + SF_SECURITY_TOKEN in .env"
        )
    return env


def connect(env: dict) -> Salesforce:
    # Session-based auth (SSO / OAuth token from Salesforce Inspector)
    if env.get("SF_SESSION_ID") and env.get("SF_INSTANCE_URL"):
        logger.info("Connecting to Salesforce via session token (instance=%s)", env["SF_INSTANCE_URL"])
        return Salesforce(
            session_id=env["SF_SESSION_ID"],
            instance_url=env["SF_INSTANCE_URL"],
        )
    # Username + password + security token
    domain = env.get("SF_DOMAIN") or None
    logger.info("Connecting to Salesforce (domain=%s, user=%s)", domain or "production", env["SF_USERNAME"])
    return Salesforce(
        username=env["SF_USERNAME"],
        password=env["SF_PASSWORD"],
        security_token=env["SF_SECURITY_TOKEN"],
        **({"domain": domain} if domain else {}),
    )


def _extract_object_name(soql: str) -> str:
    match = re.search(r'\bFROM\s+(\w+)', soql, re.IGNORECASE)
    if not match:
        raise ValueError("Cannot determine Salesforce object from SOQL — no FROM clause found.")
    return match.group(1)


def run_query(sf: Salesforce, soql: str) -> list[dict]:
    """Execute SOQL and return flat records. Falls back to Bulk API 2.0 for large queries."""
    logger.info("Running query...")
    try:
        result = sf.query_all(soql)
        records = result.get("records", [])
        return [{k: v for k, v in row.items() if k != "attributes"} for row in records]
    except SalesforceGeneralError as e:
        if "414" not in str(e) and "431" not in str(e):
            raise
        object_name = _extract_object_name(soql)
        logger.info("Query too large for REST API (4xx) — switching to Bulk API 1.0 (object: %s)", object_name)
        records = getattr(sf.bulk, object_name).query(soql)
        return [{k: v for k, v in row.items() if k != "attributes"} for row in records]


def save_csv(records: list[dict], out_path: Path) -> None:
    if not records:
        logger.warning("Query returned 0 records — writing empty CSV.")
        out_path.touch()
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(records[0].keys())

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    logger.info("Saved %d rows → %s", len(records), out_path)


def export_file(sf: Salesforce, soql_path: Path) -> None:
    logger.info("=" * 60)
    logger.info("Query file: %s", soql_path.name)
    logger.info("=" * 60)

    soql = soql_path.read_text(encoding="utf-8").strip()
    if not soql:
        logger.warning("Empty query file, skipping.")
        return

    records = run_query(sf, soql)
    out_path = OUTPUT_DIR / f"{soql_path.stem}.csv"
    save_csv(records, out_path)


def main():
    if len(sys.argv) > 1:
        soql_files = [Path(p) for p in sys.argv[1:]]
    else:
        soql_files = sorted(QUERIES_DIR.glob("*.soql"))
        if not soql_files:
            logger.error("No .soql files found in %s", QUERIES_DIR)
            sys.exit(1)
        logger.info("No arguments given — running all %d query files in queries/", len(soql_files))

    env = load_credentials()
    sf = connect(env)

    failed = []
    for path in soql_files:
        try:
            export_file(sf, path)
        except Exception as e:
            logger.error("[%s] FAILED: %s", path.name, e, exc_info=True)
            failed.append(path.name)

    if failed:
        logger.error("Failed: %s", failed)
        sys.exit(1)
    else:
        logger.info("All exports completed successfully.")


if __name__ == "__main__":
    main()
