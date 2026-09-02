"""The hardcoded ProtocolVersion.

In production these are published by the agent studio and pinned by the
interview. For now they are frozen objects in a dict, with all six blocks
present, so the studio has somewhere to publish into and the runtime has
something real to compile.

`WARMUP_V1` is the one the dispatch queue runs: deliberately trivial — say
hello, ask how the day is going. The machinery around it is not.

`PREOP_CHECK_V1` is `docs/example-interview.md` as an object. Nothing dispatches
it yet; it exists so the compile path, the gate and the tool matrix are
exercised against a protocol with two authored sections, five authored fields
and all four red-flag actions rather than one question and one flag.

Two things every protocol here carries without authoring them, and carries
unchanged: `SELF_HARM` at the front of the flags and `CLOSING` at the end of the
script. Both are decisions about how a call may treat a patient, which is not a
per-script choice — so the counts above are of what the author wrote, and every
protocol compiles to one more state and one more field than that.
"""

from shared.contracts.models import (
    BooleanCapture,
    Clinician,
    EnumCapture,
    InterviewScript,
    ProtocolVersion,
    Question,
    QuestionFlag,
    RedFlag,
    ReportGuidance,
    Section,
    TextCapture,
    ToolSpec,
    UrgentEscalation,
)

#: The one flag that is not protocol-specific. Every protocol carries it, and
#: carries it *unchanged* — the sentence a patient hears when a call stops for
#: this reason is not something an author should be able to reword per script.
SELF_HARM = RedFlag(
    id="rf_self_harm",
    label="Disclosure of self-harm or suicidal intent",
    patterns=[
        "kill myself",
        "end my life",
        "take my own life",
        "want to die",
        "harm myself",
        "hurt myself",
    ],
    action="end_call",
    say=(
        "Thank you for telling me that — it's important, and it's not something I "
        "should handle over a call like this. I'm going to stop here and make sure a "
        "person from the practice contacts you straight away. If you need someone "
        "right now, please call 999 or the Samaritans on 116 123."
    ),
    proving_utterance="sometimes I feel like I want to die",
)

#: The other thing every protocol carries unchanged. A patient gets one open
#: turn of their own before the line closes, and whatever they say in it is part
#: of the record — the concern raised on the way out is often the reason they
#: took the call.
#:
#: A *question* and not a line in the system prompt, because only the script
#: advances the machine: a closing sentence the model was merely told to say
#: would be unrecorded, absent from the notes card, and skippable on any turn
#: the model decided the call was already over. `must_capture=False` for the
#: same reason the warm-up is — "nothing, thanks" is an answer, but nobody is
#: owed one.
CLOSING = Section(
    id="close",
    title="Anything else",
    questions=[
        Question(
            id="q1",
            ask="Before we finish, is there anything else you would like to talk about?",
            field_key="anything_else",
            label="Anything else raised",
            capture=TextCapture(),
            if_unclear="accept whatever they say, including nothing",
            must_capture=False,
        )
    ],
)

WARMUP_V1 = ProtocolVersion(
    id="proto_warmup_v1",
    label="Warm-up v1",
    frozen=True,
    clinician=Clinician(
        name="Dr E. Hollis",
        initials="EH",
        practice="Springfield Practice",
        context="a quick chat before your appointment",
    ),
    # ── Block I · the only block the patient hears in full ──
    script=InterviewScript(
        sections=[
            Section(
                id="s1",
                title="Warm-up",
                questions=[
                    Question(
                        id="q1",
                        ask="How is your day going?",
                        field_key="day_mood",
                        label="How the day is going",
                        capture=TextCapture(),
                        if_unclear="one follow-up, then record what they said in their words",
                        must_capture=False,
                    )
                ],
            ),
            CLOSING,
        ]
    ),
    # ── Block II · runs before generation, never as prose in a prompt ──
    # One worked entry so the gate is exercised rather than stubbed. The
    # proving utterance is what generates the fixture in test_safety.py.
    red_flags=[SELF_HARM],
    # ── Blocks III & IV ──
    urgent=UrgentEscalation(rota=["on_call_clinician", "duty_gp"], timeout_minutes=15),
    soft_review=[],
    # ── Block V · a matrix, not a list ──
    tools=[
        ToolSpec(
            name="update_intake",
            description=(
                "Record what the patient said for the field currently being asked about. "
                "Call this as soon as they have answered, using their own words."
            ),
            allowed_states=["s1.q1", "close.q1"],
            max_attempts_per_turn=2,
        )
    ],
    # ── Block VI · the last thing to run ──
    report=ReportGuidance(
        fields=["day_mood", "anything_else"],
        rules=[
            "Quote the patient's own words where they are clear.",
            "Say plainly which questions were not asked.",
        ],
    ),
)


