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


PROTOCOLS: dict[str, ProtocolVersion] = {
    WARMUP_V1.id: WARMUP_V1,
    PREOP_CHECK_V1.id: PREOP_CHECK_V1,
    PREOP_SHORT_V1.id: PREOP_SHORT_V1,
}
