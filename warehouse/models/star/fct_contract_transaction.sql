/* fct_contract_transaction: one row per contract transaction (ADR-0001 point 3).
   The dimensions are declared as dependencies so that they are built before the
   foreign-key audits of this table read them. */
MODEL (
  name main.fct_contract_transaction,
  kind FULL,
  grain transaction_key,
  depends_on (
    main.dim_date,
    main.dim_recipient,
    main.dim_reporting_recipient,
    main.dim_sub_agency,
    main.dim_psc,
    main.dim_naics,
    main.dim_competition
  ),
  columns (
    transaction_key VARCHAR,
    award_key VARCHAR,
    action_date DATE,
    obligation_amount DECIMAL(18, 2),
    award_type_code VARCHAR,
    recipient_uei VARCHAR,
    reporting_uei VARCHAR,
    sub_agency_code VARCHAR,
    psc_code VARCHAR,
    naics_code VARCHAR,
    competition_code VARCHAR,
    pop_state_code VARCHAR
  ),
  audits (
    not_null(columns := (transaction_key)),
    unique_values(columns := (transaction_key)),
    accepted_values(column := award_type_code, is_in := ('A', 'B', 'C', 'D')),
    foreign_key(column := action_date, dimension := main.dim_date, key := "date"),
    foreign_key(column := recipient_uei, dimension := main.dim_recipient, key := recipient_uei),
    foreign_key(
      column := reporting_uei,
      dimension := main.dim_reporting_recipient,
      key := reporting_uei
    ),
    foreign_key(
      column := sub_agency_code,
      dimension := main.dim_sub_agency,
      key := sub_agency_code
    ),
    foreign_key(column := psc_code, dimension := main.dim_psc, key := psc_code),
    foreign_key(column := naics_code, dimension := main.dim_naics, key := naics_code),
    foreign_key(
      column := competition_code,
      dimension := main.dim_competition,
      key := competition_code
    )
  )
);

SELECT
  contract_transaction_unique_key::VARCHAR AS transaction_key,
  contract_award_unique_key::VARCHAR AS award_key,
  action_date::DATE AS action_date,
  federal_action_obligation::DECIMAL(18, 2) AS obligation_amount,
  award_type_code::VARCHAR AS award_type_code,
  recipient_uei::VARCHAR AS recipient_uei,
  reporting_uei::VARCHAR AS reporting_uei,
  awarding_sub_agency_code::VARCHAR AS sub_agency_code,
  product_or_service_code::VARCHAR AS psc_code,
  naics_code::VARCHAR AS naics_code,
  extent_competed_code::VARCHAR AS competition_code,
  pop_state_code::VARCHAR AS pop_state_code
FROM staging.contract_transaction
