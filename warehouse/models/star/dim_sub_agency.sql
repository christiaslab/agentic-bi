/* dim_sub_agency: one row per awarding sub-agency found in the snapshot. The label is
   the one recorded on the latest transaction with that code, ties broken by the
   greatest transaction key (the dim_reporting_recipient rule of the contract). */
MODEL (
  name main.dim_sub_agency,
  kind FULL,
  grain sub_agency_code,
  columns (
    sub_agency_code VARCHAR,
    sub_agency_name VARCHAR
  ),
  audits (
    not_null(columns := (sub_agency_code, sub_agency_name)),
    unique_values(columns := (sub_agency_code))
  )
);

SELECT
  awarding_sub_agency_code::VARCHAR AS sub_agency_code,
  awarding_sub_agency_name::VARCHAR AS sub_agency_name
FROM staging.contract_transaction
WHERE
  awarding_sub_agency_code IS NOT NULL
QUALIFY
  ROW_NUMBER() OVER (
    PARTITION BY awarding_sub_agency_code
    ORDER BY action_date DESC, contract_transaction_unique_key DESC
  ) = 1
