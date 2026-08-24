-- Phase 1 · the four schemas from system-map.md's store cut.
--
-- Four schemas, one database. The boundary that matters is which code may
-- touch which table, not which host it lives on, and a schema holds that line
-- while a single connection string still reaches everything.

create schema if not exists clinical;
create schema if not exists transcript;
create schema if not exists config;
create schema if not exists metrics;   -- claimed now, populated at Phase 6+.


-- ─── config.protocols ────────────────────────────────────────────────────────
--
-- A ProtocolVersion is frozen at publish and pinned by every interview that
-- runs it, so a captured field can always be read back against the question
-- that produced it. `PROTOCOLS` in services/agent/config/protocol.py seeds it;
-- the agent studio becomes the second writer at Phase 6+.

create table config.protocols (
    id          text primary key,
    label       text not null,
    version     jsonb not null,   -- the whole ProtocolVersion, camelCase as on the wire
    created_at  timestamptz not null default now()
);

-- Immutability is the entire point of the table, so it is enforced here rather
-- than trusted to every future caller.
create or replace function config.reject_mutation() returns trigger
language plpgsql as $$
begin
    raise exception
        'config.protocols is append-only: % on % is refused. Publish a new id instead.',
        tg_op, tg_table_name;
end;
$$;

create trigger protocols_immutable
    before update or delete on config.protocols
    for each row execute function config.reject_mutation();


-- ─── clinical.patients ───────────────────────────────────────────────────────
--
-- Synthetic only. deployment.md §2's trade stands: no PHI posture, so nothing
-- here may become a real person without revisiting that section first.

create table clinical.patients (
    id          text primary key,
    first_name  text not null,
    created_at  timestamptz not null default now()
);


-- ─── clinical.interviews ─────────────────────────────────────────────────────
--
-- This is the dispatch queue. A status column rather than a broker: dispatch
-- is a single row transitioning under one process, and it has to stay
-- queryable by a clinician afterwards, which a message in a broker is not.

create table clinical.interviews (
    id             text primary key,
    protocol_id    text not null references config.protocols (id),
    patient_id     text not null references clinical.patients (id),
    status         text not null default 'queued'
                   check (status in ('queued', 'running', 'completed', 'abandoned')),
    scheduled_for  timestamptz,
    outcome        text,
    started_at     timestamptz,
    ended_at       timestamptz,
    created_at     timestamptz not null default now()
);

-- What next_interview() runs, and the only hot read on this table.
create index interviews_queued
    on clinical.interviews (scheduled_for nulls first, created_at)
    where status = 'queued';

create index interviews_by_patient on clinical.interviews (patient_id, created_at desc);


-- ─── transcript.events ───────────────────────────────────────────────────────
--
-- Exactly the session_log.py event shapes, append-only, one row per line the
-- JSONL writer would have written.
--
-- Both ids are kept. `interview_id` is what a clinician asks questions about;
-- `session_id` is what the wire and the JSONL already use, and keeping it is
-- what makes the Phase 1 verification -- diff these rows against the JSONL the
-- same call wrote -- a straight comparison rather than a reconstruction.

create table transcript.events (
    id            bigint generated always as identity primary key,
    interview_id  text not null references clinical.interviews (id),
    session_id    text not null,
    seq           integer not null,   -- per-session, assigned by the writer
    type          text not null,      -- the discriminator, lifted out for indexing
    at            timestamptz not null,
    payload       jsonb not null,     -- the event as the JSONL line, minus at/sessionId
    unique (session_id, seq)
);

create index events_by_interview on transcript.events (interview_id, seq);
create index events_by_type on transcript.events (interview_id, type);

create or replace function transcript.reject_mutation() returns trigger
language plpgsql as $$
begin
    raise exception
        'transcript.events is append-only: % is refused. The record of a call does not change.',
        tg_op;
end;
$$;

create trigger events_immutable
    before update or delete on transcript.events
    for each row execute function transcript.reject_mutation();


-- ─── clinical.results ────────────────────────────────────────────────────────
--
-- The captured fields, one row per field, for the review composer. Derived
-- from transcript.events and therefore rebuildable -- but the composer should
-- not have to replay a conversation to render a row.

create table clinical.results (
    interview_id  text not null references clinical.interviews (id),
    field_key     text not null,
    label         text not null,
    value         text,
    status        text not null
                  check (status in ('pending', 'live', 'open', 'captured')),
    updated_at    timestamptz not null default now(),
    primary key (interview_id, field_key)
);
