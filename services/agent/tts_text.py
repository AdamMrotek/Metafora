"""Cutting a streaming reply into chunks Orpheus will accept.

This is where most of the difference between "responsive" and "phone tree"
lives: TTS starts on sentence one rather than on the finished reply, so the
patient hears the first words while the model is still writing the rest.

Three files' worth of knowledge from the TypeScript agent land here, because
Pipecat's built-in aggregators do not cover any of it:

· **The 200-character cap.** Orpheus *rejects* longer input — it does not
  truncate — and no built-in aggregator enforces a hard character limit. It
  never cuts mid-word: a clinical sentence sliced through a drug name is worse
  than a slightly early break.
· **Request packing.** Every chunk is one TTS request and Groq's limits are on
  *requests*, not characters — 10 per minute on the free tier. Speaking a
  four-sentence reply as four requests burns the budget for no benefit, because
  only the first chunk is on the latency path. So the first sentence goes out
  the moment it is ready and everything after it rides along in as few requests
  as the cap allows.
· **Reasoning stripping.** `reasoning_effort`/`reasoning_format: hidden` keeps
  gpt-oss's chain of thought out of `content`; this is the belt to that braces.
  Reasoning spoken to a patient would be a clinical failure, not a cosmetic
  one, and a `<think>` opened in one delta and closed in another would survive
  a per-delta regex.

One invariant makes it fit Pipecat's interface: `flush()` may return only a
single aggregation, so pending text is never allowed to exceed the cap.
"""

import re
from collections.abc import AsyncIterator

from pipecat.utils.text.base_text_aggregator import (
    Aggregation,
    AggregationType,
    BaseTextAggregator,
)

from services.agent.config.tuning import TTS_MAX_CHARS

#: Terminators that end a spoken sentence.
TERMINATORS = frozenset({".", "!", "?", "\n"})

#: Abbreviations whose trailing full stop does not end a sentence. Without
#: this, "Dr. Hollis" becomes two TTS calls with an audible seam in the middle
#: of the clinician's name.
ABBREVIATIONS = frozenset(
    {"dr", "mr", "mrs", "ms", "prof", "st", "e.g", "i.e", "approx", "no"}
)

_CLOSERS = re.compile(r"[\"'”’)\]]")
_CLAUSE_BREAK = re.compile(r"[,;:—–]\s(?=[^,;:—–]*$)")
_WORD_BREAK = re.compile(r"\s(?=\S*$)")
_OPEN_TAG = re.compile(r"<(think|thinking|reasoning)>", re.I)
_CLOSE_TAG = re.compile(r"</(think|thinking|reasoning)>", re.I)


def last_break_before(text: str, limit: int) -> int:
    """Last point at or before `limit` that is safe to cut.

    A comma or clause break if there is one, otherwise a space. Falls back to a
    hard cut only if a single "word" is longer than the whole budget.
    """
    window = text[:limit]
    for pattern in (_CLAUSE_BREAK, _WORD_BREAK):
        match = pattern.search(window)
        if match:
            return match.end()
    return limit


def hard_wrap(text: str, limit: int = TTS_MAX_CHARS) -> list[str]:
    """Split an over-long chunk at word boundaries so nothing exceeds the cap."""
    if len(text) <= limit:
        return [text] if text else []

    out: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = last_break_before(rest, limit)
        head, rest = rest[:cut].strip(), rest[cut:].strip()
        if not head:
            break
        out.append(head)
    if rest:
        out.append(rest)
    return [c for c in out if c]


class _ReasoningFilter:
    """Strips `<think>` blocks from a *stream*, where tags split across deltas.

    Holds back any trailing fragment that could still become an opening tag, so
    a `<` arriving at the end of one delta is never spoken while we wait to see
    whether `think>` follows.
    """

    #: Longest tag we might be part-way through, so we know how much to hold.
    _MAX_TAG = len("</reasoning>")

    def __init__(self) -> None:
        self._buffer = ""
        self._inside = False

    def push(self, delta: str) -> str:
        self._buffer += delta
        out = ""
        while self._buffer:
            if self._inside:
                match = _CLOSE_TAG.search(self._buffer)
                if not match:
                    # Keep only enough to recognise a close tag spanning deltas.
                    self._buffer = self._buffer[-self._MAX_TAG :]
                    break
                self._buffer = self._buffer[match.end() :]
                self._inside = False
                continue

            match = _OPEN_TAG.search(self._buffer)
            if match:
                out += self._buffer[: match.start()]
                self._buffer = self._buffer[match.end() :]
                self._inside = True
                continue

            # No tag. Release everything except a possible partial tag tail.
            cut = self._buffer.rfind("<")
            if cut == -1 or len(self._buffer) - cut > self._MAX_TAG:
                out += self._buffer
                self._buffer = ""
            else:
                out += self._buffer[:cut]
                self._buffer = self._buffer[cut:]
            break
        return out

    def flush(self) -> str:
        """End of stream. An unterminated `<think>` drops its tail deliberately."""
        rest = "" if self._inside else self._buffer
        self._buffer = ""
        self._inside = False
        return rest


