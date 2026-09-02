"""The published protocols, and the only ones the runtime knows about.

In production these are published by the agent studio and pinned by the
interview. For now they are frozen objects in a dict, with all six blocks
present, so the studio has somewhere to publish into and the runtime has
something real to compile.

Three are published, and all three are offered:

  · `WARMUP_V1` is what the dispatch queue runs by default — say hello, ask how
    the day is going. Deliberately trivial; the machinery around it is not.
  · `PREOP_CHECK_V2` is `docs/example-interview.md` as an object: two authored
    sections, five authored fields, flags at all three levels and on both nets.
  · `PREOP_SHORT_V2` is that script cut to two questions, for a thirty-second
    run that can still reach an escalation.

The v1 generation is gone. It was kept for one thing — that a hit filed against
a superseded version still resolves to the flag that raised it — and nothing has
been filed against it here, so `PROTOCOLS` and `OFFERED` are the same set today.
They diverge again the moment a version is genuinely superseded: publish under a
new id, leave the old one in `PROTOCOLS`, take it out of `OFFERED`. That is also
why nothing here is ever edited in place. `config.protocols` is append-only and
`db.seed_protocols()` inserts `on conflict do nothing`, so a change to a live id
reaches no database that has already booted; and the ids kept their `_v2` suffix
for the same reason — `proto_preop_check_v2` is what the seeded rows and the
demo already name, and renaming a key the record has filed buys nothing.

Two things every protocol carries without authoring them, and carries unchanged:
`SELF_HARM` at the front of the flags and `closing()` at the end of the script.
Both are decisions about how a call may treat a patient, which is not a
per-script choice — so the counts above are of what the author wrote, and every
protocol compiles to one more state and one more field than that.

── What can end a call ──

Nothing the model decides. There is no hangup tool; a call ends two ways, and
both are settled in this process:

  · **every field captured** — `machine.complete` goes true and
    `EndOfInterview` hangs up once the assistant has stopped speaking. The
    goodbye is the `message_next` of the tool call that recorded the last
    answer, which is why it cannot arrive after the line has dropped.
  · **a flag authored at `action="end_call"`** — the interview stops at that
    question, the model's `message_next` is withheld before it is synthesised
    (`next_message.speakable`), and the flag's own `say` is spoken verbatim in
    its place. What a patient hears when a call stops is the protocol's
    sentence, never a model's.

So the second kind is the only thing in this file that can cut an interview
short, and every one of them is here:

    proto_warmup_v1        rf_self_harm          gate · any turn
    proto_preop_check_v2   rf_self_harm          gate · any turn
                           qf_attendance_cannot  answer to `attendance`
                           qf_closing_concern    answer to `anything_else`
    proto_preop_short_v2   rf_self_harm          gate · any turn
                           qf_attendance_cannot  answer to `attendance`
                           qf_closing_concern    answer to `anything_else`

`test_flag_types.py` holds that table, and holds the rule under it: a flag that
stops the call carries a sentence, and a flag that does not carries none. A call
that hangs up without saying why has hung up on somebody; a sentence on a flag
that lets the conversation continue would race the model's own reply into the
same TTS.
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

# ─── what every protocol carries ─────────────────────────────────────────────

#: The one red flag that is not protocol-specific. Every protocol carries it,
#: and carries it *unchanged* — the sentence a patient hears when a call stops
#: for this reason is not something an author should be able to reword per
#: script.
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


def closing(*flags: QuestionFlag) -> Section:
    """The last question of every script: the patient's own turn.

    A *question* and not a line in the system prompt, because only the script
    advances the machine — a closing sentence the model was merely told to say
    would be unrecorded, absent from the notes card, and skippable on any turn
    the model decided the call was already over. `must_capture=False` for the
    same reason the warm-up is: "nothing, thanks" is an answer, but nobody is
    owed one.

    The flags are the caller's because what a patient raises here means
    different things to different scripts. A warm-up has nothing clinical to
    hear, so it passes none and the gate is the only thing watching that turn;
    a pre-op check the day before an operation has, so it passes one that stops
    the call and says what happens to what it just heard.
    """
    return Section(
        id="close",
        title="Anything else",
        questions=[
            Question(
                id="q1",
                ask="Before we finish, is there anything else you would like to talk about?",
                field_key="anything_else",
                label="Anything else raised",
                capture=TextCapture(),
                # Grammatically a yes/no question; actually the patient's one
                # open turn. "Yes" is them clearing their throat, and
                # `tools._is_thin` refuses it once so they are asked what it is.
                expects_content=True,
                if_unclear="accept whatever they say, including nothing",
                must_capture=False,
                flags=list(flags),
            )
        ],
    )


INTAKE = (
    "Record what the patient said for the field currently being asked about. "
    "Call this as soon as they have answered, using their own words."
)


def intake_tool(script: InterviewScript) -> ToolSpec:
    """The one tool, legal from every state this script compiles to.

    Still a matrix and not a list: `machine.authorise` checks `allowed_states`
    on every call, and a script with a question the model may not record would
    narrow this. Derived rather than restated because every protocol published
    so far allows all of them, and a hand-copied list that misses a state is a
    question whose answer cannot be recorded — which is a question the interview
    can never advance past.
    """
    return ToolSpec(
        name="update_intake",
        description=INTAKE,
        allowed_states=[
            f"{section.id}.{question.id}"
            for section in script.sections
            for question in section.questions
        ],
        max_attempts_per_turn=2,
    )


# ─── Warm-up ─────────────────────────────────────────────────────────────────

_WARMUP_SCRIPT = InterviewScript(
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
        # Unflagged. Nothing on this script is clinical, so there is no answer
        # here worth stopping a call over — `SELF_HARM` is, and it is the gate's.
        closing(),
    ]
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
    script=_WARMUP_SCRIPT,
    # ── Block II · runs before generation, never as prose in a prompt ──
    # One worked entry so the gate is exercised rather than stubbed. The
    # proving utterance is what generates the fixture in test_safety.py.
    red_flags=[SELF_HARM],
    # ── Blocks III & IV ──
    urgent=UrgentEscalation(rota=[], timeout_minutes=15),
    soft_review=[],
    # ── Block V · a matrix, not a list ──
    tools=[intake_tool(_WARMUP_SCRIPT)],
    # ── Block VI · the last thing to run ──
    report=ReportGuidance(
        fields=["day_mood", "anything_else"],
        rules=[
            "Quote the patient's own words where they are clear.",
            "Say plainly which questions were not asked.",
        ],
    ),
)


# ─── Pre-op check · Block II, the gate ───────────────────────────────────────
#
# Administrative, not diagnostic: none of these is a clinical judgement. The
# unit decides; we route.
#
# Patterns are matched after `safety.normalise`, which replaces punctuation with
# spaces — so "a and e" catches "A and E" but *not* "A&E", which normalises to
# "a e". Spell it out or add the collapsed form; do not assume the ampersand
# survives.
#
# Ids keep their `rf_` / `yf_` prefixes, which are left over from a four-level
# vocabulary this catalog no longer has. They are opaque keys the record has
# already filed, and the level is the `action` — never the prefix. Renaming one
# orphans every hit filed under it and buys nothing but a tidier grep.

#: The urgent one. Every pattern is a phrase worth a look out of context — never
#: `pain`, `sore` or `hurts` on their own, because `safety.py` handles no
#: negation, so *"no new pain at all"* fires this. The flag is legitimate only
#: because ringing someone who did not need it is an acceptable answer to that.
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

#: Urgent to the *booking*, not to the patient: the unit decides proceed, move
#: or cancel. All four of these are flagged rather than urgent because the gate
#: matches them anywhere in the call, on any subject — "still taking" is a
#: phrase about a hobby as easily as about apixaban. The question-level flags
#: below fire on the answer to the question that asked, so they can carry weight
#: these cannot.
FITNESS_CHANGE = RedFlag(
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
    action="soft_review",
    proving_utterance="the GP started me on antibiotics on Monday",
)

ANTICOAGULANT_TAKEN = RedFlag(
    id="rf_anticoagulant_taken",
    label="Anticoagulant not stopped as instructed",
    patterns=[
        "still taking",
        "didn't stop",
        "carried on taking",
        "took it this morning",
        "forgot to stop",
    ],
    action="soft_review",
    proving_utterance="I'm still taking the apixaban",
)

ATTENDANCE_RISK = RedFlag(
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
)

NO_ESCORT = RedFlag(
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
)


# ─── Pre-op check · Block I, and the flags that hang off a question ──────────
#
# Every flag above is a `RedFlag`: a phrase, matched against the turn, before
# generation, knowing nothing about what was asked. That ordering is what makes
# it unbypassable, and it is also its ceiling. *"No."* to *are you still able to
# come?* is a cancellation with no phrase in it to match, so the gate reads it
# as an ordinary turn and the call carries on asking about fasting.
#
# So the questions carry flags of their own. Two triggers, and the order between
# them is the whole point:
#
#   · `when_value` is a value the question's own `EnumCapture` already declares
#     — a table lookup, no model in it, no negation problem. Anything the enum
#     can say should be said this way.
#   · `when` is a sentence a model judges the answer against, for what the enum
#     cannot: metaphor, indirection, a hedge that is really a refusal.
#
# `attendance` carries both on its first flag and that is the shape to copy: the
# lookup is the floor, the judgement is the net under the phrasings nobody
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

#: What the patient raises on their own turn, at the end of a call about an
#: operation the day after tomorrow. `CLOSING` used to be unflagged on the
#: argument that an open turn is not an answer to a question — but it is the
#: answer to *this* question, and leaving it to the gate meant "I still feel
#: pain around my knee" matched no pattern, raised nothing, and reached no
#: clinician.
#:
#: Authored at `end_call`, which reads oddly for a question that ends the
#: interview anyway. It buys the two things `complete` does not: the sentence
#: the patient hears is the unit's rather than the model's, and the call is
#: filed under why it ended instead of merely that it did. The condition is
#: deliberately narrow — a question about parking is not this.
CLOSING_CONCERN = QuestionFlag(
    id="qf_closing_concern",
    label="Concern raised on the patient's own turn",
    when=(
        "the patient raises something a clinician would want to see before the "
        "operation — a symptom, something that hurts, a worry about the "
        "anaesthetic or the surgery itself. Not a question about parking, "
        "timings, transport or paperwork, and not a remark that rules something out"
    ),
    action="end_call",
    say=(
        "Thank you for telling me — that's exactly the sort of thing we need to "
        "know. I'll make sure it goes to the unit today and they'll come back to "
        "you before Thursday. Take care now, goodbye."
    ),
    proving_answer="I still feel pain around my knee",
)

_PREOP_SCRIPT = InterviewScript(
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
                        # The one flag mid-script that stops the call, and the
                        # only one that is not a clinical matter at all. The rest
                        # of the script is about an operation that is going to
                        # happen; once it is not, fasting times and escorts are
                        # questions about nothing. Stopping is the honest thing
                        # to do and the sentence says what happens next.
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
                            # where Block II is enforced. `test_prompts.py` holds
                            # that, and caught this line.
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
                        # No enum to look up, so this one is judgement only — and
                        # it is the case the gate is worst at. `yf_no_escort`
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
                        # "still taking" anywhere in the call, on any subject.
                        # This fires only on the answer to the question that
                        # asked, so it means what it says.
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
                    # Same trap as the closing question: "yes" is a true answer
                    # to the grammar and an empty one to the clinician, who gets
                    # a flag saying something changed and a value that does not
                    # say what.
                    expects_content=True,
                    if_unclear="one follow-up, then record what they said in their words",
                    must_capture=True,
                    flags=[
                        # Deliberately at the level the gate's `rf_fitness_change`
                        # sits. The question-level version is more precise, not
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
        closing(CLOSING_CONCERN),
    ]
)

PREOP_CHECK_V2 = ProtocolVersion(
    id="proto_preop_check_v2",
    label="Pre-op check v2",
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
    # plus the closing question the unit does not author. Questions s2.q1 and
    # s2.q2 are reminders wearing a question mark: the point is that the
    # instruction is said out loud and the answer written down, not that
    # anything is evaluated.
    script=_PREOP_SCRIPT,
    # ── Block II ──
    red_flags=[
        SELF_HARM,
        SURGICAL_SITE_CHANGE,
        FITNESS_CHANGE,
        ANTICOAGULANT_TAKEN,
        ATTENDANCE_RISK,
        NO_ESCORT,
    ],
    # ── Blocks III & IV ──
    #
    # The decision owed is proceed / move / cancel, by a human, before the unit
    # closes — so the timeout is the working day, not fifteen minutes. The rota
    # is empty and never filled: a call-out rings the clinician who owns the
    # board, then the clinic's front desk, which is one number belonging to the
    # clinic rather than something a protocol author picks. `timeout_minutes`
    # stays — it is the deadline the dashboard's escalation band draws.
    urgent=UrgentEscalation(rota=[], timeout_minutes=120),
    soft_review=[],
    # ── Block V ──
    tools=[intake_tool(_PREOP_SCRIPT)],
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
#
# Two questions lifted verbatim from `PREOP_CHECK_V2` — the same objects, so the
# ids, field keys, labels, capture types and flags are identical and a value
# captured here means exactly what the same key means there. A shortened *copy*
# rather than a trimmed original: a captured field has to be readable back
# against the question that produced it.
#
# These two and not another two: one per section, so the section transition is
# still exercised, and `meds_stopped` is the question the urgent question-flag
# hangs off — a short interview that cannot escalate would be a smoke test of
# the wrong thing. It carries `SURGICAL_SITE_CHANGE` for the same reason on the
# gate's side: it fires on the closing turn, which every script ends on.

_PREOP_SHORT_SCRIPT = InterviewScript(
    sections=[
        Section(
            id="s1",
            title="Getting there",
            questions=[_PREOP_SCRIPT.sections[0].questions[0]],
        ),
        Section(
            id="s2",
            title="Before you come in",
            questions=[_PREOP_SCRIPT.sections[1].questions[1]],
        ),
        closing(CLOSING_CONCERN),
    ]
)

PREOP_SHORT_V2 = ProtocolVersion(
    id="proto_preop_short_v2",
    label="Pre-op check (short) v2",
    frozen=True,
    clinician=PREOP_CHECK_V2.clinician,
    script=_PREOP_SHORT_SCRIPT,
    # Every gate flag that can fire on what these two questions invite, plus the
    # two that watch the closing turn. The others are dropped because nothing
    # here asks about fasting, escorts or health changes — not to make the
    # interview shorter.
    red_flags=[
        SELF_HARM,
        SURGICAL_SITE_CHANGE,
        ANTICOAGULANT_TAKEN,
        ATTENDANCE_RISK,
    ],
    urgent=PREOP_CHECK_V2.urgent,
    soft_review=[],
    tools=[intake_tool(_PREOP_SHORT_SCRIPT)],
    report=ReportGuidance(
        fields=["attendance", "meds_stopped", "anything_else"],
        rules=PREOP_CHECK_V2.report.rules,
    ),
)


#: Everything ever published. The runtime and `db.seed_protocols()` read this,
#: because an interview pinned to a superseded version must still run and still
#: be readable — that is the whole of what append-only buys.
PROTOCOLS: dict[str, ProtocolVersion] = {
    WARMUP_V1.id: WARMUP_V1,
    PREOP_CHECK_V2.id: PREOP_CHECK_V2,
    PREOP_SHORT_V2.id: PREOP_SHORT_V2,
}

#: What may be dispatched *now*. `GET /protocols` lists this and `dispatch.py`
#: validates against it, so nobody can queue a version that has been superseded.
#: Nothing has been, so it is `PROTOCOLS` — publishing the next generation
#: retires a version for new calls and never for old records, and those are two
#: different sets the moment there is a difference to hold.
OFFERED: dict[str, ProtocolVersion] = dict(PROTOCOLS)
