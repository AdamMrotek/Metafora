"""The hardcoded ProtocolVersion.

In production this is published by the agent studio and pinned by the
interview. For now it is one frozen object with all six blocks present —
five of them near-empty, but *shaped*, so the studio has somewhere to
publish into and the runtime has something real to compile.

The script is deliberately trivial: say hello, ask how the day is going.
The machinery around it is not.
"""

from shared.contracts.models import (
    Clinician,
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
            )
        ]
    ),
    # ── Block II · runs before generation, never as prose in a prompt ──
    # One worked entry so the gate is exercised rather than stubbed. The
    # proving utterance is what generates the fixture in test_safety.py.
    red_flags=[
        RedFlag(
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
    ],
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
            allowed_states=["s1.q1"],
            max_attempts_per_turn=2,
        )
    ],
    # ── Block VI · the last thing to run ──
    report=ReportGuidance(
        fields=["day_mood"],
        rules=[
            "Quote the patient's own words where they are clear.",
            "Say plainly which questions were not asked.",
        ],
    ),
)

PROTOCOLS: dict[str, ProtocolVersion] = {WARMUP_V1.id: WARMUP_V1}
