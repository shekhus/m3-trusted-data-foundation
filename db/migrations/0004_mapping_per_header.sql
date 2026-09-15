-- A mapping binds one exact source header, not a whole source (docs/decisions.md D-013).
-- PLT-02 renamed cust_no in 2026-03: files before and after need different confirmed mappings, and a header
-- with no confirmed mapping is exactly what drift detection (week 4) reports.

ALTER TABLE ops.mapping_versions
    ADD COLUMN header       jsonb    NOT NULL,
    ADD COLUMN header_hash  char(64) NOT NULL,
    ADD COLUMN rejected_by  text,
    ADD COLUMN rejected_at  timestamptz,
    ADD CONSTRAINT mapping_versions_rejected_who_when
        CHECK (status <> 'rejected' OR (rejected_by IS NOT NULL AND rejected_at IS NOT NULL));

DROP INDEX ops.mapping_versions_one_confirmed_idx;
CREATE UNIQUE INDEX mapping_versions_one_confirmed_per_header_idx
    ON ops.mapping_versions (source, header_hash) WHERE status = 'confirmed';
CREATE INDEX mapping_versions_source_header_idx ON ops.mapping_versions (source, header_hash, version DESC);
