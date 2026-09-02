import logging

from simple_salesforce import Salesforce
from simple_salesforce.exceptions import SalesforceGeneralError

logger = logging.getLogger(__name__)


def connect(username: str, password: str, token: str, domain: str) -> Salesforce:
    logger.info("Connecting to source Salesforce org (domain=%s, user=%s)", domain, username)
    return Salesforce(username=username, password=password, security_token=token, domain=domain)


def fetch_all_records(sf: Salesforce, object_type: str, fields: list, where: str | None = None) -> list:
    """
    Fetch every record for a Salesforce object via SOQL.
    Falls back to Bulk API 1.0 if the REST query is too large (HTTP 414/431).
    Returns a list of flat dicts (the 'attributes' key stripped).
    """
    soql = f"SELECT {', '.join(fields)} FROM {object_type}"
    if where:
        soql += f" WHERE {where}"

    logger.info("[%s] running query: %s", object_type, soql)
    try:
        result = sf.query_all(soql)
        records = result.get("records", [])
    except SalesforceGeneralError as e:
        if "414" not in str(e) and "431" not in str(e):
            raise
        logger.info("[%s] query too large for REST API — switching to Bulk API 1.0", object_type)
        records = getattr(sf.bulk, object_type).query(soql)

    clean = [{k: v for k, v in r.items() if k != "attributes"} for r in records]
    logger.info("[%s] fetched %d records.", object_type, len(clean))
    return clean


def fetch_records_since(sf: Salesforce, object_type: str, fields: list,
                        since_iso: str, extra_filter: str | None = None) -> list:
    """Fetch records with LastModifiedDate >= since_iso (e.g. '2026-01-01T00:00:00Z')."""
    where = f"LastModifiedDate >= {since_iso}"
    if extra_filter:
        where = f"({where}) AND ({extra_filter})"
    return fetch_all_records(sf, object_type, fields, where)