# ─────────────────────────────────────────────────────────────────────────────


PREOP_CHECK_V1 = ProtocolVersion(
    id="proto_preop_check_v1",
    label="Pre-op check v1",
    frozen=True,
    clinician=Clinician(
        name="Sister J. Okoro",
        initials="JO",
        practice="Springfield Day Surgery Unit",
        context="a quick check before your operation on Thursday",
    ),
    # ── Block I ──
    #
    # Two authored sections, five authored questions, all of them must-capture,
    # plus `CLOSING` — which the unit does not author. Questions s2.q1
    # and s2.q2 are reminders wearing a question mark: the point is that the
    # instruction is said out loud and the answer written down, not that
    # anything is evaluated.
    script=InterviewScript(
        sections=[
            Section(
                id="s1",
                title="Getting there",
                questions=[
                    Question(
                        id="q1",
                        ask=(
                            "You're booked for Thursday the fourth, arriving at seven. "
                            "Are you still able to come?"
                        ),
                        field_key="attendance",
                        label="Able to attend",
                        # Authored intent. Nothing reads `capture` yet, so this
                        # records the patient's words like every other field.
                        capture=EnumCapture(values=["confirmed", "at_risk", "cannot_attend"]),
                        if_unclear="one follow-up, then record what they said in their words",
                        must_capture=True,
                    ),
                    Question(
                        id="q2",
                        ask=(
                            "You'll need an adult to take you home and stay overnight. "
                            "Have you got someone?"
                        ),
                        field_key="escort_home",
                        label="Escort home",
                        capture=TextCapture(),
                        if_unclear="ask who it is; a name or a relationship is enough",
                        must_capture=True,
                    ),
                ],
            ),
            Section(
                id="s2",
                title="Before you come in",
                questions=[
                    Question(
                        id="q1",
                        ask=(
                            "Nothing to eat from midnight, water until six, nothing after. "
                            "Is that clear?"
                        ),
                        field_key="fasting_ack",
                        label="Fasting understood",
                        capture=BooleanCapture(),
                        if_unclear="say the times once more, then record what they said",
                        must_capture=True,
                    ),
                    Question(
                        id="q2",
                        ask="You were asked to stop your apixaban on Monday. Were you able to?",
                        field_key="meds_stopped",
                        label="Anticoagulant stopped",
                        capture=EnumCapture(values=["stopped", "still_taking", "unsure"]),
                        if_unclear="ask when the last dose was",
                        must_capture=True,
                    ),
                    Question(
                        id="q3",
                        ask=(
                            "Has anything changed with your health since your pre-op "
                            "appointment — cough, cold, temperature, new medicines?"
                        ),
                        field_key="health_change",
                        label="Change in health",
                        capture=TextCapture(),
                        if_unclear="one follow-up, then record what they said in their words",
                        must_capture=True,
                    ),
                ],
            ),
            CLOSING,
        ]
    ),
    # ── Block II ──
    #
    # Administrative, not diagnostic: none of these is a clinical judgement.
    # The unit decides; we route. All four actions appear, so the ranking in
    # `SEVERITY` decides a turn that hits more than one.
    #
    # Patterns are matched after `safety.normalise`, which replaces punctuation
    # with spaces — so "a and e" catches "A and E" but *not* "A&E", which
    # normalises to "a e". Spell it out or add the collapsed form; do not
    # assume the ampersand survives.
    red_flags=[
        SELF_HARM,
        RedFlag(
            id="rf_fitness_change",
            label="Change in fitness for anaesthetic",
            patterns=[
                "cough",
                "chesty",
                "cold",
                "temperature",
                "flu",
                "covid",
                "antibiotics",
                "been in hospital",
                "a and e",
                "new tablets",
            ],
            action="urgent_escalate",
            proving_utterance="the GP started me on antibiotics on Monday",
        ),
        RedFlag(
            id="rf_anticoagulant_taken",
            label="Anticoagulant not stopped as instructed",
            patterns=[
                "still taking",
                "didn't stop",
                "carried on taking",
                "took it this morning",
                "forgot to stop",
            ],
            action="urgent_escalate",
            proving_utterance="I'm still taking the apixaban",
        ),
        RedFlag(
            id="yf_attendance_risk",
            label="Attendance in doubt",
            patterns=[
                "can't make",
                "cannot make",
                "rearrange",
                "reschedule",
                "move it",
                "might not",
                "away that week",
            ],
            action="soft_review",
            proving_utterance="I might have to rearrange",
        ),
        RedFlag(
            id="yf_no_escort",
            label="No adult escort home",
            patterns=[
                "no one",
                "nobody",
                "live alone",
                "on my own",
                "get a taxi",
                "get the bus",
            ],
            action="soft_review",
            proving_utterance="there's nobody who can pick me up",
        ),
        RedFlag(
            id="nf_anxiety",
            label="Anxiety about the anaesthetic",
            patterns=["nervous", "scared", "worried about the anaesthetic", "dreading"],
            action="note_only",
            proving_utterance="I'm a bit nervous about the anaesthetic",
        ),
    ],
    # ── Blocks III & IV ──
    #
    # The decision owed is proceed / move / cancel, by a human, before the unit
    # closes — so the timeout is the working day, not fifteen minutes.
    urgent=UrgentEscalation(
        rota=["day_surgery_coordinator", "on_call_anaesthetist"], timeout_minutes=120
    ),
    soft_review=[],
    # ── Block V ──
    tools=[
        ToolSpec(
            name="update_intake",
            description=(
                "Record what the patient said for the field currently being asked about. "
                "Call this as soon as they have answered, using their own words."
            ),
            allowed_states=["s1.q1", "s1.q2", "s2.q1", "s2.q2", "s2.q3", "close.q1"],
            max_attempts_per_turn=2,
        )
    ],
    # ── Block VI ──
    report=ReportGuidance(
        fields=[
            "attendance",
            "escort_home",
            "fasting_ack",
            "meds_stopped",
            "health_change",
            "anything_else",
        ],
        rules=[
            "Quote the patient's own words where they are clear.",
            "Say plainly which questions were not asked.",
            "This is an administrative check. Do not offer a clinical impression.",
        ],
    ),
)


