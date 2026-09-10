"""
Cutting meeting transcripts into passages you can actually search.

What was there before. One vector per meeting, computed over the title, the
summary, and the first 6,000 characters of the transcript. Our transcripts
average 22,585 characters, so three quarters of what was said never reached
the index at all. And even if it had: a single vector for an hour of talking
does not resemble any of the sixty things discussed in that hour. The average
of sixty topics is not any of them.

What it does now. The transcript is cut into passages of roughly 1,200
characters, each with its own vector. You search passages, and what comes
back is the passage: the person reads the sentences where the thing was
actually said, instead of the title of a one-hour meeting they now have to
scrub through by hand.

Where the cut goes. On sentence boundaries, not on a character count. Whisper
on our recordings produces continuous prose with no line breaks and no
speaker labels, but it does punctuate. Cutting mid-sentence destroys exactly
the thing somebody would want to find.

Passages overlap by 200 characters. Without the overlap a sentence that
straddles two passages is retrievable from neither: the first one has it half
finished and the second one has no idea what is being discussed.

The long-sentence case is not theoretical. A six-minute stretch with no pause
comes out of Whisper as one sentence, and without the hard split that single
sentence becomes a passage the size of the whole meeting. My first version
did exactly that, and also emitted it twice, because the overlap step handed
the entire chunk back to itself and the loop had nothing left to make
progress with.
"""
import re

CHUNK = 1200                # characters, roughly 300 tokens
OVERLAP = 200
MIN_CHUNK = 120             # below this it is a tail, not a passage

# A full stop followed by whitespace and a capital or a digit. The
# abbreviations that show up in our transcripts (etc., no., approx.) are not
# followed by a capital, so they do not split.
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀÈÉÌÒÙ0-9])")


def _units(text: str, longest: int) -> list[tuple[int, str]]:
    """(offset, sentence) in reading order, nothing longer than `longest`."""
    out, cursor = [], 0
    for sentence in SENTENCE_END.split(text):
        if not sentence:
            continue
        at = text.find(sentence, cursor)
        if at < 0:
            at = cursor
        for k in range(0, len(sentence), longest):
            out.append((at + k, sentence[k:k + longest]))
        cursor = at + len(sentence)
    return out


def chunk(text: str, size: int = CHUNK, overlap: int = OVERLAP) -> list[dict]:
    """A transcript as overlapping passages.

    Returns [{ordinal, text, from_char, to_char}] in reading order. The
    offsets are what lets the interface jump to the point in the transcript
    rather than only showing the passage out of context.
    """
    text = (text or "").strip()
    if not text:
        return []

    units = _units(text, size)
    out, current = [], []

    def length(group):
        return sum(len(s) + 1 for _, s in group) - 1 if group else 0

    for unit in units:
        current.append(unit)
        if length(current) < size:
            continue
        out.append({"text": " ".join(s for _, s in current).strip(),
                    "from_char": current[0][0]})
        # Carry the last few sentences forward. Never all of them: if the
        # passage was a single sentence, handing it back would rewrite it
        # unchanged and the loop would sit there duplicating it.
        tail, taken = [], 0
        for x in reversed(current[1:]):
            if taken >= overlap:
                break
            tail.insert(0, x)
            taken += len(x[1]) + 1
        current = tail

    if current:
        last = " ".join(s for _, s in current).strip()
        # A few trailing words are not a passage; they belong to the one before
        if len(last) < MIN_CHUNK and out:
            out[-1]["text"] = (out[-1]["text"] + " " + last).strip()
        elif last:
            out.append({"text": last, "from_char": current[0][0]})

    for n, piece in enumerate(out):
        piece["ordinal"] = n
        piece["to_char"] = piece["from_char"] + len(piece["text"])
    return out
