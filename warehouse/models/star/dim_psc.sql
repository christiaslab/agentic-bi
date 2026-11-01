/* dim_psc: one row per product or service code (PSC) found in the snapshot. The label is
   the one recorded on the latest transaction with that code, ties broken by the
   greatest transaction key (the dim_reporting_recipient rule of the contract). */
MODEL (
  name main.dim_psc,
  kind FULL,
  grain psc_code,
  columns (
    psc_code VARCHAR,
    psc_description VARCHAR
  ),
  audits (
    not_null(columns := (psc_code, psc_description)),
    unique_values(columns := (psc_code))
  )
);

SELECT
  product_or_service_code::VARCHAR AS psc_code,
  product_or_service_code_description::VARCHAR AS psc_description
FROM staging.contract_transaction
WHERE
  product_or_service_code IS NOT NULL
QUALIFY
  ROW_NUMBER() OVER (
    PARTITION BY product_or_service_code
    ORDER BY action_date DESC, contract_transaction_unique_key DESC
  ) = 1
