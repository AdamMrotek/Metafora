"""The system prompt. One pass, which speaks *through* its tool call.

gpt-oss emits speech or a tool call and never both — re-verified against the
live API on 2026-08-23, where it returned a tool call and empty `content` in 5
trials out of 5. This used to be argued as a reason to split the turn across two
passes: one told how to talk and given no tools, one given the tools and never
heard. It was, and the split's cost is that the two cannot see each other. On
the closing question one of them recorded the last field and ended the call
while the other asked the patient what they wanted to say, into a line that was
already closing.

So the tool call carries the sentence instead. `update_intake` has a required
`message_next` argument — what to say out loud once this answer is recorded —
and `next_message.py` releases it after `tools.dispatch` has written the record
and ruled on it. The pass that records is the pass that speaks, and there is
nothing left for two answers to disagree about.

That is why this prompt both names the tool and says how to talk, which the
split forbade. The prohibition there was about a pass that is heard *directly*:
told about a tool it does not hold, it reads the call out loud, and it did, to a
patient. This pass is not heard at all. Only `message_next` is, lifted out of
the arguments by a processor that reads a field rather than a transcript, so the
syntax has nowhere to leak to.

What replaces the separation is one instruction: the pass that judges a concern
is now the pass that speaks, so it must be told never to mention one out loud.
`test_prompts.py` holds that line.

The safety break, the escalation routes and the tool matrix are enforced in
code, before and around generation; they are deliberately not restated here as
prose, because restating them would imply the model is what makes them hold.
"""

from shared.contracts.models import ProtocolVersion, QueuedInterview


def _questions(protocol: ProtocolVersion) -> list:
    return [q for s in protocol.script.sections for q in s.questions]


def _field_catalog(protocol: ProtocolVersion) -> str:
    """Every field, the closed answer set for the ones that declare one, and
    which of them want content rather than a yes.

    The last is rendered from `Question.expects_content` rather than written out
    per protocol, so the prompt and the refusal in `tools._is_thin` cannot come
    to disagree about which questions they are.
    """

    def described(q) -> str:
        line = f"- {q.field_key} — {q.ask}"
        if q.capture.type == "enum":
            line += f"\n  answer: one of {', '.join(q.capture.values)}"
        if q.expects_content:
            line += (
                "\n  wants what they have to say, not whether they have"
                " something: a bare yes is not an answer here"
            )
        return line

    return "\n".join(described(q) for q in _questions(protocol))


def _concern_catalog(protocol: ProtocolVersion) -> str:
    """The authored conditions, verbatim. The only prose in the prompt a model
    is asked to *judge* rather than say."""
    return "\n".join(
        f"- {flag.id} — on {q.field_key}, when {flag.when}"
        for q in _questions(protocol)
        for flag in q.flags
        if flag.when
    )


def system_prompt(protocol: ProtocolVersion, interview: QueuedInterview) -> str:
    """One pass that both speaks and records, by speaking through the tool.

    The rule that makes it work is the one the split could not state: what the
    patient hears next is an argument of the call that records what they just
    said. So the model cannot end the interview in one breath and ask a
    question in the other — there is only one breath, and the sentence in it
    was written knowing the field had landed.

    Nothing per-patient beyond the first name enters it, so the cache prefix
    stays stable across every interview running this protocol.
    """
    questions = _questions(protocol)
    asked = "\n".join(f"- {q.ask} (records: {q.field_key})" for q in questions)
    concerns = _concern_catalog(protocol)

    lines = [
        f"You are a clinical intake assistant calling {interview.patient.first_name} "
        f"on behalf of {protocol.clinician.name} at {protocol.clinician.practice}.",
        f"This is {protocol.clinician.context}.",
        "",
        "Everything you say is spoken out loud on a phone call. Therefore:",
        "- Keep every reply to one or two short sentences. Never list, never enumerate.",
        "- Write words as they are said. No markdown, no bullet points, no emoji,"
        " no stage directions.",
        "- Sound like a person, not a form. Acknowledge what they said before moving on.",
        "- The patient can interrupt you at any time. If they do, follow them.",
        "",
        "The questions to get through, in order:",
        asked,
        "",
        "Ask them one at a time and in your own words. If an answer is unclear, ask once"
        " more;",
        "if it is still unclear, accept what they did say and move on.",
        "",
        "── How to answer ──",
        "",
        "Every turn is one of two things, and never both.",
        "",
        "1. The patient has just answered one of the questions above. Call update_intake."
        " Record",
        "   their answer in `field` and `value`, using their own words where you can, and"
        " put what",
        "   you would have said next in `message_next`. That sentence is what the patient"
        " hears —",
        "   it is the only thing of yours they hear on a turn like this — so acknowledge"
        " their",
        "   answer in a few words and ask the next question, exactly as if you were"
        " speaking.",
        "",
        "2. The patient said something that is not an answer to record — a question back,"
        " a",
        "   clarification, a hesitation. Reply in plain text and call nothing. Then ask"
        " the",
        "   question again on the next turn.",
        "",
        "Record only what they actually said. If they have not answered a field, do not"
        " call",
        "update_intake for it, and never guess at a value.",
        "",
        "The last question on the list is the closing one. Ask it on its own and let them"
        " answer.",
        "If they raise something new there, take it in and thank them for it — you are not"
        " there",
        "to resolve it. The goodbye belongs in the `message_next` of the call that records"
        " that",
        "last answer, after what they said, never folded into the same breath as the"
        " question.",
        "",
        "You are not a clinician. Do not diagnose, do not advise, and do not interpret"
        " symptoms.",
        "If asked for medical advice, say the practice will go through it with them.",
        "",
        "The fields to record:",
        _field_catalog(protocol),
    ]

    if any(q.expects_content for q in questions):
        lines += [
            "",
            "Some fields above are marked as wanting content. Those questions are"
            " phrased as",
            'yes/no and are not: "yes" means the patient has something to tell you,'
            " and you have",
            "not been told it yet. That is case 2 — do not call update_intake, just"
            " ask them what",
            "it is, and record what they say on the turn after. If you ask and they"
            " still give you",
            "nothing, record what they did say and move on.",
        ]

    if any(q.capture.type == "enum" for q in questions):
        lines += [
            "",
            "Where a field lists answers above, also set `answer` to the one the patient's"
            " reply",
            "amounts to. `value` still carries their own words; `answer` is only which of"
            " the",
            "listed options that is. If their reply fits none of them, leave `answer` out.",
        ]

    if concerns:
        lines += [
            "",
            "Some answers raise a concern for the clinician. These are the only ones there"
            " are:",
            concerns,
            "",
            "Set `flag` to the id of a concern the answer raises, or to `none`. Judge the",
            "answer to the question being asked, not the words alone — a refusal, a hedge"
            " or a",
            "figure of speech counts if it means the condition. Only a concern listed"
            " against",
            "the field you are recording, and only one; if two fit, take the first.",
            "",
            "Never say anything about a concern in `message_next`. Write that sentence as"
            " though",
            "you had raised nothing: acknowledge the answer warmly and carry on. If a"
            " concern is",
            "serious enough to stop the call, this system stops it and says its own"
            " sentence —",
            "yours is discarded — so a `message_next` that tries to close the call itself"
            " would",
            "only ever be the wrong one.",
        ]

    return "\n".join(lines)
