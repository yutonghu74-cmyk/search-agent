BEGIN;

CREATE TYPE task_lifecycle AS ENUM ('active', 'archived', 'deleted');
CREATE TYPE revision_status AS ENUM ('proposed', 'confirmed', 'superseded', 'rejected');
CREATE TYPE run_status AS ENUM (
    'CREATED', 'PARSING', 'AWAITING_INPUT', 'PLANNING', 'RUNNING', 'PAUSED',
    'COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED'
);
CREATE TYPE operation_status AS ENUM ('queued', 'running', 'succeeded', 'failed', 'cancelled');
CREATE TYPE verdict AS ENUM ('pass', 'fail', 'unknown');

CREATE TABLE retrieval_task (
    task_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    owner_user_id text NOT NULL,
    conversation_id text,
    lifecycle task_lifecycle NOT NULL DEFAULT 'active',
    task_version bigint NOT NULL DEFAULT 1 CHECK (task_version > 0),
    confirmed_revision integer,
    pending_revision integer,
    deletion_status text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, task_id)
);

CREATE TABLE query_revision (
    task_id text NOT NULL REFERENCES retrieval_task(task_id),
    revision integer NOT NULL CHECK (revision > 0),
    status revision_status NOT NULL,
    schema_version text NOT NULL,
    raw_request text NOT NULL CHECK (length(btrim(raw_request)) > 0),
    normalized_spec jsonb NOT NULL,
    spec_digest text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    confirmed_at timestamptz,
    PRIMARY KEY (task_id, revision),
    UNIQUE (task_id, spec_digest),
    CHECK (schema_version = 'query-spec@1')
);

ALTER TABLE retrieval_task
    ADD CONSTRAINT retrieval_task_confirmed_revision_fk
    FOREIGN KEY (task_id, confirmed_revision) REFERENCES query_revision(task_id, revision)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE retrieval_task
    ADD CONSTRAINT retrieval_task_pending_revision_fk
    FOREIGN KEY (task_id, pending_revision) REFERENCES query_revision(task_id, revision)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE task_run (
    task_id text NOT NULL REFERENCES retrieval_task(task_id),
    generation integer NOT NULL CHECK (generation > 0),
    revision integer,
    status run_status NOT NULL,
    stage text,
    fencing_token uuid NOT NULL,
    plan_version integer,
    started_at timestamptz,
    ended_at timestamptz,
    stop_reason text,
    PRIMARY KEY (task_id, generation),
    FOREIGN KEY (task_id, revision) REFERENCES query_revision(task_id, revision)
);

CREATE UNIQUE INDEX one_schedulable_run_per_task
    ON task_run(task_id)
    WHERE status IN ('CREATED', 'PARSING', 'AWAITING_INPUT', 'PLANNING', 'RUNNING', 'PAUSED');

CREATE TABLE idempotency_record (
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    task_id text,
    route text NOT NULL,
    idempotency_key text NOT NULL,
    request_digest text NOT NULL,
    response_status integer NOT NULL,
    response_body jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, user_id, route, idempotency_key)
);

CREATE TABLE operation (
    operation_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    task_id text NOT NULL REFERENCES retrieval_task(task_id),
    generation integer NOT NULL,
    operation_type text NOT NULL,
    status operation_status NOT NULL,
    idempotency_key text NOT NULL,
    input_digest text NOT NULL,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_id, generation, operation_type, idempotency_key),
    FOREIGN KEY (task_id, generation) REFERENCES task_run(task_id, generation)
);

CREATE TABLE candidate (
    candidate_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    task_id text NOT NULL REFERENCES retrieval_task(task_id),
    revision integer NOT NULL,
    media_unit_id text NOT NULL,
    asset_version text NOT NULL,
    content_digest text,
    first_seen_generation integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_id, revision, media_unit_id, asset_version),
    FOREIGN KEY (task_id, revision) REFERENCES query_revision(task_id, revision)
);

CREATE TABLE retrieval_hit (
    hit_id text PRIMARY KEY,
    candidate_id text NOT NULL REFERENCES candidate(candidate_id),
    operation_id text NOT NULL REFERENCES operation(operation_id),
    library_id text NOT NULL,
    physical_binding_id text NOT NULL,
    index_version text NOT NULL,
    vector_primary_key text NOT NULL,
    query_branch_id text NOT NULL,
    raw_score double precision,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (operation_id, physical_binding_id, index_version, vector_primary_key, query_branch_id)
);

CREATE TABLE feature_record (
    feature_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    media_unit_id text NOT NULL,
    asset_version text NOT NULL,
    feature_schema text NOT NULL,
    algorithm_ref text NOT NULL,
    preprocessing_digest text NOT NULL,
    parameter_digest text NOT NULL,
    observation jsonb,
    execution_status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, media_unit_id, asset_version, feature_schema, algorithm_ref, preprocessing_digest, parameter_digest)
);

CREATE TABLE validation_result (
    validation_id text PRIMARY KEY,
    candidate_id text NOT NULL REFERENCES candidate(candidate_id),
    task_id text NOT NULL,
    revision integer NOT NULL,
    rule_id text NOT NULL,
    rule_digest text NOT NULL,
    feature_id text REFERENCES feature_record(feature_id),
    verdict verdict NOT NULL,
    reason_code text NOT NULL,
    evidence_ref text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (candidate_id, revision, rule_digest),
    FOREIGN KEY (task_id, revision) REFERENCES query_revision(task_id, revision)
);

CREATE TABLE result_set (
    result_set_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    task_id text NOT NULL REFERENCES retrieval_task(task_id),
    revision integer NOT NULL,
    generation integer NOT NULL,
    kind text NOT NULL CHECK (kind IN ('preview', 'delivery')),
    accepted_count integer NOT NULL CHECK (accepted_count >= 0),
    pending_review_count integer NOT NULL CHECK (pending_review_count >= 0),
    manifest_digest text,
    published_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_id, revision, generation, kind, published_at),
    FOREIGN KEY (task_id, revision) REFERENCES query_revision(task_id, revision),
    FOREIGN KEY (task_id, generation) REFERENCES task_run(task_id, generation)
);

CREATE TABLE result_member (
    result_set_id text NOT NULL REFERENCES result_set(result_set_id),
    candidate_id text NOT NULL REFERENCES candidate(candidate_id),
    rank integer NOT NULL CHECK (rank > 0),
    stable_sort_key text NOT NULL,
    PRIMARY KEY (result_set_id, candidate_id),
    UNIQUE (result_set_id, rank),
    UNIQUE (result_set_id, stable_sort_key)
);

CREATE TABLE task_event (
    event_id text PRIMARY KEY,
    task_id text NOT NULL REFERENCES retrieval_task(task_id),
    sequence bigint NOT NULL CHECK (sequence > 0),
    event_schema_version text NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_id, sequence)
);

CREATE TABLE outbox_event (
    event_id text PRIMARY KEY REFERENCES task_event(event_id),
    topic text NOT NULL,
    payload jsonb NOT NULL,
    published_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX candidate_task_revision_idx ON candidate(task_id, revision, created_at);
CREATE INDEX validation_task_revision_idx ON validation_result(task_id, revision, verdict);
CREATE INDEX task_event_replay_idx ON task_event(task_id, sequence);
CREATE INDEX outbox_pending_idx ON outbox_event(next_attempt_at) WHERE published_at IS NULL;

COMMIT;
