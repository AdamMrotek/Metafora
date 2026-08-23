"""Ported from `voice/sentences.test.ts` and `voice/reasoning.test.ts`.

Those implementations retire with `loop.ts`, but what they knew does not: the
Orpheus character cap is not a preference, and reasoning read aloud to a
patient is a clinical failure. The behaviour is re-asserted here against the
Pipecat aggregator that replaced them.
"""

import pytest

from services.agent.config.tuning import TTS_MAX_CHARS
from services.agent.tts_text import OrpheusAggregator, _ReasoningFilter, hard_wrap


async def collect(agg: OrpheusAggregator, *deltas: str) -> list[str]:
    out: list[str] = []
    for delta in deltas:
        async for a in agg.aggregate(delta):
            out.append(a.text)
    tail = await agg.flush()
    if tail:
        out.append(tail.text)
    return out


# ─── sentence boundaries ─────────────────────────────────────────────────────


async def test_splits_on_terminators():
    assert await collect(OrpheusAggregator(), "Hello there. How are you?") == [
        "Hello there.",
        "How are you?",
    ]


async def test_first_sentence_is_emitted_before_the_stream_ends():
    """The whole point: TTS starts on sentence one, not on the finished reply."""
    agg = OrpheusAggregator()
    first = [a.text async for a in agg.aggregate("Hello there. ")]
    assert first == ["Hello there."]


async def test_a_decimal_point_is_not_a_sentence_boundary():
    assert await collect(OrpheusAggregator(), "Take 5.5 mg each morning.") == [
        "Take 5.5 mg each morning."
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Dr. Hollis will see you.",
        "Prof. Adeyemi is away.",
        "Take it e.g. with food.",
    ],
)
async def test_abbreviations_do_not_split(text):
    assert await collect(OrpheusAggregator(), text) == [text]


async def test_a_lone_initial_does_not_split_the_clinicians_name():
    """"Dr E. Hollis" spoken as two requests has an audible seam mid-name."""
    assert await collect(OrpheusAggregator(), "Dr E. Hollis asked me to call.") == [
        "Dr E. Hollis asked me to call."
    ]


async def test_tags_split_across_deltas_still_resolve():
    assert await collect(OrpheusAggregator(), "Take 5", ".5 mg now.") == ["Take 5.5 mg now."]


async def test_closing_quotes_stay_with_their_sentence():
    assert await collect(OrpheusAggregator(), 'She said "fine." Then she left.') == [
        'She said "fine."',
        "Then she left.",
    ]


# ─── the Orpheus cap ─────────────────────────────────────────────────────────


async def test_no_chunk_ever_exceeds_the_cap():
    """Orpheus rejects longer input outright; it does not truncate."""
    long = " ".join(["the patient reported a persistent headache"] * 30)
    chunks = await collect(OrpheusAggregator(), long)
    assert chunks, "must produce something"
    assert all(len(c) <= TTS_MAX_CHARS for c in chunks), [len(c) for c in chunks]


async def test_never_cuts_mid_word():
    long = " ".join(["paracetamol"] * 60)
    for chunk in await collect(OrpheusAggregator(), long):
        assert "paracetamo" not in chunk.replace("paracetamol", ""), chunk


async def test_hard_wrap_prefers_a_clause_break():
    text = "a" * 150 + ", " + "b" * 100
    assert hard_wrap(text, 200)[0].endswith(",")


async def test_flush_returns_a_single_chunk_within_the_cap():
    """Pipecat's `flush()` may return only one aggregation, so pending text
    must never be allowed to grow past a single request."""
    agg = OrpheusAggregator()
    async for _ in agg.aggregate("One. " + "two three four five " * 30):
        pass
    tail = await agg.flush()
    assert tail is None or len(tail.text) <= TTS_MAX_CHARS


# ─── request packing ─────────────────────────────────────────────────────────


async def test_packs_later_sentences_to_spend_fewer_requests():
    """10 requests/minute on the free tier; four short sentences is one request
    after the first, not four."""
    chunks = await collect(OrpheusAggregator(), "One. Two. Three. Four.")
    assert chunks[0] == "One."          # never held back — it is on the latency path
    assert len(chunks) == 2, chunks     # the rest ride along together
    assert chunks[1] == "Two. Three. Four."


async def test_packing_can_be_disabled():
    chunks = await collect(OrpheusAggregator(pack_after_first=False), "One. Two. Three.")
    assert chunks == ["One.", "Two.", "Three."]


# ─── reasoning ───────────────────────────────────────────────────────────────


async def test_reasoning_blocks_are_never_spoken():
    chunks = await collect(
        OrpheusAggregator(), "<think>the patient sounds tired</think>Good to hear."
    )
    assert chunks == ["Good to hear."]


async def test_a_reasoning_tag_split_across_deltas_is_still_stripped():
    """A `<think>` opened in one delta and closed in another survives a
    per-delta regex — which is how it reached a patient's ear."""
    chunks = await collect(OrpheusAggregator(), "<thi", "nk>hidden</thi", "nk>Hello there.")
    assert chunks == ["Hello there."]
    assert not any("hidden" in c for c in chunks)


async def test_an_unterminated_reasoning_block_drops_its_tail():
    assert await collect(OrpheusAggregator(), "Fine. <think>still musing") == ["Fine."]


async def test_a_lone_angle_bracket_is_not_mistaken_for_a_tag():
    f = _ReasoningFilter()
    assert f.push("5 < 6 and that is fine. ") + f.flush() == "5 < 6 and that is fine. "


async def test_multiple_reasoning_blocks():
    chunks = await collect(
        OrpheusAggregator(), "<think>a</think>First. <think>b</think>Second."
    )
    assert chunks == ["First.", "Second."]


async def test_interruption_discards_queued_text():
    """An aborted reply that stays in the record corrupts it."""
    agg = OrpheusAggregator()
    async for _ in agg.aggregate("One. Two. Three"):
        pass
    await agg.handle_interruption()
    assert await agg.flush() is None
