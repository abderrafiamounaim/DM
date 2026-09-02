# Salesforce → Salesforce Migration Template

Reusable pipeline scaffolding for a Salesforce-to-Salesforce data migration,
extracted from a previous HubSpot → Salesforce migration project. The
tooling and folder structure are generic; all business logic (field
mappings, SOQL/SQL queries, load jobs) is left as minimal examples to be
replaced per project.

## Pipeline

```
extract/   Source Salesforce org  --SOQL-->  PostgreSQL staging (JSONB, one table/object)
transform/ PostgreSQL staging     --SQL + pandas notebooks-->  target-ready CSVs
load/      Target-ready CSVs     --REST API-->  Target Salesforce org
export/    Ad hoc SOQL -> CSV pulls (reporting/QA), independent of the pipeline above
```

Each module owns its own `.env` (see `.env.example` in each folder) because
the source and target orgs typically have different credentials.

## Setup

Requires [uv](https://docs.astral.sh/uv/) (each script declares its own
dependencies inline — no separate `requirements.txt`/virtualenv setup needed)
and a PostgreSQL instance for staging.

```
# per module: copy the example env file and fill in real credentials
cp extract/.env.example extract/.env
cp export/.env.example export/.env
cp load/.env.example load/.env

cp extract/scopes.example.yaml extract/scopes.yaml   # define objects/fields for this project
```

## Usage

```
# 1. Extract source records into staging
cd extract && uv run extract.py Account Contact

# 2. Transform: run SQL directly, or open a notebook under transform/notebooks/
cd ../transform && uv run export.py sql/example_accounts.sql

# 3. Load into the target org
cd ../load && uv run load_records.py jobs/job_example.yaml --dry-run
uv run load_records.py jobs/job_example.yaml
```

## What's in each module

- **extract/** — `extract.py` + `config.py` + `db.py` + `sf_source.py`: generic
  SOQL-to-Postgres staging, one JSONB table per object, no assumptions about
  target schema. Define fields per object in `scopes.yaml`.
- **export/** — `export.py`: runs `.soql` files in `queries/` against Salesforce
  and saves CSVs. Falls back to Bulk API 1.0 automatically for large queries.
  Useful for ad hoc pulls without going through the staging database.
- **transform/** — `export.py` (SQL → CSV) plus `sql/` and `notebooks/` for
  exploratory, per-object transformation work. `notebooks/example_object/`
  shows the intended notebook structure (Setup → Load → Explore → Build
  target → Export) — copy it per object.
- **load/** — `load_records.py`: generic CSV → Salesforce loader driven by
  declarative job YAMLs (`jobs/*.yaml`). Supports insert/upsert/update,
  multi-threading, `--dry-run`, `--resume`, and per-run logs under `log/runs/`.
- **docs/migration-documentation-guide.md** — methodology reference: the nine
  standard deliverables of a CRM-to-CRM migration workstream (strategy,
  mapping, DQA, runbook, mock reports, reconciliation, cutover, sign-off,
  final report).

## Security notes

- `.gitignore` excludes all `.env` files, run logs, and any `*.csv`/`*.xlsx`
  data files by default — only `.env.example` files are tracked.
- Before pushing to GitHub, double-check `git status` for anything that
  looks like real data or credentials, especially inside `.claude/` or any
  IDE settings files copied from elsewhere.
- Rotate/revoke any credentials that were ever used locally during template
  setup before sharing the repository.
