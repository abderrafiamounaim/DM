-- Example staging query: source Account -> target-ready shape.
-- Reads from the src_account table populated by extract/extract.py.
-- Adjust field names and the JSONB extraction (properties->>'Field') per project.

SELECT
    source_id,
    properties->>'Name'                AS name,
    properties->>'BillingCity'         AS billing_city,
    properties->>'BillingCountry'      AS billing_country,
    properties->>'ParentId'            AS parent_id,
    properties->>'OwnerId'             AS owner_id,
    source_created_at,
    source_updated_at
FROM src_account
