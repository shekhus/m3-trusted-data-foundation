-- Schema drift alerts (docs/plan.md A9). A batch whose fingerprint differs from the source's previous one raises
-- an alert naming what changed and, via lineage, what it breaks. Publish refuses any batch whose fingerprint
-- has an open alert; an owner resolves the alert once the new shape has a confirmed mapping.

CREATE TABLE ops.drift_alerts (
    alert_id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source               text        NOT NULL,
    batch_id             uuid        NOT NULL REFERENCES ops.batches (batch_id),
    from_fingerprint_id  bigint      NOT NULL REFERENCES ops.fingerprints (fingerprint_id),
    to_fingerprint       char(64)    NOT NULL,
    diff                 jsonb       NOT NULL,
    message              text        NOT NULL,
    status               text        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    resolution           text,
    resolved_by          text,
    resolved_at          timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'resolved' OR (resolution IS NOT NULL AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL))
);
-- one alert per change of shape, however many files arrive in the new shape
CREATE UNIQUE INDEX drift_alerts_one_per_shape_idx ON ops.drift_alerts (source, to_fingerprint);
CREATE INDEX drift_alerts_status_idx ON ops.drift_alerts (source, status);
