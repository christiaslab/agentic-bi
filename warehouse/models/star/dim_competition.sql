/* dim_competition: one row per extent-competed code found in the snapshot. The code and
   its label come from the source; is_competed comes from the seed. A transaction with
   no code keeps a null competition_code in the fact: there is no row for it here. */
MODEL (
  name main.dim_competition,
  kind FULL,
  grain competition_code,
  columns (
    competition_code VARCHAR,
    competition_description VARCHAR,
    is_competed BOOLEAN
  ),
  audits (
    not_null(columns := (competition_code, competition_description, is_competed)),
    unique_values(columns := (competition_code)),
    competition_seed_matches_source
  )
);

WITH source AS (
  SELECT
    extent_competed_code AS competition_code,
    extent_competed AS competition_description
  FROM staging.contract_transaction
  WHERE
    extent_competed_code IS NOT NULL
  QUALIFY
    ROW_NUMBER() OVER (
      PARTITION BY extent_competed_code
      ORDER BY action_date DESC, contract_transaction_unique_key DESC
    ) = 1
)
SELECT
  source.competition_code::VARCHAR AS competition_code,
  source.competition_description::VARCHAR AS competition_description,
  seed.is_competed::BOOLEAN AS is_competed
FROM source
LEFT JOIN seeds.extent_competed AS seed
  ON seed.competition_code = source.competition_code