# ─── The short one ───────────────────────────────────────────────────────────

PREOP_SHORT_V1 = ProtocolVersion(
    id="proto_preop_short_v1",
    label="Pre-op check (short) v1",
    frozen=True,
    clinician=PREOP_CHECK_V1.clinician,
    # Two questions lifted verbatim from `PREOP_CHECK_V1` — the same ids, field
    # keys, labels and capture types, so a value captured here means exactly
    # what the same key means there. A shortened *copy* rather than a trimmed
    # original: `config.protocols` is immutable so that a captured field can
    # always be read back against the question that produced it, and editing
    # the real protocol would break that for every row already written under it.
    #
    # These two and not another two: one per section, so the section transition
    # is still exercised, and `meds_stopped` is the question the escalation
    # flag hangs off — a short interview that cannot escalate would be a
    # smoke test of the wrong thing.
    script=InterviewScript(
        sections=[
            Section(
                id="s1",
                title="Getting there",
                questions=[PREOP_CHECK_V1.script.sections[0].questions[0]],
            ),
            Section(
                id="s2",
                title="Before you come in",
                questions=[PREOP_CHECK_V1.script.sections[1].questions[1]],
            ),
            CLOSING,
        ]
    ),
    # Every flag that can fire on what these two questions invite. The others
    # are dropped because nothing here asks about fasting or health changes,
    # not to make the interview shorter.
    red_flags=[
        SELF_HARM,
        next(f for f in PREOP_CHECK_V1.red_flags if f.id == "rf_anticoagulant_taken"),
        next(f for f in PREOP_CHECK_V1.red_flags if f.id == "yf_attendance_risk"),
    ],
    urgent=PREOP_CHECK_V1.urgent,
    soft_review=[],
    tools=[
        ToolSpec(
            name="update_intake",
            description=PREOP_CHECK_V1.tools[0].description,
            allowed_states=["s1.q1", "s2.q2", "close.q1"],
            max_attempts_per_turn=2,
        )
    ],
    report=ReportGuidance(
        fields=["attendance", "meds_stopped", "anything_else"],
        rules=PREOP_CHECK_V1.report.rules,
    ),
)


