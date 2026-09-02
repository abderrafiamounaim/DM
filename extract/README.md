# extract/

Pulls records from the **source** Salesforce org into a PostgreSQL staging
database, one table per object (`src_<object>`), storing the full record as
JSONB in a `properties` column plus `source_id` / `source_created_at` /
`source_updated_at` / `extracted_at`.

This mirrors the JSONB-staging pattern used for the HubSpot source in the
previous project (`hubspot-pg-extract/`), adapted for a Salesforce source:
`sf_source.py` replaces the HubSpot REST client with SOQL queries via
`simple-salesforce`, with an automatic Bulk API 1.0 fallback for large objects.

Salesforce relationships are already present as lookup/master-detail ID
fields on each record (e.g. `AccountId`, `ParentId`) — there is no separate
association-fetching step like HubSpot required.

## Setup

```
cp .env.example .env      # fill in source org credentials + DATABASE_URL
cp scopes.example.yaml scopes.yaml   # define fields per object for this project
```

## Usage

```
uv run extract.py Account Contact
uv run extract.py Opportunity --since 2026-01-01
```

Querying the staging tables from `transform/` uses standard Postgres JSONB
operators, e.g. `properties->>'Name'`.
