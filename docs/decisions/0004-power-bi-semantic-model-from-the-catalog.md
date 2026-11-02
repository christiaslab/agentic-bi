# ADR-0004: A Power BI semantic model generated from the metrics catalog

Status: Accepted, 2026-11-02 (owner ruling). Proposed 2026-11-01.

## Context

The owner builds this project as a public portfolio piece that carries their Power BI work from their last role into an agentic BI system. The plan of record keeps Power BI in phase two ("a Power BI path"), so at the close of S6 the repository would show no Power BI artefact.

ADR-0002 already puts every measure definition in one metrics catalog. Power BI Desktop projects (PBIP) store a semantic model as plain TMDL text files, and Microsoft documents programmatic generation of those files ("Programmatic generation and editing item definitions", Power BI Desktop projects overview, page dated 2026-09-23). A generator can therefore write the Power BI model from the same catalog the compiler reads.

## Decision

1. **A new plan row, S3c, inside the usable phase.** It needs the S2 models and the S3a catalog, not S3b or S4, so it can share a night with both.
2. **One definition, two consumers.** Each catalog metric has a DAX expression in `catalog/dax.yaml`, keyed by metric name, beside its SQL rule in `catalog/metrics.yaml` (the catalog loader refuses unknown keys, and a test keeps the two files' metric lists equal). A generator writes the PBIP semantic model from `docs/contracts/star-schema.md` and `catalog/metrics.yaml`: one table per contract table, the contract's many-to-one relationships, `dim_date` marked as the date table, and one measure per metric with the catalog's description. Generated files are never edited by hand; a CI test regenerates them and fails on any difference.
3. **Data.** The contract tables are exported to Parquet from the S2 models (`data/model/`). Power Query reads them through a single `DataFolder` parameter, in import mode. No gateway, no service, no Pro licence.
4. **Proof.** Power BI Desktop runs on Windows only and CI cannot evaluate DAX. The owner opens the project in Desktop and records five golden values from `docs/golden/golden.json`, at least one `derived`, in `docs/powerbi.md`. Each must equal `docs/golden/` to the cent. This check is manual and the README says so.
5. **Division of work.** The generator owns the semantic model's `definition/` folder and a minimal report pointer built only from Microsoft's published JSON schemas. Report pages and visuals are the owner's work in Desktop, saved in the same PBIP. Files whose schema Microsoft does not document (`report.json`, `diagramLayout.json`) are never written by code.
6. **File hygiene.** UTF-8 without BOM, CRLF line endings kept by `.gitattributes`, short paths (Windows limit of 260 characters), and `.pbi/cache.abf` and `.pbi/localSettings.json` ignored.

## Plan change

Add to the sessions table of `docs/PLAN.md`, after S3b:

| S | Deliverable | Done means |
| --- | --- | --- |
| S3c | Power BI semantic model (PBIP, TMDL) generated from the catalog, over the contract tables exported to Parquet | The generator's output is in sync with the catalog in CI; the owner reproduces five golden values in Power BI Desktop, at least one `derived` |

In "Phase two", replace "a Power BI path" with "publishing to the Power BI service".

## Consequences

- One more session, and a Windows step (Parallels) for the owner's Desktop check. Under ADR-0003 point 4 that check is hands-on work: it waits until the project is unparked (not before 2026-11-29) unless the owner rules otherwise.
- DAX and SQL can still disagree in meaning, above all for filter context in the top-10 share and the competition rate. The golden check is the guard, which is why it includes a `derived` value.
- A generated report folder may be refused by Desktop. Fallback: the owner saves a blank report once in Desktop, and the generator stops writing the report folder.
- The Figma design layer stays parked.