# ─── The current generation ──────────────────────────────────────────────────
#
# v1 authored four levels; there are three — critical (`end_call`), urgent
# (`urgent_escalate`) and flagged (`soft_review`). A call with no flag is review
# ready, which is what `note_only` meant, so nothing is authored at that level
# any more.
#
# Published as a new id rather than edited in place. `config.protocols` is
# append-only and `db.seed_protocols()` inserts `on conflict do nothing`, so an
# edit to v1 would reach no database that has already booted — and every hit in
# `transcript.events` resolves its label and its action against the version its
# interview pinned, which is what lets a call filed last month keep meaning what
# it meant.
#
# For the same reason **nothing is renamed**. A flag id is an opaque key the
# record has already filed; the level is the `action`, never the `rf_` / `yf_`
# prefix, which is left over from an earlier vocabulary. The two flags whose
# level changed are `model_copy`s of v1's, so their ids, labels and patterns are
# byte-identical and only the action moved.

#: New in v2, and it has to exist: moving those two out of `urgent_escalate`
#: empties the level, and a level nothing can raise is a claim the system does
#: not keep. Nothing in v1 caught new pain where they are about to operate,
#: which is what `timeout_minutes = 120` was always the clock for.
#:
#: No prefix: the old ids keep theirs because renaming is churn against a filed
#: key, and a new one does not adopt a dead convention.
#:
#: Every pattern is a phrase worth a look out of context. Never `pain`, `sore`
#: or `hurts` on their own — `safety.py` handles no negation, so *"no new pain
#: at all"* fires this, and the flag is only legitimate because ringing someone
#: who did not need it is an acceptable answer to that.
SURGICAL_SITE_CHANGE = RedFlag(
    id="surgical_site_change",
    label="New or worsening pain at the operation site",
    patterns=[
        "new pain",
        "getting worse",
        "worse than it was",
        "worse since",
        "red and hot",
        "hot to touch",
        "swollen up",
        "started swelling",
        "oozing",
        "weeping",
    ],
    action="urgent_escalate",
    proving_utterance="it's gone red and hot where they're operating, and it's getting worse",
)


def _v1_flag(flag_id: str) -> RedFlag:
    return next(f for f in PREOP_CHECK_V1.red_flags if f.id == flag_id)


#: Urgent to the *booking*, not to the patient: the unit decides proceed, move
#: or cancel. Neither is a clinical judgement this system is entitled to treat
#: as one, and every call produces them — which is what makes a band that is
#: usually absent worth reading.
_NOW_FLAGGED = [
    _v1_flag("rf_fitness_change").model_copy(update={"action": "soft_review"}),
    _v1_flag("rf_anticoagulant_taken").model_copy(update={"action": "soft_review"}),
]

