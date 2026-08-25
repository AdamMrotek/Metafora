-- Where a patient came from.
--
-- Phase 1 gives the public demo an ephemeral synthetic patient per visitor
-- rather than one shared Alice, so the table now holds two kinds of row that
-- look identical and mean different things: someone a clinician dispatched a
-- call to, and someone who is an artefact of a link being clicked. A dashboard
-- that cannot tell them apart shows a caseload full of strangers.
--
-- Defaulted rather than required because every row that exists today, and every
-- row the demo writes, is a demo row. Phase 5's dispatch says 'dispatched'
-- explicitly, which is the only way it should ever be said.

alter table clinical.patients
    add column origin text not null default 'demo'
    check (origin in ('demo', 'dispatched'));
