/* dim_date: every day of the fiscal year of the slice, fiscal fields derived from the date.
   Fiscal year Y runs from Y-1-10-01 to Y-09-30; October is fiscal month 1. */
MODEL (
  name main.dim_date,
  kind FULL,
  grain "date",
  columns (
    "date" DATE,
    fiscal_year INTEGER,
    fiscal_quarter INTEGER,
    fiscal_month INTEGER,
    calendar_year INTEGER,
    calendar_month INTEGER
  ),
  audits (
    not_null(columns := ("date")),
    unique_values(columns := ("date")),
    forall(criteria := (fiscal_month BETWEEN 1 AND 12, fiscal_quarter BETWEEN 1 AND 4))
  )
);

WITH days AS (
  SELECT
    day::DATE AS "date"
  FROM GENERATE_SERIES(
    MAKE_DATE(@fiscal_year - 1, 10, 1),
    MAKE_DATE(@fiscal_year, 9, 30),
    INTERVAL '1' DAY
  ) AS series(day)
), fiscal AS (
  SELECT
    "date",
    YEAR("date") + CASE WHEN MONTH("date") >= 10 THEN 1 ELSE 0 END AS fiscal_year,
    (MONTH("date") + 2) % 12 + 1 AS fiscal_month,
    YEAR("date") AS calendar_year,
    MONTH("date") AS calendar_month
  FROM days
)
SELECT
  "date"::DATE AS "date",
  fiscal_year::INTEGER AS fiscal_year,
  ((fiscal_month - 1) // 3 + 1)::INTEGER AS fiscal_quarter,
  fiscal_month::INTEGER AS fiscal_month,
  calendar_year::INTEGER AS calendar_year,
  calendar_month::INTEGER AS calendar_month
FROM fiscal
