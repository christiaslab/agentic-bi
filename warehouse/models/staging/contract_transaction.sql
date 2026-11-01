/* The committed S1 snapshot, typed and kept whole: one row per contract transaction.
   Source fields keep their USAspending names (docs/sources.md). The two per-transaction
   derivations of the contract are made once here: the reporting parent and the place
   of performance state. Materialised, so the built database never reads the Parquet
   again (ADR-0002 point 6 turns external access off for queries). */
MODEL (
  name staging.contract_transaction,
  kind FULL,
  grain contract_transaction_unique_key,
  columns (
    contract_transaction_unique_key VARCHAR,
    contract_award_unique_key VARCHAR,
    federal_action_obligation DECIMAL(18, 2),
    action_date DATE,
    action_date_fiscal_year INTEGER,
    awarding_agency_code VARCHAR,
    awarding_sub_agency_code VARCHAR,
    awarding_sub_agency_name VARCHAR,
    recipient_uei VARCHAR,
    recipient_name VARCHAR,
    recipient_parent_uei VARCHAR,
    recipient_parent_name VARCHAR,
    primary_place_of_performance_country_code VARCHAR,
    primary_place_of_performance_state_code VARCHAR,
    award_type_code VARCHAR,
    product_or_service_code VARCHAR,
    product_or_service_code_description VARCHAR,
    naics_code VARCHAR,
    naics_description VARCHAR,
    extent_competed_code VARCHAR,
    extent_competed VARCHAR,
    reporting_uei VARCHAR,
    reporting_name VARCHAR,
    pop_state_code VARCHAR
  ),
  audits (
    not_null(columns := (contract_transaction_unique_key)),
    unique_values(columns := (contract_transaction_unique_key)),
    accepted_values(column := awarding_agency_code, is_in := ('068'))
  )
);

SELECT
  contract_transaction_unique_key::VARCHAR AS contract_transaction_unique_key,
  contract_award_unique_key::VARCHAR AS contract_award_unique_key,
  federal_action_obligation::DECIMAL(18, 2) AS federal_action_obligation,
  action_date::DATE AS action_date,
  action_date_fiscal_year::INTEGER AS action_date_fiscal_year,
  awarding_agency_code::VARCHAR AS awarding_agency_code,
  awarding_sub_agency_code::VARCHAR AS awarding_sub_agency_code,
  awarding_sub_agency_name::VARCHAR AS awarding_sub_agency_name,
  recipient_uei::VARCHAR AS recipient_uei,
  recipient_name::VARCHAR AS recipient_name,
  recipient_parent_uei::VARCHAR AS recipient_parent_uei,
  recipient_parent_name::VARCHAR AS recipient_parent_name,
  primary_place_of_performance_country_code::VARCHAR AS primary_place_of_performance_country_code,
  primary_place_of_performance_state_code::VARCHAR AS primary_place_of_performance_state_code,
  award_type_code::VARCHAR AS award_type_code,
  product_or_service_code::VARCHAR AS product_or_service_code,
  product_or_service_code_description::VARCHAR AS product_or_service_code_description,
  naics_code::VARCHAR AS naics_code,
  naics_description::VARCHAR AS naics_description,
  extent_competed_code::VARCHAR AS extent_competed_code,
  extent_competed::VARCHAR AS extent_competed,
  /* contract: the parent UEI recorded on the transaction, else the recipient's own UEI */
  COALESCE(recipient_parent_uei, recipient_uei)::VARCHAR AS reporting_uei,
  /* the name that goes with that UEI: the parent's, or the recipient's on the fallback */
  CASE
    WHEN recipient_parent_uei IS NULL
    THEN recipient_name
    ELSE recipient_parent_name
  END::VARCHAR AS reporting_name,
  /* contract: US state or territory code, null when abroad (docs/sources.md) */
  CASE
    WHEN primary_place_of_performance_country_code = 'USA'
    THEN primary_place_of_performance_state_code
  END::VARCHAR AS pop_state_code
FROM READ_PARQUET(@snapshot_parquet)
