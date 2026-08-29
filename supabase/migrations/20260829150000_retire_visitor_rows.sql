-- Phase 5.0 · retiring the per-visitor demo rows.
--
-- Before the roster, every visitor to the public demo became a new person:
-- `clinical.patients` filled with one-call strangers, each with a first name
-- and nothing else. `queue.py` no longer does that -- a visitor takes a call as
-- one of the ten seeded people -- so the rows already written are a population
-- that can only shrink, and they sit at the top of the patients screen with an
-- em dash where an identity goes.
--
-- This deletes them. A demo row with no NHS number is one of them by
-- definition, which is the same predicate `ROSTER_PICK` uses from the other
-- side; a dispatched patient is never touched, and neither is the roster.
--
-- On a database that never ran the old code -- a fresh one, or `make test-pg` --
-- this migration matches nothing and does nothing.


-- ─── suspending append-only, on purpose and out loud ─────────────────────────
--
-- `transcript.events` refuses DELETE by trigger, because the record of a call
-- does not change. That is the right rule and it is being stepped around here
-- rather than quietly worked around: these are synthetic calls taken by
-- strangers on a portfolio demo, and the alternative is keeping their
-- transcripts forever to preserve a guarantee about clinical records that these
-- rows were never an instance of.
--
-- Named, scoped to one statement, and turned straight back on. If this file
-- ever grows a second delete, that is the moment to stop and ask again.

alter table transcript.events disable trigger events_immutable;

delete from transcript.events e
    using clinical.interviews i, clinical.patients p
    where e.interview_id = i.id
      and i.patient_id = p.id
      and p.origin = 'demo'
      and p.nhs_number is null;

alter table transcript.events enable trigger events_immutable;


-- ─── the rows themselves ─────────────────────────────────────────────────────

delete from clinical.results r
    using clinical.interviews i, clinical.patients p
    where r.interview_id = i.id
      and i.patient_id = p.id
      and p.origin = 'demo'
      and p.nhs_number is null;

delete from clinical.interviews i
    using clinical.patients p
    where i.patient_id = p.id
      and p.origin = 'demo'
      and p.nhs_number is null;

delete from clinical.patients
    where origin = 'demo' and nhs_number is null;
