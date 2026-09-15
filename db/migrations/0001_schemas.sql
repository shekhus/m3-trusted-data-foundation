-- Medallion schemas (docs/plan.md §1.1). Tables arrive in later migrations as each stage lands:
-- bronze.raw_* with ingest, silver.* with validation, gold.* with publish.

CREATE SCHEMA bronze;
CREATE SCHEMA silver;
CREATE SCHEMA gold;
CREATE SCHEMA ops;

COMMENT ON SCHEMA bronze IS 'Raw source rows as received, one table per source, nothing parsed or dropped.';
COMMENT ON SCHEMA silver IS 'Mapped, typed, validated canonical rows. Rows failing a blocking rule go to ops.exceptions.';
COMMENT ON SCHEMA gold IS 'Governed tables that consumer views are compiled against (metrics/*.yaml).';
COMMENT ON SCHEMA ops IS 'Control plane: batches, mappings, exceptions, lineage, fingerprints, LLM call log.';
