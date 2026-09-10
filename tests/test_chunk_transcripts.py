"""
The chunker, including the two cases that broke it.

A transcript is the one input you cannot sanity-check by eye: it is forty
thousand characters of unpunctuated speech and every chunking bug looks
exactly like every other one from the outside. So the guarantees are written
down here instead: everything is covered, nothing is emitted twice, and no
passage is larger than it was asked to be.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "retrieval"))

from chunk_transcripts import CHUNK, MIN_CHUNK, OVERLAP, chunk


def prose(n):
    """Distinct sentences, so duplicates in the output mean a real bug and
    not just repeated input."""
    return " ".join(f"This is sentence number {i} and it says something." for i in range(n))


# ── the shape of the output ─────────────────────────────────────────

def test_nothing_in_nothing_out():
    assert chunk("") == []
    assert chunk(None) == []
    assert chunk("   ") == []


def test_a_short_transcript_is_one_passage():
    out = chunk("We agreed to ship on Monday.")
    assert len(out) == 1
    assert out[0]["ordinal"] == 0
    assert out[0]["from_char"] == 0


def test_ordinals_are_consecutive_from_zero():
    out = chunk(prose(200))
    assert [p["ordinal"] for p in out] == list(range(len(out)))


def test_offsets_describe_the_passage():
    for p in chunk(prose(200)):
        assert p["to_char"] == p["from_char"] + len(p["text"])
        assert p["from_char"] >= 0


def test_no_passage_is_empty():
    assert all(p["text"].strip() for p in chunk(prose(200)))


# ── the guarantees that matter ──────────────────────────────────────

def test_passages_are_not_duplicated():
    """The first version emitted the same passage twice whenever the overlap
    handed a whole chunk back to itself."""
    texts = [p["text"] for p in chunk(prose(300))]
    assert len(texts) == len(set(texts))


def test_consecutive_passages_overlap():
    """A sentence straddling two passages has to be findable from one of
    them. Without the overlap it is findable from neither."""
    out = chunk(prose(200))
    assert len(out) > 2
    for a, b in zip(out, out[1:]):
        assert set(a["text"].split()) & set(b["text"].split())


def test_every_sentence_survives_somewhere():
    """Coverage, the thing you cannot eyeball on forty thousand characters."""
    out = chunk(prose(200))
    joined = " ".join(p["text"] for p in out)
    for i in range(200):
        assert f"sentence number {i} " in joined + " ", i


def test_a_sentence_longer_than_the_chunk_is_split():
    """Six minutes of talking with no pause comes out of Whisper as one
    sentence. Without the hard split that sentence becomes a passage the size
    of the whole meeting."""
    out = chunk("word " * 2000)          # 10,000 characters, no full stop
    assert len(out) > 1
    assert max(len(p["text"]) for p in out) <= CHUNK


def test_no_punctuation_at_all_still_chunks():
    """Not checking for duplicates here: the input is one character repeated,
    so identical passages are the correct answer, not a bug. Distinct text is
    what test_passages_are_not_duplicated is for."""
    out = chunk("x" * 5000)
    assert len(out) > 1
    assert max(len(p["text"]) for p in out) <= CHUNK
    assert sum(len(p["text"]) for p in out) >= 5000 - len(out)


def test_a_trailing_scrap_joins_the_passage_before_it():
    """Four words on their own are not a passage, and as a passage they would
    be retrieved with no context around them."""
    out = chunk(prose(100) + " Right.")
    assert all(len(p["text"]) >= MIN_CHUNK for p in out)
    assert out[-1]["text"].endswith("Right.")


# ── the knobs ───────────────────────────────────────────────────────

def test_a_smaller_size_makes_more_passages():
    text = prose(200)
    assert len(chunk(text, size=400)) > len(chunk(text, size=1600))


def test_the_defaults_are_the_ones_documented():
    assert (CHUNK, OVERLAP, MIN_CHUNK) == (1200, 200, 120)
