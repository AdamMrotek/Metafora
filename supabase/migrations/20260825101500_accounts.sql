-- Phase 2 · who may read the record.
--
-- Two things, and they are one thought: an account is a row the repo put here,
-- and a patient may name one. Signing up must not be the same act as being
-- granted a caseload -- on a public demo URL that is exactly what self-serve
-- would mean -- so `config.accounts` is seeded by this file and nothing in the
-- application ever inserts into it.


-- ─── config.accounts ─────────────────────────────────────────────────────────
--
-- Not append-only, unlike `config.protocols`. One column is written by the
-- application: `sub`, the Supabase auth user id, bound the first time a
-- verified token arrives for that email. Everything else is the migration's.
--
-- Email is the key rather than `sub` because the seed has to be written before
-- the auth user exists -- the console creates the user, this file says what
-- that user is allowed to do, and `sub` is how the two meet.

create table config.accounts (
    email         text primary key,
    -- The `sub` claim of the verified JWT. Null until the first sign-in; once
    -- set, a token for this email carrying a different `sub` is refused, so an
    -- account belongs to one auth user and stays there.
    sub           text unique,
    role          text not null check (role in ('clinician', 'admin')),
    display_name  text not null,
    created_at    timestamptz not null default now()
);


-- ─── clinical.patients.clinician_email ───────────────────────────────────────
--
-- Ownership as one column, not a policy engine. Roles are the whole of the
-- access model today -- one clinician, three read routes, every decision is
-- "are you a clinician, yes or no" -- but the dashboard promises a patients
-- screen scoped to that clinician's *own* list, and nothing else in the schema
-- can express "own".
--
-- Nullable, and null means unowned: a demo visitor is nobody's patient. Phase
-- 5's dispatch is the only thing that ever sets it, because being dispatched a
-- call is what makes someone yours.

alter table clinical.patients
    add column clinician_email text references config.accounts (email);

create index patients_by_clinician
    on clinical.patients (clinician_email, created_at desc);


-- ─── the seed ────────────────────────────────────────────────────────────────
--
-- The accounts that exist are the accounts this file says exist. Create the
-- matching user in the Supabase console (Authentication -> Users) with this
-- email; the first token it signs binds `sub` above. Change the address here
-- and re-run rather than inserting a row by hand.

insert into config.accounts (email, role, display_name) values
    ('adam.mrotek2@gmail.com', 'clinician', 'Dr A. Mrotek')
on conflict (email) do nothing;
