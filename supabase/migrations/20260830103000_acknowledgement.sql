-- Phase 5b · a red flag a human clears.
--
-- The record already knows a red flag was raised: `PostgresSessionWriter`
-- queues a `safety.scanned` row for every committed turn, and `reads.py`
-- derives the worst action on an interview from those rows. The one thing it
-- cannot produce is *that a human has seen it*, and that is the whole of this
-- migration.
--
-- Two columns, not a table. An escalation is not an entity with a life of its
-- own here: it is a call carrying an unacknowledged red, so the acknowledgement
-- belongs on the call. Per interview and not per flag, because "I have this" is
-- a statement about the call rather than about a pattern match -- a call
-- carrying two reds is cleared once -- and per-flag would need exactly the
-- table this stage is refusing.
--
-- No fifth `status`, either. "Completed and also flagged" is `status =
-- 'completed'` with `acknowledged_at` null, which leaves the existing CHECK and
-- `queue.claim()` untouched.

alter table clinical.interviews
    -- Not reversible and not reassignable: `acknowledgements.py` stamps both
    -- with `coalesce`, so a second POST returns the first stamp rather than
    -- quietly moving ownership of the decision to whoever clicked last.
    add column acknowledged_at timestamptz,
    add column acknowledged_by text references config.accounts (email);

-- The index the foreign key wants, on the same pattern as
-- `patients_by_clinician`. Partial because the column is null on almost every
-- row, and an index over "nobody has acknowledged this" would be an index over
-- the whole table.
--
-- Deliberately *not* an index on `acknowledged_at is null`: that predicate
-- matches nearly every interview ever recorded, so it selects nothing and
-- would cost a write on every insert to say so. What narrows the escalation
-- band is the flag, which lives in `transcript.events`.
create index interviews_acknowledged_by
    on clinical.interviews (acknowledged_by)
    where acknowledged_by is not null;