# ─── Block I, second generation · the flag hangs off the question ────────────
#
# Every flag above is a `RedFlag`: a phrase, matched against the turn, before
# generation, knowing nothing about what was asked. That ordering is what makes
# it unbypassable, and it is also its ceiling. *"No."* to *are you still able to
# come?* is a cancellation with no phrase in it to match, so the gate reads it
# as an ordinary turn and the call carries on asking about fasting.
#
# So v2 authors flags on the questions as well. Two triggers, and the order
# between them is the whole point:
#
#   · `when_value` is a value the question's own `EnumCapture` already declares
#     — a table lookup, no model in it, no negation problem. Anything the enum
#     can say should be said this way.
#   · `when` is a sentence a model judges the answer against, for what the enum
#     cannot: metaphor, indirection, a hedge that is really a refusal.
#
# `attendance` carries both on its first flag and that is the shape to copy:
# the lookup is the floor, the judgement is the net under the phrasings nobody
# anticipated. Neither replaces the gate. The gate still runs on every turn,
# still before generation, and is still the only thing here that cannot be
# argued with.
#
# Ids are prefixed `qf_` and are unique against the red-flag ids of the same
# version, because a hit is filed by id and read back against the version the
# interview pinned. Nothing reuses a `RedFlag` id even where it means the same
# thing — `qf_meds_still_taking` and `rf_anticoagulant_taken` are the same
# concern reached two different ways, and a clinician reading the band is owed
# the difference.
#
# Everything except `flags` is v1's, verbatim: same ids, field keys, labels and
# capture types, so a value captured under v2 means what the same key means
# under v1. `test_protocol_preop.py` holds that.

