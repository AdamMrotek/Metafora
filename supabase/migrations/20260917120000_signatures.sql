-- Phase 5c · the signature ledger.
--
-- Everything up to now answers "what happened". This is the one column that
-- answers "who is accountable for it, and can that be proven": a named
-- clinician signs one interview, irreversibly, and the signature is chained to
-- the one before it so the sequence of who-signed-what cannot be reordered or
-- have a link quietly removed from the middle.

create table clinical.signatures (
    id              text primary key,
    -- One signature per interview. A second attempt is a 409 in
    -- `services/core/ledger.py`, not a second row.
    interview_id    text not null unique references clinical.interviews (id),
    -- The chain. `prev_hash` unique is what stops the chain forking — two
    -- signatures both claiming the same predecessor cannot both land.
    prev_hash       text not null unique,
    -- sha256 of the interview row, its `clinical.results` ordered by
    -- `field_key`, and its `transcript.events` ordered by `seq` — the record
    -- as it stood the moment it was signed. See `ledger.py`.
    record_hash     text not null,
    hash            text not null unique,
    -- Composed by the server from columns a query already produced, never
    -- accepted from the client — see `ledger._issued_summary`. In particular
    -- it does not repeat `frontend/dashboard/src/demo.ts`'s invented referral
    -- clause: a signature is exactly the object this repo refuses to let an
    -- invented value reach.
    issued_summary  text not null,
    impression      text not null,
    disposition     text not null,
    signed_by       text not null references config.accounts (email),
    signed_at       timestamptz not null default now()
);

create or replace function clinical.reject_mutation() returns trigger
language plpgsql as $$
begin
    raise exception
        'clinical.signatures is append-only: % on % is refused. A signature is not corrected, it is superseded by a new interview.',
        tg_op, tg_table_name;
end;
$$;

create trigger signatures_immutable
    before update or delete on clinical.signatures
    for each row execute function clinical.reject_mutation();


-- ─── the head, as one row rather than a query ─────────────────────────────────
--
-- The plan this stage was built from said "take the head under `select ... for
-- update`" over the latest row of `clinical.signatures` itself. That does not
-- serialise two concurrent signers: a `SELECT ... FOR UPDATE` that blocks on a
-- locked row re-reads *that same row* once it unblocks, and the row
-- `clinical.signatures` orders on to find "the latest one" is never itself
-- updated by the signer who wins the race — so the loser would recompute the
-- same `prev_hash` the winner already used and rely on the unique constraint to
-- fail loudly rather than on the lock to queue it correctly.
--
-- A single mutable row does what the plan wanted: locking it blocks a second
-- signer until the first commits, and because this row *is* updated by the
-- winner, the loser's blocked read returns the fresh value once it unblocks.
create table clinical.ledger_head (
    id    boolean primary key default true,
    -- `repeat('0', 64)`: not a real hash, the empty chain's `prev_hash`. Kept
    -- as a fixed-width hex string like every other hash in this table, so a
    -- reader does not have to learn that "genesis" is spelled differently.
    hash  text not null
);

alter table clinical.ledger_head add constraint ledger_head_is_one_row check (id);

insert into clinical.ledger_head (id, hash) values (true, repeat('0', 64));
