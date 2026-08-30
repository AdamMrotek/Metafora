-- Renaming the seventh seeded patient: Greta -> Gwen.
--
-- The roster in `20260829094500_patient_identity.sql` is left as it was
-- written. An applied migration is a record of what was run, and editing one
-- would make a fresh database and an existing one disagree about their own
-- history to save a database nothing -- so the rename is a statement rather
-- than a correction. A fresh database inserts the old name and then renames
-- the row; every other database is brought level by the same line.
--
-- Guarded on the old name so re-running is a no-op and a hand-edited row is
-- not overwritten.

update clinical.patients
    set first_name = 'Gwen'
    where id = 'pt_demo_07' and first_name = 'Greta';


-- ─── what is deliberately not renamed ────────────────────────────────────────
--
-- Three interviews have "Hello Greta" spoken in `transcript.events`, which
-- refuses UPDATE by trigger. Two of them are calls somebody actually took as
-- this patient, and the agent did say that name -- rewriting them would be the
-- one thing the append-only rule exists to prevent. The third is the seeded
-- `iv_demo_07`, a fixture rather than a record; it is left alone too, because
-- reaching past `events_immutable` for a cosmetic name is not the trade
-- `20260829150000_retire_visitor_rows.sql` made when it suspended the trigger,
-- and that file asks the next caller to stop and ask again.
--
-- A database seeded after this migration never sees the old name at all.