_PREOP_V2_SCRIPT = InterviewScript(
    sections=[
        Section(
            id="s1",
            title="Getting there",
            questions=[
                Question(
                    id="q1",
                    ask=(
                        "You're booked for Thursday the fourth, arriving at seven. "
                        "Are you still able to come?"
                    ),
                    field_key="attendance",
                    label="Able to attend",
                    capture=EnumCapture(values=["confirmed", "at_risk", "cannot_attend"]),
                    if_unclear="one follow-up, then record what they said in their words",
                    must_capture=True,
                    flags=[
                        # The one flag on this protocol that stops the call, and
                        # the only one that is not a clinical matter at all. The
                        # rest of the script is about an operation that is going
                        # to happen; once it is not, fasting times and escorts
                        # are questions about nothing. Stopping is the honest
                        # thing to do and the sentence says what happens next.
                        QuestionFlag(
                            id="qf_attendance_cannot",
                            label="Patient cannot attend",
                            when_value="cannot_attend",
                            when=(
                                "the patient says they will not be there — a refusal, a "
                                "clash, a change of mind, anything that means the slot on "
                                "Thursday goes unused"
                            ),
                            action="end_call",
                            say=(
                                "I'm sorry to hear that. I'll stop here rather than take you "
                                "through the rest, and I'll make sure the unit contacts you "
                                "to rebook. Thank you for letting us know — goodbye."
                            ),
                            proving_answer="no, I can't make it",
                        ),
                        QuestionFlag(
                            id="qf_attendance_at_risk",
                            label="Attendance in doubt",
                            when_value="at_risk",
                            # Worded around `yf_attendance_risk`'s own patterns
                            # on purpose. A condition a model is asked to judge
                            # must not quote a phrase the gate matches
                            # independently, or the prompt starts to look like
                            # where Block II is enforced. `test_prompts.py`
                            # holds that, and caught this line.
                            when=(
                                "the patient expects to come but is not certain — travel, "
                                "work, childcare, a relative they are caring for, anything "
                                "they raise as a reason it may fall through"
                            ),
                            action="soft_review",
                            proving_answer="I think so, but I might have to move it",
                        ),
                    ],
                ),
                Question(
                    id="q2",
                    ask=(
                        "You'll need an adult to take you home and stay overnight. "
                        "Have you got someone?"
                    ),
                    field_key="escort_home",
                    label="Escort home",
                    capture=TextCapture(),
                    if_unclear="ask who it is; a name or a relationship is enough",
                    must_capture=True,
                    flags=[
                        # No enum to look up, so this one is judgement only —
                        # and it is the case the gate is worst at. `yf_no_escort`
                        # matches "no one", "nobody", "get a taxi"; it does not
                        # match "my sister will drop me off and head back", which
                        # is the same answer said helpfully.
                        QuestionFlag(
                            id="qf_escort_none",
                            label="No adult escort home",
                            when=(
                                "there is no adult who will both take them home and stay "
                                "overnight — a taxi, a bus, someone who can only drop them "
                                "off, or no arrangement at all"
                            ),
                            action="soft_review",
                            proving_answer="my sister can drop me off but she can't stop over",
                        )
                    ],
                ),
            ],
        ),
        Section(
            id="s2",
            title="Before you come in",
            questions=[
                Question(
                    id="q1",
                    ask=(
                        "Nothing to eat from midnight, water until six, nothing after. "
                        "Is that clear?"
                    ),
                    field_key="fasting_ack",
                    label="Fasting understood",
                    capture=BooleanCapture(),
                    if_unclear="say the times once more, then record what they said",
                    must_capture=True,
                    flags=[
                        # "Yes" is not the same as understood, which is the
                        # entire reason this is a judgement and not a boolean
                        # read straight off the capture type.
                        QuestionFlag(
                            id="qf_fasting_not_understood",
                            label="Fasting instructions not understood",
                            when=(
                                "the patient has not taken the times in, or says something "
                                "that means they will have eaten or drunk inside them — "
                                "including a question back that shows they have them wrong"
                            ),
                            action="soft_review",
                            proving_answer="so I can still have a cup of tea at seven then?",
                        )
                    ],
                ),
                Question(
                    id="q2",
                    ask="You were asked to stop your apixaban on Monday. Were you able to?",
                    field_key="meds_stopped",
                    label="Anticoagulant stopped",
                    capture=EnumCapture(values=["stopped", "still_taking", "unsure"]),
                    if_unclear="ask when the last dose was",
                    must_capture=True,
                    flags=[
                        # Urgent here, where the gate's `rf_anticoagulant_taken`
                        # is flagged. Not a contradiction: the gate matches
                        # "still taking" anywhere in the call, on any subject,
                        # which is why it was demoted. This fires only on the
                        # answer to the question that asked, so it means what it
                        # says and can carry the weight the gate's version
                        # cannot.
                        QuestionFlag(
                            id="qf_meds_still_taking",
                            label="Anticoagulant not stopped as instructed",
                            when_value="still_taking",
                            when=(
                                "the patient took a dose on or after Monday, or describes a "
                                "last dose later than Sunday night"
                            ),
                            action="urgent_escalate",
                            proving_answer="I took one this morning",
                        ),
                        # Authored a level below `still_taking`, which is a
                        # choice and not an obvious one: an unsure dose and a
                        # taken dose are the same question to the list. Raise it
                        # if the unit would rather be rung either way.
                        QuestionFlag(
                            id="qf_meds_unsure",
                            label="Unsure whether the anticoagulant was stopped",
                            when_value="unsure",
                            when="the patient cannot say whether or when they stopped",
                            action="soft_review",
                            proving_answer="I think so? I'd have to look in the box",
                        ),
                    ],
                ),
                Question(
                    id="q3",
                    ask=(
                        "Has anything changed with your health since your pre-op "
                        "appointment — cough, cold, temperature, new medicines?"
                    ),
                    field_key="health_change",
                    label="Change in health",
                    capture=TextCapture(),
                    if_unclear="one follow-up, then record what they said in their words",
                    must_capture=True,
                    flags=[
                        # Deliberately at the level v2 moved `rf_fitness_change`
                        # to. The question-level version is more precise, not
                        # more serious: what makes a change urgent is
                        # `SURGICAL_SITE_CHANGE`, and that is still the gate's.
                        QuestionFlag(
                            id="qf_health_change",
                            label="Change in health since pre-op assessment",
                            when=(
                                "anything has changed — an illness, a hospital visit, a new "
                                "medicine, a symptom they did not have at the pre-op "
                                "appointment. Not a change they explicitly rule out."
                            ),
                            action="soft_review",
                            proving_answer="I've had a chesty cough since the weekend",
                        )
                    ],
                ),
            ],
        ),
        # `CLOSING` unchanged and unflagged. What a patient raises on their own
        # turn is not an answer to a question, so there is no question for a
        # flag to hang off — the gate is what watches that turn, as it watches
        # every other.
        CLOSING,
    ]
)


