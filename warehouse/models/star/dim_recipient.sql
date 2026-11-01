/* dim_recipient: one row per recipient UEI. The name is the one recorded on the
   recipient's latest transaction, ties broken by the greatest transaction key, the
   rule the contract fixes for dim_reporting_recipient. */
MODEL (
  name main.dim_recipient,
  kind FULL,
  grain recipient_uei,
  columns (
    recipient_uei VARCHAR,
    recipient_name VARCHAR
  ),
  audits (
    not_null(columns := (recipient_uei, recipient_name)),
    unique_values(columns := (recipient_uei))
  )
);

SELECT
  recipient_uei::VARCHAR AS recipient_uei,
  recipient_name::VARCHAR AS recipient_name
FROM staging.contract_transaction
QUALIFY
  ROW_NUMBER() OVER (
    PARTITION BY recipient_uei
    ORDER BY action_date DESC, contract_transaction_unique_key DESC
  ) = 1
