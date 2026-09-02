"""The two system prompts.

Two passes, not one, and the *split* is the design. gpt-oss emits speech or a
tool call and never both — re-verified against the live API on 2026-08-23,
where it returned a tool call and empty `content` in 5 trials out of 5. So a
single pass holding the schema goes silent on exactly the turn the patient just
answered.

Which means only the capture pass may ever be told that tools exist. Telling
the speech pass to "call update_intake" while withholding the schema is an
instruction it can only obey by reading the call out loud — and it did, to a
patient. The recording instruction lives in `capture_prompt`, with the tools,
and nowhere else.

The safety break, the escalation routes and the tool matrix are enforced in
code, before and around generation; they are deliberately not restated here as
prose, because restating them would imply the model is what makes them hold.
"""

from shared.contracts.models import ProtocolVersion, QueuedInterview


def system_prompt(protocol: ProtocolVersion, interview: QueuedInterview) -> str:
    """The system prompt carries Block I and nothing else.

    Nothing per-patient beyond the first name enters it, so the cache prefix
    stays stable across every interview running this protocol.
    """
    questions = "\n".join(
        f"- {q.ask} (records: {q.field_key})"
        for s in protocol.script.sections
        for q in s.questions
    )

    return "\n".join(
        [
            f"You are a clinical intake assistant calling {interview.patient.first_name} "
            f"on behalf of {protocol.clinician.name} at {protocol.clinician.practice}.",
            f"This is {protocol.clinician.context}.",
            "",
            "You are speaking out loud on a phone call. Therefore:",
            "- Keep every reply to one or two short sentences. Never list, never enumerate.",
            "- Write words as they are said. No markdown, no bullet points, no emoji,"
            " no stage directions.",
            "- Sound like a person, not a form. Acknowledge what they said before moving on.",
            "- The patient can interrupt you at any time. If they do, follow them.",
            "",
            "The questions to get through, in order:",
            questions,
            "",
            "Ask them one at a time and in your own words. As soon as the patient has"
            " answered one,",
            "acknowledge it in a few words and ask the next. If an answer is unclear, ask"
            " once more;",
            "if it is still unclear, accept what they did say and move on.",
            "The last question on the list is the closing one. Ask it on its own and let"
            " them answer;",
            "the goodbye comes after what they say, never folded into the same breath as"
            " the question.",
            "If they raise something new there, take it in and thank them for it — you are"
            " not there to",
            "resolve it. Then say goodbye warmly and stop.",
            "",
            "You are not a clinician. Do not diagnose, do not advise, and do not"
            " interpret symptoms.",
            "If asked for medical advice, say the practice will go through it"
            " with them.",
        ]
    )


def capture_prompt(protocol: ProtocolVersion) -> str:
    """The capture pass runs against a prompt of its own.

    It has to, because only one of the two passes is given the tool schema. The
    speech pass is told to converse and never told the tools exist; telling it to
    "call update_intake" while withholding the schema is an instruction it can
    only obey by reading the call out loud, which is what it did — the syntax
    reached the patient's ear and the pass that could actually record the field
    was a different one.

    So the recording instruction lives here, with the tools, and nowhere else.
    Everything about *how to speak* is absent for the same reason: this pass is
    never heard, and its prose is discarded.
    """
    questions = [q for s in protocol.script.sections for q in s.questions]

    def described(q) -> str:
        line = f"- {q.field_key} — {q.ask}"
        if q.capture.type == "enum":
            line += f"\n  answer: one of {', '.join(q.capture.values)}"
        return line

    fields = "\n".join(described(q) for q in questions)

    #: The authored conditions, verbatim. This is the only prose in either
    #: prompt that a model is asked to *judge* rather than say, and it is here
    #: rather than in `system_prompt` for the same reason the tools are: the
    #: pass that speaks must never be told a concern exists, or it will
    #: acknowledge one out loud to the patient it was raised about.
    concerns = "\n".join(
        f"- {flag.id} — on {q.field_key}, when {flag.when}"
        for q in questions
        for flag in q.flags
        if flag.when
    )

    lines = [
        # Wrapped differently from the source line, this would be a
        # different prompt. The text is ported verbatim; the line length is
        # not ours to tidy.
        "You are recording a clinical intake call. You are not speaking to the patient — another",  # noqa: E501
        "pass is doing that, and anything you write as prose is discarded. Your only job is to",
        "write the record.",
        "",
        "The fields to record:",
        fields,
        "",
        "When the patient has answered one, call update_intake to record it, using their own",
        "words where you can. Record only what they actually said. If they have not answered a",
        "field yet, do not call update_intake for it, and never guess at a value.",
    ]

    if protocol.script.sections and any(q.capture.type == "enum" for q in questions):
        lines += [
            "",
            "Where a field lists answers above, also set `answer` to the one the patient's reply",
            "amounts to. `value` still carries their own words; `answer` is only which of the",
            "listed options that is. If their reply fits none of them, leave `answer` out.",
        ]

    if concerns:
        lines += [
            "",
            "Some answers raise a concern for the clinician. These are the only ones there are:",  # noqa: E501
            concerns,
            "",
            "Set `flag` to the id of a concern the answer raises, or to `none`. Judge the",
            "answer to the question being asked, not the words alone — a refusal, a hedge or a",
            "figure of speech counts if it means the condition. Only a concern listed against",
            "the field you are recording, and only one; if two fit, take the first. Say nothing",
            "of this to the patient — you are not the pass that speaks.",
        ]

    return "\n".join(lines)