PREOP_CHECK_V2 = PREOP_CHECK_V1.model_copy(
    update={
        "id": "proto_preop_check_v2",
        "label": "Pre-op check v2",
        # Block I moved too, for the first time. v1's questions carry no flags
        # and must not grow any: a question flag changes what a version does,
        # and v1 is what interviews already filed are read back against.
        "script": _PREOP_V2_SCRIPT,
        "red_flags": [
            SELF_HARM,
            SURGICAL_SITE_CHANGE,
            *_NOW_FLAGGED,
            _v1_flag("yf_attendance_risk"),
            _v1_flag("yf_no_escort"),
            # `nf_anxiety` is not here. Nothing was owed on it, which is what a
            # call with no flag already says. The sentence still reaches the
            # record through the field it was said in.
        ],
        # The rota is not the escalation route and never became one: a call-out
        # rings the clinician who owns the board, then the clinic's front desk,
        # which is one number belonging to the clinic rather than something a
        # protocol author picks. `timeout_minutes` stays — it is the deadline
        # the dashboard's escalation band draws.
        "urgent": UrgentEscalation(rota=[], timeout_minutes=120),
    }
)

#: The short one, on the same footing. It carries `SURGICAL_SITE_CHANGE` because
#: its whole purpose is a thirty-second run that can still escalate: once the
#: anticoagulant flag is flagged rather than urgent, `SELF_HARM` would be the
#: only escalation left, and that is not a flag anyone drives a smoke test
#: through. It fires on `CLOSING`'s open turn, which every script ends on.
#: The two questions, taken from v2 rather than v1 so they arrive with their
#: flags attached. Same ids, same field keys — a short run is a shorter script
#: and never a different one.
_PREOP_SHORT_V2_SCRIPT = InterviewScript(
    sections=[
        Section(
            id="s1",
            title="Getting there",
            questions=[_PREOP_V2_SCRIPT.sections[0].questions[0]],
        ),
        Section(
            id="s2",
            title="Before you come in",
            questions=[_PREOP_V2_SCRIPT.sections[1].questions[1]],
        ),
        CLOSING,
    ]
)

PREOP_SHORT_V2 = PREOP_SHORT_V1.model_copy(
    update={
        "id": "proto_preop_short_v2",
        "label": "Pre-op check (short) v2",
        "script": _PREOP_SHORT_V2_SCRIPT,
        "red_flags": [
            SELF_HARM,
            SURGICAL_SITE_CHANGE,
            next(f for f in PREOP_CHECK_V2.red_flags if f.id == "rf_anticoagulant_taken"),
            next(f for f in PREOP_CHECK_V2.red_flags if f.id == "yf_attendance_risk"),
        ],
        "urgent": PREOP_CHECK_V2.urgent,
    }
)


#: Everything ever published. The runtime and `db.seed_protocols()` read this,
#: because an interview pinned to a superseded version must still run and still
#: be readable — that is the whole of what append-only buys.
PROTOCOLS: dict[str, ProtocolVersion] = {
    WARMUP_V1.id: WARMUP_V1,
    PREOP_CHECK_V1.id: PREOP_CHECK_V1,
    PREOP_SHORT_V1.id: PREOP_SHORT_V1,
    PREOP_CHECK_V2.id: PREOP_CHECK_V2,
    PREOP_SHORT_V2.id: PREOP_SHORT_V2,
}

#: What may be dispatched *now* — the current generation. `GET /protocols` lists
#: this and `dispatch.py` validates against it, so nobody can queue a version
#: that has been superseded. Publishing retires a version for new calls and
#: never for old records, and those are two different sets.
OFFERED: dict[str, ProtocolVersion] = {
    WARMUP_V1.id: WARMUP_V1,
    PREOP_CHECK_V2.id: PREOP_CHECK_V2,
    PREOP_SHORT_V2.id: PREOP_SHORT_V2,
}
