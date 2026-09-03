"""The demo record: interviews a dashboard can actually be read against.

`supabase/migrations/*_patient_identity.sql` seeds ten people. This seeds their
calls — one interview for every state the review table can render, because a
dashboard with three completed rows demonstrates one branch of
`frontend/dashboard/src/format.ts` and leaves the other eight undrawn.

**Why Python and not a migration.** `clinical.interviews.protocol_id` references
`config.protocols`, which is not seeded by SQL: `db.seed_protocols()` publishes
it from `services/agent/config/protocol.py` at boot, so the whole protocol has
one source. A migration inserting interviews would either run before that table
was populated or duplicate the script in SQL. Seeding here runs immediately
after `seed_protocols()` and takes the labels *from the protocol*, so a renamed
question renames the seeded record too.

Idempotent by `on conflict do nothing`, exactly as the protocol seed is, so a
re-boot is a no-op and a partially-applied seed heals. `transcript.events` has
no natural key beyond `(session_id, seq)`, which is unique — so it conflicts on
that rather than needing an id.

**These are not calls that happened.** Every other interview on the deployment
is: a visitor pressed Start and spoke. These eighteen are written, and they carry
`pt_demo_*` patients and `iv_demo_*` ids so they can be told apart in one query.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from services.agent.config.protocol import PROTOCOLS

logger = logging.getLogger(__name__)

PREOP = "proto_preop_check_v2"
WARMUP = "proto_warmup_v1"


@dataclass(frozen=True)
class Turn:
    """One thing said, and what the gate made of it.

    `hits` are red-flag ids as `gate.py` records them, and `action` is the
    worst one's action — the same two fields `safety.scanned` carries, because
    the dashboard reads them straight out of the payload.
    """

    who: str
    text: str
    hits: tuple[str, ...] = ()
    action: str | None = None
    blocked: bool = False


def a(text: str) -> Turn:
    return Turn("assistant", text)


def p(text: str, *hits: str, action: str | None = None, blocked: bool = False) -> Turn:
    return Turn("patient", text, hits, action, blocked)


@dataclass(frozen=True)
class Call:
    """One seeded interview, in the terms the record uses.

    `status` and `outcome` are the pair `store._record_outcome` writes, and
    together they pick the sentence the review table draws — which is why every
    branch of `format.ts::outcome` has a row here.
    """

    n: int
    patient: str
    protocol: str
    status: str
    outcome: str | None = None
    turns: tuple[Turn, ...] = ()
    captured: dict[str, str] = field(default_factory=dict)
    #: How long ago the call started, and how long it ran.
    started_ago: timedelta | None = None
    ran: timedelta = timedelta(minutes=6)
    #: For a queued row: how far ahead it is booked.
    scheduled_in: timedelta | None = None

    @property
    def id(self) -> str:
        return f"iv_demo_{self.n:02d}"

    @property
    def session_id(self) -> str:
        return f"sess_demo_{self.n:02d}"


# ─── the conversations ───────────────────────────────────────────────────────
#
# Short on purpose. Enough turns that the transcript reads as a call and the
# gate is visibly running on every one of them — a `safety.scanned` that matched
# nothing is the evidence the scan happened, and the detail screen draws it.

ATTEND = "You're booked for Thursday the fourth, arriving at seven. Are you still able to come?"
ESCORT = "You'll need an adult to take you home and stay overnight. Have you got someone?"
FASTING = "Nothing to eat from midnight, water until six, nothing after. Is that clear?"
MEDS = "You were asked to stop your apixaban on Monday. Were you able to?"
HEALTH = (
    "Has anything changed with your health since your pre-op appointment — "
    "cough, cold, temperature, new medicines?"
)
CLOSE = "Before we finish, is there anything else you would like to talk about?"

SELF_HARM_SAY = (
    "Thank you for telling me that — it's important, and it's not something I should handle "
    "over a call like this. I'm going to stop here and make sure a person from the practice "
    "contacts you straight away. If you need someone right now, please call 999 or the "
    "Samaritans on 116 123."
)


def opening(name: str) -> Turn:
    return a(
        f"Hello {name}, this is Springfield Day Surgery Unit. I've got a quick check "
        "before your operation on Thursday — it should take about five minutes."
    )


CALLS: tuple[Call, ...] = (
    # ── ran to the end, nothing flagged ──────────────────────────────────────
    Call(
        n=1,
        patient="pt_demo_01",
        protocol=PREOP,
        status="completed",
        outcome="complete",
        started_ago=timedelta(hours=3, minutes=20),
        ran=timedelta(minutes=6),
        turns=(
            opening("Alice"),
            a(ATTEND),
            p("Yes, I'll be there at seven."),
            a(ESCORT),
            p("My daughter's taking me and staying over."),
            a(FASTING),
            p("Nothing from midnight, water until six. That's clear."),
            a(MEDS),
            p("Yes, I stopped it on the Monday like the letter said."),
            a(HEALTH),
            p("No, nothing's changed."),
            a(CLOSE),
            p("No, that's everything, thank you."),
        ),
        captured={
            "attendance": "Confirmed — arriving at seven",
            "escort_home": "Daughter, taking her home and staying overnight",
            "fasting_ack": "Understood — nothing from midnight, water until six",
            "meds_stopped": "Stopped on Monday as instructed",
            "health_change": "No change reported",
            "anything_else": "Nothing raised",
        },
    ),
    # ── flagged: the call is untouched and the unit decides ──────────────────
    Call(
        n=20,
        patient="pt_demo_05",
        protocol=PREOP,
        status="completed",
        outcome="complete",
        started_ago=timedelta(hours=9, minutes=15),
        ran=timedelta(minutes=7),
        turns=(
            opening("Erin"),
            a(ATTEND),
            p("Yes, I'll be there."),
            a(ESCORT),
            p("My sister's collecting me and staying over."),
            a(FASTING),
            p("Nothing from midnight, water until six. Got it."),
            a(MEDS),
            p("I'm not on anything like that."),
            a(HEALTH),
            p(
                "I've had a bit of a cough and a temperature since the weekend.",
                "rf_fitness_change",
                action="soft_review",
            ),
            a(CLOSE),
            p("No, nothing else."),
        ),
        captured={
            "attendance": "Confirmed",
            "escort_home": "Sister, collecting and staying overnight",
            "fasting_ack": "Understood",
            "meds_stopped": "Not on an anticoagulant",
            "health_change": "Cough and temperature since the weekend",
            "anything_else": "Nothing raised",
        },
    ),
    Call(
        n=21,
        patient="pt_demo_09",
        protocol=PREOP,
        status="completed",
        outcome="complete",
        started_ago=timedelta(days=2, hours=5),
        ran=timedelta(minutes=6),
        turns=(
            opening("Iris"),
            a(ATTEND),
            p("Yes, still coming."),
            a(ESCORT),
            p("My daughter's taking me home."),
            a(FASTING),
            p("Yes, that's clear."),
            a(MEDS),
            p(
                "I took it this morning, actually — I thought I was meant to keep going.",
                "rf_anticoagulant_taken",
                action="soft_review",
            ),
            a(HEALTH),
            p("No, nothing's changed."),
            a(CLOSE),
            p("No, that's everything."),
        ),
        captured={
            "attendance": "Confirmed",
            "escort_home": "Daughter",
            "fasting_ack": "Understood",
            "meds_stopped": "Still taking — took a dose this morning",
            "health_change": "No change reported",
            "anything_else": "Nothing raised",
        },
    ),
    # ── flagged: the booking, not the patient ────────────────────────────────
    Call(
        n=4,
        patient="pt_demo_04",
        protocol=PREOP,
        status="completed",
        outcome="complete",
        started_ago=timedelta(days=1, hours=7),
        ran=timedelta(minutes=6),
        turns=(
            opening("Dev"),
            a(ATTEND),
            p("Yes, I'll be there."),
            a(ESCORT),
            p(
                "That's the thing — there's nobody who can pick me up. I live alone.",
                "yf_no_escort",
                action="soft_review",
            ),
            a(FASTING),
            p("Yes, understood."),
            a(MEDS),
            p("Stopped it on Monday."),
            a(HEALTH),
            p("No, all fine."),
            a(CLOSE),
            p("No, thank you."),
        ),
        captured={
            "attendance": "Confirmed",
            "escort_home": "None — lives alone, no one available",
            "fasting_ack": "Understood",
            "meds_stopped": "Stopped Monday",
            "health_change": "No change reported",
            "anything_else": "Nothing raised",
        },
    ),
    Call(
        n=5,
        patient="pt_demo_05",
        protocol=PREOP,
        status="completed",
        outcome="complete",
        started_ago=timedelta(days=2, hours=1),
        ran=timedelta(minutes=5),
        turns=(
            opening("Erin"),
            a(ATTEND),
            p(
                "I might have to rearrange — I'm away that week.",
                "yf_attendance_risk",
                action="soft_review",
            ),
            a(ESCORT),
            p("My son would bring me, if it moves."),
            a(FASTING),
            p("Yes, that's clear."),
            a(MEDS),
            p("Stopped it Monday."),
            a(HEALTH),
            p("No changes."),
            a(CLOSE),
            p("Just when someone can call me back about the date."),
        ),
        captured={
            "attendance": "At risk — may need to rearrange, away that week",
            "escort_home": "Son, if the date moves",
            "fasting_ack": "Understood",
            "meds_stopped": "Stopped Monday",
            "health_change": "No change reported",
            "anything_else": "Asked for a call back about the date",
        },
    ),
    # ── nothing flagged, and still worth reading ─────────────────────────────
    Call(
        n=6,
        patient="pt_demo_06",
        protocol=PREOP,
        status="completed",
        outcome="complete",
        started_ago=timedelta(days=2, hours=6),
        ran=timedelta(minutes=6),
        turns=(
            opening("Femi"),
            a(ATTEND),
            p("Yes, I'll be there."),
            a(ESCORT),
            p("My brother's taking me home."),
            a(FASTING),
            p("Understood."),
            a(MEDS),
            p("Stopped on Monday."),
            a(HEALTH),
            p("No, nothing."),
            a(CLOSE),
            p("I'm a bit nervous about the anaesthetic, that's all."),
        ),
        captured={
            "attendance": "Confirmed",
            "escort_home": "Brother",
            "fasting_ack": "Understood",
            "meds_stopped": "Stopped Monday",
            "health_change": "No change reported",
            "anything_else": "Nervous about the anaesthetic",
        },
    ),
    # ── urgent: the call runs on, and a clinician owes an answer ─────────────
    #
    # The only `urgent_escalate` in the seed, and the reason the level is not
    # empty. Nothing about the call says it: it ran to the end, every field was
    # captured, and the row is `completed / complete` like any other. The flag
    # is the whole of the difference, which is what the escalation band exists
    # to say out loud.
    #
    # `iv_demo_07` is written again here. The rename migration removed it so the
    # seed could rewrite it with the current name, and it is the call this
    # patient did not otherwise have.
    Call(
        n=7,
        patient="pt_demo_07",
        protocol=PREOP,
        status="completed",
        outcome="complete",
        started_ago=timedelta(hours=5, minutes=40),
        ran=timedelta(minutes=6),
        turns=(
            opening("Greta"),
            a(ATTEND),
            p("Yes, I'll be there."),
            a(ESCORT),
            p("My son's driving me and staying over."),
            a(FASTING),
            p("Nothing from midnight. Understood."),
            a(MEDS),
            p("I stopped it on Monday, as I was told."),
            a(HEALTH),
            p(
                "The hip's been getting worse this week, and the skin over it has "
                "gone red and hot.",
                "surgical_site_change",
                action="urgent_escalate",
            ),
            a(CLOSE),
            p("No, that's everything."),
        ),
        captured={
            "attendance": "Confirmed",
            "escort_home": "Son, driving and staying overnight",
            "fasting_ack": "Understood",
            "meds_stopped": "Stopped Monday",
            "health_change": "Hip worse this week; skin over it red and hot",
            "anything_else": "Nothing raised",
        },
    ),
    # ── the gate stopping a call ─────────────────────────────────────────────
    #
    # The only `end_call` in the seed, and the only red that stops a call rather
    # than letting it run on. `blocked=True` is what `safety.scanned` carries
    # when the gate fires: the agent reads the flag's own `say` and hangs up, so
    # the row is filed `abandoned / safety` with four fields already captured —
    # which is what the detail screen draws against a call that ended early.
    Call(
        n=19,
        patient="pt_demo_10",
        protocol=PREOP,
        status="abandoned",
        outcome="safety",
        started_ago=timedelta(hours=3, minutes=40),
        ran=timedelta(minutes=5),
        turns=(
            opening("Jonas"),
            a(ATTEND),
            p("Yes, I'll be there."),
            a(ESCORT),
            p("My son's driving me and staying the night."),
            a(FASTING),
            p("Nothing after midnight, understood."),
            a(MEDS),
            p("Stopped it Monday, yes."),
            a(HEALTH),
            p(
                "Honestly? I'd rather end my life than go through with this operation.",
                "rf_self_harm",
                action="end_call",
                blocked=True,
            ),
            a(SELF_HARM_SAY),
        ),
        captured={
            "attendance": "Confirmed",
            "escort_home": "Son, driving and staying overnight",
            "fasting_ack": "Understood",
            "meds_stopped": "Stopped Monday",
        },
    ),
    # ── the patient left ─────────────────────────────────────────────────────
    Call(
        n=8,
        patient="pt_demo_08",
        protocol=PREOP,
        status="abandoned",
        outcome="patient_left",
        started_ago=timedelta(days=3, hours=4),
        ran=timedelta(minutes=4),
        turns=(
            opening("Hari"),
            a(ATTEND),
            p("Yes, I'm still coming."),
            a(ESCORT),
            p("My wife will collect me."),
            a(FASTING),
        ),
        captured={"attendance": "Confirmed", "escort_home": "Wife"},
    ),
    # ── the ceiling, the deploy, the early ending, the silence ───────────────
    Call(
        n=9,
        patient="pt_demo_09",
        protocol=PREOP,
        status="abandoned",
        outcome="max_duration",
        started_ago=timedelta(days=4, hours=2),
        ran=timedelta(minutes=20),
        turns=(
            opening("Iris"),
            a(ATTEND),
            p("Sorry, could you say that again? The line's not great."),
            a(ATTEND),
            p("Yes — yes, I can come."),
            a(ESCORT),
            p("My daughter, I think. I'll have to check with her."),
            a(FASTING),
            p("Midnight, and water until six. Yes."),
            a(MEDS),
            p("I stopped it Monday."),
            a(HEALTH),
        ),
        captured={
            "attendance": "Confirmed",
            "escort_home": "Daughter, to be confirmed",
            "fasting_ack": "Understood",
            "meds_stopped": "Stopped Monday",
        },
    ),
    Call(
        n=10,
        patient="pt_demo_10",
        protocol=PREOP,
        status="abandoned",
        outcome="server_shutdown",
        started_ago=timedelta(days=5, hours=3),
        ran=timedelta(minutes=4),
        turns=(
            opening("Jonas"),
            a(ATTEND),
            p("Yes, I'll be there."),
            a(ESCORT),
            p("My flatmate's taking me."),
            a(FASTING),
            p("Understood, nothing from midnight."),
        ),
        captured={
            "attendance": "Confirmed",
            "escort_home": "Flatmate",
            "fasting_ack": "Understood",
        },
    ),
    Call(
        n=11,
        patient="pt_demo_01",
        protocol=PREOP,
        status="abandoned",
        outcome="ended_by_patient",
        started_ago=timedelta(days=6, hours=5),
        ran=timedelta(minutes=2),
        turns=(
            opening("Alice"),
            a(ATTEND),
            p("Yes, but I can't talk now — can someone ring me tomorrow?"),
        ),
        captured={"attendance": "Confirmed"},
    ),
    Call(
        n=12,
        patient="pt_demo_02",
        protocol=PREOP,
        status="abandoned",
        outcome="pipeline_finished",
        started_ago=timedelta(days=7, hours=1),
        ran=timedelta(minutes=3),
        turns=(
            opening("Ben"),
            a(ATTEND),
            p("Yes, still fine for Thursday."),
            a(ESCORT),
        ),
        captured={"attendance": "Confirmed"},
    ),
    # ── the other protocol ───────────────────────────────────────────────────
    Call(
        n=13,
        patient="pt_demo_09",
        protocol=WARMUP,
        status="completed",
        outcome="complete",
        started_ago=timedelta(days=2, hours=9),
        ran=timedelta(minutes=2),
        turns=(
            a(
                "Hello Iris, this is Dr Hollis's practice — just a quick chat "
                "before your appointment."
            ),
            a("How is your day going?"),
            p("Not bad, thanks. Bit of a slow start."),
            a(CLOSE),
            p("No, nothing else."),
        ),
        captured={
            "day_mood": "Slow start, otherwise fine",
            "anything_else": "Nothing raised",
        },
    ),
    # ── on the line right now ────────────────────────────────────────────────
    Call(
        n=14,
        patient="pt_demo_03",
        protocol=PREOP,
        status="running",
        started_ago=timedelta(minutes=4),
        turns=(
            opening("Chloe"),
            a(ATTEND),
            p("Yes, that still works."),
            a(ESCORT),
        ),
    ),
    # ── booked, not started ──────────────────────────────────────────────────
    Call(
        n=15, patient="pt_demo_04", protocol=PREOP,
        status="queued", scheduled_in=timedelta(days=1),
    ),
    Call(
        n=16, patient="pt_demo_05", protocol=PREOP,
        status="queued", scheduled_in=timedelta(days=3),
    ),
    Call(
        n=17, patient="pt_demo_06", protocol=WARMUP,
        status="queued", scheduled_in=timedelta(days=4),
    ),
)


# ─── building the rows ───────────────────────────────────────────────────────


def _fields(protocol_id: str) -> list[tuple[str, str]]:
    """`(field_key, label)` in script order, from the protocol itself.

    Taken from the object rather than restated here, so a renamed question
    renames the seeded record and cannot drift from `clinical.results`.
    """
    protocol = PROTOCOLS[protocol_id]
    return [
        (question.field_key, question.label)
        for section in protocol.script.sections
        for question in section.questions
    ]


def _results(call: Call) -> list[tuple[str, str, str, str | None, str]]:
    """Every declared field, captured or not.

    `_record_outcome` writes the whole set when a call ends, so a half-finished
    interview has a row per question and only some of them captured — which is
    what the review table's meter counts. The first uncaptured field is `open`
    (it is the one the call was on) and the rest are `pending`.
    """
    if call.status in {"queued", "running"}:
        return []

    rows: list[tuple[str, str, str, str | None, str]] = []
    seen_gap = False
    for key, label in _fields(call.protocol):
        value = call.captured.get(key)
        if value is not None:
            status = "captured"
        elif seen_gap:
            status = "pending"
        else:
            status, seen_gap = "open", True
        rows.append((call.id, key, label, value, status))
    return rows


def _events(call: Call, started: datetime) -> list[tuple]:
    """The call as `transcript.events`, in the order the pipeline writes them.

    Only five of the seventeen event types carry words, and `transcript.ts`
    reads exactly those; the rest are here because a transcript that opens with
    the patient already talking does not look like a log of a call. Four are
    written here — the fifth, `closure.spoken`, belongs to a call the pipeline
    itself stopped and has no seeded equivalent.
    """
    if call.status == "queued":
        return []

    rows: list[tuple] = []
    seq = 0
    at = started

    def add(kind: str, payload: dict) -> None:
        nonlocal seq, at
        seq += 1
        rows.append((call.id, call.session_id, seq, kind, at, payload))
        at = at + timedelta(seconds=9)

    add(
        "session.created",
        {
            "type": "session.created",
            "protocolId": call.protocol,
            "patientId": call.patient,
            "roomName": f"room_{call.session_id}",
        },
    )
    add("room.joined", {"type": "room.joined", "identity": "agent"})
    add("patient.joined", {"type": "patient.joined", "identity": "patient"})

    spoken = 0
    for turn in call.turns:
        if turn.who == "assistant":
            if spoken == 0:
                add(
                    "opening.spoken",
                    {"type": "opening.spoken", "text": turn.text, "chunks": 2, "ms": 940.0},
                )
            else:
                add(
                    "llm.completed",
                    {"type": "llm.completed", "text": turn.text, "toolCalls": 0},
                )
            spoken += 1
            continue

        add(
            "turn.committed",
            {
                "type": "turn.committed",
                "transcript": turn.text,
                "durationMs": 1000.0 + 55.0 * len(turn.text),
                "source": "voice",
            },
        )
        add(
            "safety.scanned",
            {
                "type": "safety.scanned",
                "blocked": turn.blocked,
                "hits": list(turn.hits),
                "action": turn.action,
            },
        )

    if call.status != "running":
        add(
            "session.ended",
            {
                "type": "session.ended",
                "reason": call.outcome,
                "fields": {key: call.captured.get(key) for key, _ in _fields(call.protocol)},
            },
        )
    return rows


def _interview(call: Call, now: datetime) -> tuple:
    started = None if call.started_ago is None else now - call.started_ago
    ended = None
    if started is not None and call.status not in {"running", "queued"}:
        ended = started + call.ran
    scheduled = None if call.scheduled_in is None else now + call.scheduled_in
    # A booked call was booked in the past even though it happens in the future.
    # Using `scheduled_for` here would give it a `created_at` ahead of every
    # completed call and float it to the top of a table ordered by activity.
    created = started or (now - timedelta(days=1) if scheduled else now)
    return (
        call.id,
        call.protocol,
        call.patient,
        call.status,
        call.outcome,
        scheduled,
        started,
        ended,
        created,
    )


# ─── the write ───────────────────────────────────────────────────────────────


async def seed_demo_record(pool) -> None:
    """Write the seeded calls, once, and re-book the ones that never ran.

    Silent when the roster is absent: a database with the schema but not the
    identity migration has no `pt_demo_*` to attach a call to, and the demo
    working without them is more important than the seed landing.
    """
    if pool is None:
        return

    roster = await pool.fetchval(
        "select count(*) from clinical.patients where id like 'pt_demo_%'"
    )
    if not roster:
        logger.info("seed · no demo roster, skipping the seeded record")
        return

    now = datetime.now(UTC)
    async with pool.acquire() as conn, conn.transaction():
        await conn.executemany(
            "insert into clinical.interviews "
            "(id, protocol_id, patient_id, status, outcome, scheduled_for, "
            " started_at, ended_at, created_at) "
            "values ($1, $2, $3, $4, $5, $6, $7, $8, $9) on conflict (id) do nothing",
            [_interview(call, now) for call in CALLS],
        )
        await conn.executemany(
            "insert into clinical.results (interview_id, field_key, label, value, status) "
            "values ($1, $2, $3, $4, $5) on conflict (interview_id, field_key) do nothing",
            [row for call in CALLS for row in _results(call)],
        )
        await conn.executemany(
            "insert into transcript.events "
            "(interview_id, session_id, seq, type, at, payload) "
            "values ($1, $2, $3, $4, $5, $6) on conflict (session_id, seq) do nothing",
            [
                row
                for call in CALLS
                for row in _events(
                    call, now - (call.started_ago or timedelta(0))
                )
            ],
        )
        # A booked call that is never taken drifts into the past and the
        # "Scheduled care calls" card stops meaning anything. These rows have no
        # transcript, so moving them is not rewriting a record of anything.
        await conn.executemany(
            "update clinical.interviews set scheduled_for = $2 "
            "where id = $1 and status = 'queued'",
            [
                (call.id, now + call.scheduled_in)
                for call in CALLS
                if call.scheduled_in is not None
            ],
        )
    logger.info(f"seed · {len(CALLS)} demo interviews present")
