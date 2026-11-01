/* The extent-competed codes found in the snapshot, with the publisher's label and
   whether the code marks a competed action. Source of the mapping: docs/warehouse.md. */
MODEL (
  name seeds.extent_competed,
  kind SEED (
    path '../../seeds/extent_competed.csv'
  ),
  grain competition_code,
  columns (
    competition_code VARCHAR,
    competition_description VARCHAR,
    is_competed BOOLEAN
  ),
  audits (
    not_null(columns := (competition_code, competition_description, is_competed)),
    unique_values(columns := (competition_code))
  )
)
