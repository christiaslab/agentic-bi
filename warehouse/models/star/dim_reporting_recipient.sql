/* dim_reporting_recipient: one row per reporting UEI (the parent UEI recorded on a
   transaction, else the recipient's own UEI). Contract: the name is the parent name
   recorded on the latest transaction with that reporting UEI (the recipient name when
   the fallback applies), ties broken by transaction key; the greatest key wins. */
MODEL (
  name main.dim_reporting_recipient,
  kind FULL,
  grain reporting_uei,
  columns (
    reporting_uei VARCHAR,
    reporting_name VARCHAR
  ),
  audits (
    not_null(columns := (reporting_uei, reporting_name)),
    unique_values(columns := (reporting_uei))
  )
);

SELECT
  reporting_uei::VARCHAR AS reporting_uei,
  reporting_name::VARCHAR AS reporting_name
FROM staging.contract_transaction
QUALIFY
  ROW_NUMBER() OVER (
    PARTITION BY reporting_uei
    ORDER BY action_date DESC, contract_transaction_unique_key DESC
  ) = 1
