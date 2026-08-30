-- Phase 5a · the link a patient follows.
--
-- Until now every call was the demo link: `POST /session` with nothing in the
-- body, resolving to whichever roster patient `random()` picked. An interview a
-- clinician queued for a named person needs the opposite -- a link that means
-- exactly one interview and nothing else -- and this table is what a link is.
--
-- The token is never stored. What is stored is `nonce` in the clear and
-- `sha256(token)`, and the token is *derived* rather than drawn:
--
--     token = base64url(hmac_sha256(INVITE_SECRET, interview_id || ':' || nonce))
--
-- which is what makes "copy the link again" give back the link already sent
-- instead of silently minting a second one and killing the first. A leak of
-- this table still yields no working link, because the secret is not in it, and
-- rotating the secret invalidates every outstanding link -- which is the
-- correct behaviour for a secret, and is checked by re-hashing before a reused
-- link is handed back.


create table clinical.invitations (
    id            text primary key,
    interview_id  text not null references clinical.interviews (id),
    -- Not a secret: it is half of the input the secret is applied to, and it is
    -- the only reason the same link can be re-derived on a second click.
    nonce         text not null,
    token_hash    text not null unique,
    -- `'link'` is the whole of Phase 5a: the clinician copies the URL and sends
    -- it however they like. `'email'` is here so that adding a sender later is
    -- a new service and not a migration under a live table.
    channel       text not null default 'link' check (channel in ('link', 'email')),
    created_at    timestamptz not null default now(),
    -- Stamped by whatever actually sends. Null for a copied link: handing a URL
    -- to a clinician is not the same event as it reaching a patient, and this
    -- column should not claim it is.
    sent_at       timestamptz,
    expires_at    timestamptz,
    -- Spent. Stamped by the arrival that resolves it, in the same statement
    -- that selects it, so two tabs opened from one link cannot both be first.
    opened_at     timestamptz,
    revoked_at    timestamptz
);

-- At most one live invitation per interview. This is what `mint` relies on to
-- find "the link already sent" without a scan, and what stops a rotation
-- leaving two usable links behind: rotating inserts a new row and must set
-- `revoked_at` on the old one, or the index refuses it.
create unique index invitations_live on clinical.invitations (interview_id)
    where revoked_at is null and opened_at is null;

create index invitations_by_interview
    on clinical.invitations (interview_id, created_at desc);

-- Deliberately *not* append-only, unlike `config.protocols` and
-- `transcript.events`. `opened_at` and `revoked_at` are the point of the table:
-- an invitation is a thing that gets spent, and a record that cannot record
-- that has no way to refuse a second use.