class OrpheusAggregator(BaseTextAggregator):
    """Sentence aggregation with a hard character cap and request packing."""

    def __init__(self, *, max_chars: int = TTS_MAX_CHARS, pack_after_first: bool = True) -> None:
        super().__init__(aggregation_type=AggregationType.SENTENCE)
        self._max_chars = max_chars
        self._pack_after_first = pack_after_first
        self._reasoning = _ReasoningFilter()
        self._raw = ""      # incoming, not yet resolved into a sentence
        self._pending = ""  # complete sentences held back for packing
        self._emitted = 0

    @property
    def text(self) -> Aggregation:
        return Aggregation(text=(self._pending + self._raw).strip(), type=AggregationType.SENTENCE)

    async def aggregate(self, text: str) -> AsyncIterator[Aggregation]:
        self._raw += self._reasoning.push(text)

        while True:
            cut = self._find_boundary()
            if cut == -1:
                break
            sentence, self._raw = self._raw[:cut].strip(), self._raw[cut:]
            for piece in hard_wrap(sentence, self._max_chars):
                for out in self._offer(piece):
                    yield out

        # Nothing terminated yet, but we are already past what Orpheus accepts:
        # break at the last safe word boundary rather than wait for a full stop.
        while len(self._raw) > self._max_chars:
            cut = last_break_before(self._raw, self._max_chars)
            piece, self._raw = self._raw[:cut].strip(), self._raw[cut:]
            if not piece:
                break
            for out in self._offer(piece):
                yield out

        # Invariant: `flush()` returns at most one aggregation, so pending text
        # plus whatever is still buffered must always fit in a single request.
        if self._pending and len(self._pending) + len(self._raw) > self._max_chars:
            yield self._emit(self._pending)
            self._pending = ""

    def _offer(self, piece: str) -> list[Aggregation]:
        """Apply the packing rule to one complete chunk."""
        if not self._pack_after_first:
            return [self._emit(piece)]

        # The first chunk is never held: it is the only one the patient is
        # waiting on, and holding it back to save a request is the wrong trade.
        if self._emitted == 0 and not self._pending:
            return [self._emit(piece)]

        joined = f"{self._pending} {piece}" if self._pending else piece
        if len(joined) > self._max_chars:
            out = [self._emit(self._pending)] if self._pending else []
            self._pending = piece
            return out

        self._pending = joined
        return []

    def _emit(self, text: str) -> Aggregation:
        self._emitted += 1
        return Aggregation(text=text.strip(), type=AggregationType.SENTENCE)

    def _find_boundary(self) -> int:
        """Index just past the next real sentence terminator, or -1."""
        for i, ch in enumerate(self._raw):
            if ch not in TERMINATORS:
                continue

            if ch == ".":
                prev = self._raw[i - 1] if i > 0 else ""
                nxt = self._raw[i + 1] if i + 1 < len(self._raw) else None
                # A decimal point ("5.5 mg") is not a boundary.
                if prev.isdigit() and nxt is not None and nxt.isdigit():
                    continue
                if self._ends_with_abbreviation(i):
                    continue
                # Mid-stream, a trailing "." with nothing after it may still
                # become "5.5" once the next delta lands. Wait one character.
                if nxt is None:
                    return -1

            # Absorb closing quotes and brackets so they stay with the sentence.
            end = i + 1
            while end < len(self._raw) and _CLOSERS.match(self._raw[end]):
                end += 1
            return end
        return -1

    def _ends_with_abbreviation(self, dot_index: int) -> bool:
        word = re.split(r"[\s(]", self._raw[:dot_index])[-1]
        # A lone capital is an initial, not a sentence end. Without this,
        # "Dr E. Hollis" is spoken as two requests with an audible seam through
        # the middle of the clinician's name.
        if len(word) == 1 and word.isupper() and word.isalpha():
            return True
        if word.lower() in ABBREVIATIONS:
            return True
        # A dot part-way through a dotted abbreviation. The TypeScript original
        # listed "e.g"/"i.e" but never reached them: for "e.g." the *first* dot
        # is tested with word == "e", which matched nothing, so it split before
        # the entry could apply. Deliberate deviation from a faithful port —
        # this is precisely the audible seam the abbreviation list exists to
        # prevent.
        return any(a.startswith(f"{word.lower()}.") for a in ABBREVIATIONS)

    async def flush(self) -> Aggregation | None:
        tail = self._reasoning.flush() + self._raw
        self._raw = ""
        combined = f"{self._pending} {tail.strip()}".strip() if self._pending else tail.strip()
        self._pending = ""
        return self._emit(combined) if combined else None

    async def handle_interruption(self) -> None:
        """Barge-in: the patient talked over us, so queued text is discarded.

        An aborted reply that stays in the record corrupts it, not just the
        experience — so nothing half-spoken is preserved here.
        """
        await self.reset()

    async def reset(self) -> None:
        self._reasoning = _ReasoningFilter()
        self._raw = ""
        self._pending = ""
        self._emitted = 0
