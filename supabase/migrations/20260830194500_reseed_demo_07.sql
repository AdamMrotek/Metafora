-- Phase 5.0 · letting the seeded call for pt_demo_07 be written again.
--
-- `20260830193000_demo_07_rename.sql` renamed the patient and deliberately left
-- every transcript alone. Two of the three that say the old name are calls
-- somebody actually took, and they stay that way -- the agent did say it. The
-- third, `iv_demo_07`, is not a call that happened: `services/core/seed.py` is
-- fifteen *written* interviews, one per state the review table can render, and
-- its own docstring says so. A fixture whose opening line contradicts the row
-- it belongs to is a bug in the fixture, not a record to be preserved.
--
-- The seed is idempotent by `on conflict do nothing`, so it cannot heal this on
-- its own. Removing the interview is what lets it write the call again, with
-- the current name, on the next boot.
--
-- On a database seeded after the rename this matches nothing and does nothing.


-- ─── suspending append-only, on purpose and out loud ─────────────────────────
--
-- This is the second file to reach past `events_immutable`, which
-- `20260829150000_retire_visitor_rows.sql` asked the next caller to stop and
-- think about. The trade is narrower than that one's and it is scoped to a
-- single id: `iv_demo_07` is synthetic by construction, authored in this repo,
-- and reproduced verbatim by the seed the moment it is gone. Nothing is lost
-- that is not immediately rewritten.
--
-- Named, scoped to one statement, and turned straight back on.

alter table transcript.events disable trigger events_immutable;

delete from transcript.events where interview_id = 'iv_demo_07';

alter table transcript.events enable trigger events_immutable;


-- ─── the row itself, and what references it ──────────────────────────────────
--
-- `clinical.invitations` is here for the foreign key rather than because a
-- seeded call has one; a link minted against this interview by hand would
-- otherwise block the statement below it.

delete from clinical.results     where interview_id = 'iv_demo_07';
delete from clinical.invitations where interview_id = 'iv_demo_07';
delete from clinical.interviews  where id           = 'iv_demo_07';


-- The acknowledgement goes with the row. That is the honest outcome rather than
-- an oversight: `acknowledged_at` says a named human saw *this* call, and the
-- call it said it about no longer exists. The rewritten one arrives
-- unacknowledged, so its red flag is owed again -- which is also the state a
-- fresh demo deployment is in.
