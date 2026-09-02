#!/usr/bin/env python3
# /// script
# dependencies = ["simple-salesforce", "pandas", "python-dotenv"]
# ///
"""
Load any Salesforce object from a CSV file.

Usage:
    uv run load_records.py job_contacts.yaml
    uv run load_records.py job_contacts.yaml --dry-run
    uv run load_records.py job_contacts.yaml --limit 3
    uv run load_records.py job_contacts.yaml --resume

Job YAML fields:
    object      : Salesforce object API name  (e.g. Contact)
    operation   : insert | upsert | update
    external_id : External ID field for upsert/update
    csv         : Path to the source CSV (relative to this script)
    batch_size  : Records per API call  (1 = safest)
    threads     : Parallel workers      (1 = sequential)
    limit       : Max rows to process   (omit = all)
    exclude     : Path to a CSV whose external_id values to skip (optional)

Resume / exclusion:
    --resume    Auto-skips IDs already in the latest success log for this object.
    exclude:    In the YAML, point to any success CSV to exclude manually.

Logs written to: log/runs/<YYYY-MM-DD_HHMMSS>_<job>/success.csv
                 log/runs/<YYYY-MM-DD_HHMMSS>_<job>/fails.csv
                 log/runs/<YYYY-MM-DD_HHMMSS>_<job>/meta.json
"""

import argparse
import json
import os
import pathlib
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv
from simple_salesforce import Salesforce, exceptions as sf_exc

# ── Paths ──────────────────────────────────────────────────────────────────────
HERE    = pathlib.Path(__file__).parent
LOG_DIR = HERE / "log"

# ── Fields always excluded from the API payload ────────────────────────────────
SKIP_FIELDS   = {"_", "LastActivityDate"}
DATE_FIELDS   = {"CreatedDate", "LastModifiedDate"}   # ISO-8601; needs "Set Audit Fields" perm
AUDIT_FIELDS  = {"CreatedDate", "LastModifiedDate", "CreatedById", "LastModifiedById"}
BOOL_VALUES   = {"true", "false"}

# ── ANSI colours ───────────────────────────────────────────────────────────────
GRN = "\033[92m"
RED = "\033[91m"
YLW = "\033[93m"
BLU = "\033[94m"
DIM = "\033[2m"
RST = "\033[0m"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_yaml(text: str) -> dict:
    """Minimal YAML parser for flat key: value job files (no pyyaml needed)."""
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        # Strip inline comments
        if " #" in val:
            val = val[:val.index(" #")].strip()
        # Unquote
        if len(val) >= 2 and val[0] in ('"', "'") and val[-1] == val[0]:
            val = val[1:-1]
        # Type coercions
        if val == "" or val.lower() in ("null", "~", "none"):
            result[key] = None
        elif val.lower() == "true":
            result[key] = True
        elif val.lower() == "false":
            result[key] = False
        else:
            try:
                result[key] = int(val)
            except ValueError:
                try:
                    result[key] = float(val)
                except ValueError:
                    result[key] = val
    return result


def load_job(path: pathlib.Path) -> dict:
    with open(path, encoding="utf-8") as f:
        job = _parse_yaml(f.read())
    required = {"object", "operation", "csv"}
    missing  = required - job.keys()
    if missing:
        sys.exit(f"job YAML missing required keys: {missing}")
    if job["operation"] not in ("insert", "upsert", "update"):
        sys.exit(f"operation must be insert | upsert | update, got: {job['operation']}")
    if job["operation"] in ("upsert", "update") and not job.get("external_id"):
        sys.exit("external_id is required for upsert / update operations")
    return job


def connect() -> Salesforce:
    load_dotenv(HERE / ".env")
    session_id   = os.environ.get("SF_SESSION_ID")
    instance_url = os.environ.get("SF_INSTANCE_URL")
    if session_id and instance_url:
        print(f"{BLU}Connecting to Salesforce (session)...{RST}")
        sf = Salesforce(session_id=session_id, instance_url=instance_url)
    else:
        username = os.environ["SF_USERNAME"]
        password = os.environ["SF_PASSWORD"]
        token    = os.environ["SF_SECURITY_TOKEN"]
        domain   = os.environ.get("SF_DOMAIN", "test")
        print(f"{BLU}Connecting to Salesforce ({domain})...{RST}")
        sf = Salesforce(username=username, password=password,
                        security_token=token, domain=domain)
    print(f"{GRN}Connected.  Instance: {sf.sf_instance}{RST}\n")
    return sf


