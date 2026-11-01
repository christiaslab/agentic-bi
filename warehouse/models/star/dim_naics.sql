/* dim_naics: one row per NAICS code found in the snapshot. The label is
   the one recorded on the latest transaction with that code, ties broken by the
   greatest transaction key (the dim_reporting_recipient rule of the contract). */
MODEL (
  name main.dim_naics,
  kind FULL,
  grain naics_code,
  columns (
    naics_code VARCHAR,
    naics_description VARCHAR
  ),
  audits (
    not_null(columns := (naics_code, naics_description)),
    unique_values(columns := (naics_code))
  )
);

SELECT
  naics_code::VARCHAR AS naics_code,
  naics_description::VARCHAR AS naics_description
FROM staging.contract_transaction
WHERE
  naics_code IS NOT NULL
QUALIFY
  ROW_NUMBER() OVER (
    PARTITION BY naics_code
    ORDER BY action_date DESC, contract_transaction_unique_key DESC
  ) = 1
