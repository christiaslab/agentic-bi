/* Codes of dim_competition that the seed does not list, or lists with another label
   than the publisher's. */
AUDIT (
  name competition_seed_matches_source
);

SELECT
  dim.competition_code
FROM @this_model AS dim
LEFT JOIN seeds.extent_competed AS seed
  ON seed.competition_code = dim.competition_code
WHERE
  seed.competition_code IS NULL
  OR seed.competition_description IS DISTINCT FROM dim.competition_description