def _to_sf_datetime(val: str) -> str | None:
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(val.strip(), fmt).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except ValueError:
            continue
    return None


def build_payload(row: pd.Series, external_id_field: str, operation: str = "insert") -> dict:
    payload = {}
    for col, val in row.items():
        if col in SKIP_FIELDS or col.startswith("_") or (col == external_id_field and operation != "insert"):
            continue
        if pd.isna(val) or str(val).strip() == "":
            continue
        if col in DATE_FIELDS:
            converted = _to_sf_datetime(str(val))
            if converted:
                payload[col] = converted
        elif isinstance(val, str) and val.strip().lower() in BOOL_VALUES:
            payload[col] = val.strip().lower() == "true"
        elif '.' in col:
            # Relationship lookup: "Parent.Legacy_CRM_ID__c" → {"Parent": {"Legacy_CRM_ID__c": val}}
            rel, field = col.split('.', 1)
            payload.setdefault(rel, {})[field] = val
        elif isinstance(val, str) and val.strip().lstrip("-").replace(".", "", 1).isdigit():
            s = val.strip()
            payload[col] = int(s) if "." not in s else float(s)
        else:
            payload[col] = val
    return payload


def call_sf(sf: Salesforce, sf_obj, operation: str,
            external_id_field: str, external_id_value: str, payload: dict,
            sf_object_name: str = "") -> dict:
    """
    Execute one API call (insert / upsert / update) and return a normalised result.
    simple-salesforce upsert returns:
        dict {"id": "003...", "success": True}  -> HTTP 201 (Inserted)
        None                                     -> HTTP 204 (Updated)
    Errors raise SalesforceMalformedRequest (caught by caller).
    """
    if operation == "insert":
        result = sf_obj.create(payload)
        # Returns {"id": "003...", "success": True, "errors": []}
        return {"status": "Succeeded",
                "id":     result.get("id", ""),
                "action": "Inserted",
                "errors": ""}

    if operation in ("upsert", "update"):
        result = sf_obj.upsert(f"{external_id_field}/{external_id_value}", payload)
        # Returns None (204 Updated) or dict with "id" (201 Inserted)
        if isinstance(result, dict):
            return {"status": "Succeeded",
                    "id":     result.get("id", ""),
                    "action": "Inserted",
                    "errors": ""}
        # HTTP 204 Updated — SF doesn't return the ID, fetch it via SOQL
        safe_val = external_id_value.replace("'", "\\'")
        soql     = (f"SELECT Id FROM {sf_object_name} "
                    f"WHERE {external_id_field} = '{safe_val}' LIMIT 1")
        qr       = sf.query(soql)
        sf_id    = qr["records"][0]["Id"] if qr["totalSize"] > 0 else ""
        return {"status": "Succeeded",
                "id":     sf_id,
                "action": "Updated",
                "errors": ""}

    raise ValueError(f"Unknown operation: {operation}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("job",      help="Path to job YAML file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate without calling Salesforce")
    parser.add_argument("--limit",  type=int, default=None,
                        help="Override the limit in the job YAML")
    parser.add_argument("--resume", action="store_true",
                        help="Skip IDs already present in the latest success log")
    args = parser.parse_args()

    # ── Load job config ───────────────────────────────────────────────────────
    job_path = pathlib.Path(args.job)
    if not job_path.is_absolute():
        job_path = HERE / job_path
    job = load_job(job_path)

    sf_object_name  = job["object"]
    operation       = job["operation"]
    external_id     = job.get("external_id", "")
    batch_size      = int(job.get("batch_size", 1))
    threads         = int(job.get("threads",    1))
    limit           = args.limit or job.get("limit")  # CLI overrides YAML

    csv_path = pathlib.Path(job["csv"])
    if not csv_path.is_absolute():
        csv_path = HERE / csv_path

    # ── Load CSV ──────────────────────────────────────────────────────────────
    df = pd.read_csv(csv_path, dtype=str)
    if limit:
        df = df.head(int(limit))

    # ── Exclusion: skip already-processed external IDs ────────────────────────
    excluded_ids: set[str] = set()

    # Option 1 – YAML field: exclude: path/to/some_success.csv
    exclude_yaml = job.get("exclude")
    if exclude_yaml and external_id:
        excl_path = pathlib.Path(exclude_yaml)
        if not excl_path.is_absolute():
            excl_path = HERE / excl_path
        if excl_path.exists():
            excl_df = pd.read_csv(excl_path, dtype=str)
            if external_id in excl_df.columns:
                excluded_ids |= set(excl_df[external_id].dropna().str.strip())
                print(f"{YLW}  Excluding {len(excluded_ids)} IDs from {excl_path.name}{RST}")

    # Option 2 – --resume: auto-find latest success log for this object
    if args.resume and external_id:
        runs_dir  = LOG_DIR / "runs"
        pattern   = f"*_{job_path.stem}"
        # Folders are YYYY-MM-DD_HHMMSS_<object> — lexicographic sort = chronological
        candidates = sorted(
            p for p in runs_dir.glob(pattern)
            if p.is_dir() and (p / "success.csv").exists()
        )
        if candidates:
            latest_run   = candidates[-1]
            latest       = latest_run / "success.csv"
            resume_df    = pd.read_csv(latest, dtype=str)
            if external_id in resume_df.columns:
                resume_ids = set(resume_df[external_id].dropna().str.strip())
                new_ids    = resume_ids - excluded_ids
                excluded_ids |= new_ids
                print(f"{YLW}  --resume: skipping {len(new_ids)} IDs from {latest_run.name}{RST}")
        else:
            print(f"{YLW}  --resume: no previous success log found — running all rows{RST}")

    if excluded_ids and external_id and external_id in df.columns:
        before = len(df)
        df = df[~df[external_id].str.strip().isin(excluded_ids)].reset_index(drop=True)
        print(f"{YLW}  Skipped {before - len(df)} rows, {len(df)} remaining{RST}")

    total = len(df)

    print(f"\n{BLU}{'-'*60}{RST}")
    print(f"  Job      : {job_path.name}")
    print(f"  File     : {csv_path.name}")
    print(f"  Object   : {sf_object_name}")
    print(f"  Operation: {operation.upper()}"
          + (f"  (ext-id: {external_id})" if external_id else ""))
    print(f"  Rows     : {total}{' (dry-run)' if args.dry_run else ''}"
          f"  batch={batch_size}  threads={threads}")
    print(f"{BLU}{'-'*60}{RST}\n")

    # ── Create run directory ──────────────────────────────────────────────────
    started_at = datetime.now()
    run_id  = f"{started_at.strftime('%Y-%m-%d_%H%M%S')}_{job_path.stem}"
    run_dir = LOG_DIR / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # ── Connect ───────────────────────────────────────────────────────────────
    if not args.dry_run:
        sf     = connect()
        sf_obj = getattr(sf, sf_object_name)
    else:
        sf = sf_obj = None

    # ── Process rows ──────────────────────────────────────────────────────────
    result_cols  = list(df.columns) + ["__Status", "__Id", "__Action", "__Errors"]
    success_rows: list[dict] = []
    fail_rows:    list[dict] = []
    pad  = len(str(total))
    lock = threading.Lock()

    def process_row(i: int, row: pd.Series) -> None:
        ext_id = str(row.get(external_id, "")).strip() if external_id else ""
        fn = row.get("FirstName", ""); fn = "" if pd.isna(fn) else str(fn).strip()
        ln = row.get("LastName",  ""); ln = "" if pd.isna(ln) else str(ln).strip()
        name   = f"{fn} {ln}".strip() or str(row.get("Email", ""))
        prefix = f"[{i:>{pad}}/{total}]"

        if operation in ("upsert", "update") and not ext_id:
            result = {"status": "Failed", "id": "", "action": "",
                      "errors": "MISSING_EXTERNAL_ID"}

        elif args.dry_run:
            payload = build_payload(row, external_id, operation)
            result  = {"status": "DryRun", "id": "", "action": f"Would {operation}",
                       "errors": ""}
            print(f"{DIM}{prefix} {ext_id:<18} {name:<35} "
                  f">> DRY-RUN ({len(payload)} fields){RST}")

        else:
            payload = build_payload(row, external_id, operation)
            try:
                result = call_sf(sf, sf_obj, operation, external_id, ext_id, payload, sf_object_name)
            except sf_exc.SalesforceMalformedRequest as e:
                # If SF rejects any audit field (record already exists → update path),
                # retry once without all audit fields so the rest still land.
                audit_rejected = any(
                    any(f in err.get("message", "") for f in AUDIT_FIELDS)
                    for err in (e.content or [])
                )
                audit_in_payload = AUDIT_FIELDS & payload.keys()
                if audit_rejected and audit_in_payload:
                    retry_payload = {k: v for k, v in payload.items() if k not in AUDIT_FIELDS}
                    try:
                        result = call_sf(sf, sf_obj, operation, external_id, ext_id, retry_payload, sf_object_name)
                        skipped = ", ".join(sorted(audit_in_payload))
                        result["errors"] = f"Audit fields skipped (record already exists): {skipped}"
                    except sf_exc.SalesforceMalformedRequest as e2:
                        msg = "; ".join(
                            f"{err.get('errorCode','ERR')}: {err.get('message','')}"
                            for err in (e2.content or [])
                        ) or str(e2)
                        result = {"status": "Failed", "id": "", "action": "", "errors": msg}
                    except Exception as e2:
                        result = {"status": "Failed", "id": "", "action": "", "errors": str(e2)}
                else:
                    msg = "; ".join(
                        f"{err.get('errorCode','ERR')}: {err.get('message','')}"
                        for err in (e.content or [])
                    ) or str(e)
                    result = {"status": "Failed", "id": "", "action": "", "errors": msg}
            except Exception as e:
                result = {"status": "Failed", "id": "", "action": "", "errors": str(e)}

        # ── Real-time output ──────────────────────────────────────────────────
        if result["status"] == "Succeeded":
            tag = f"{result['action']:<8}"
            sf_id_str = f"  {result['id']}" if result["id"] else ""
            print(f"{GRN}{prefix} {ext_id:<18} {name:<35} >> {tag}{sf_id_str}{RST}")
        elif result["status"] != "DryRun":
            print(f"{RED}{prefix} {ext_id:<18} {name:<35} "
                  f">> FAILED  {result['errors'][:80]}{RST}")

        # ── Collect results ───────────────────────────────────────────────────
        out_row = row.to_dict()
        out_row["__Status"] = result["status"]
        out_row["__Id"]     = result["id"]
        out_row["__Action"] = result["action"]
        out_row["__Errors"] = result["errors"]

        with lock:
            if result["status"] == "Succeeded":
                success_rows.append(out_row)
            elif result["status"] != "DryRun":
                fail_rows.append(out_row)

    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = {
            pool.submit(process_row, i, row): i
            for i, (_, row) in enumerate(df.iterrows(), start=1)
        }
        for fut in as_completed(futures):
            fut.result()   # re-raise any unexpected exception

    # ── Write log files ───────────────────────────────────────────────────────
    finished_at  = datetime.now()
    n_ok         = len(success_rows)
    n_err        = len(fail_rows)
    n_excluded   = len(excluded_ids)

    success_path = run_dir / "success.csv"
    fail_path    = run_dir / "fails.csv"
    run_dir.mkdir(parents=True, exist_ok=True)  # guard: ensure dir exists before writing

    if success_rows:
        pd.DataFrame(success_rows, columns=result_cols).to_csv(
            success_path, index=False, quoting=1)
    if fail_rows:
        pd.DataFrame(fail_rows, columns=result_cols).to_csv(
            fail_path, index=False, quoting=1)

    meta = {
        "run_id":      run_id,
        "object":      sf_object_name,
        "operation":   operation,
        "external_id": external_id or None,
        "csv":         str(job["csv"]),
        "started_at":  started_at.strftime("%Y-%m-%dT%H:%M:%S"),
        "finished_at": finished_at.strftime("%Y-%m-%dT%H:%M:%S"),
        "total":       total,
        "succeeded":   n_ok,
        "failed":      n_err,
        "excluded":    n_excluded,
        "dry_run":     args.dry_run,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{BLU}{'-'*60}{RST}")
    if args.dry_run:
        print(f"  {YLW}Dry-run   : {total} rows — no changes made{RST}")
    else:
        print(f"  {GRN}Succeeded : {n_ok}{RST}")
        print(f"  {RED}Failed    : {n_err}{RST}")
    print(f"  {DIM}Run dir   : {run_dir}{RST}")
    print(f"{BLU}{'-'*60}{RST}\n")

    sys.exit(1 if n_err > 0 else 0)


if __name__ == "__main__":
    main()
