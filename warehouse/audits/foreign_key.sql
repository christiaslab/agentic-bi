/* Fact rows whose foreign key is not null and has no row in its dimension.
   Null keys are left to the null counts of the report: the contract allows them. */
AUDIT (
  name foreign_key
);

SELECT
  *
FROM @this_model
WHERE
  @column IS NOT NULL
  AND @column NOT IN (
    SELECT
      @key
    FROM @dimension
    WHERE
      @key IS NOT NULL
  )
