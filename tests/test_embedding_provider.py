"""
The provider choice and the arithmetic underneath it.

The test I care about most is test_two_models_never_compare. That bug does
not raise, does not log, and does not look wrong from the outside: results
keep arriving, ranked, plausible, and computed from the first 384 numbers of
a 1,536-number vector. Everything else here is ordinary.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "retrieval"))

from embedding_provider import (Budget, BudgetSpent, cosine, group_by_meeting,
                                nearest, pick_provider)


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


class FakeClient:
    """Stands in for httpx. Records what it was asked, answers what it was told."""

    def __init__(self, response=None, boom=None):
        self.response = response
        self.boom = boom
        self.calls = []

    async def post(self, url, **kw):
        self.calls.append((url, kw))
        if self.boom:
            raise self.boom
        return self.response


HOSTED, LOCAL = "text-embedding-3-small", "multilingual-e5-small"


# ── the arithmetic ──────────────────────────────────────────────────

def test_cosine_of_a_vector_with_itself_is_one():
    assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_orthogonal_is_zero():
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_opposite_is_minus_one():
    assert cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_a_zero_vector_does_not_divide_by_zero():
    assert cosine([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_two_models_never_compare():
    """1,536 numbers against 384. Python's zip stops at the shorter one and
    hands back a number that looks like every other number, so the whole
    index quietly becomes noise. Zero instead: a result that does not appear
    gets noticed, a wrong one does not."""
    hosted = [0.1] * 1536
    local = [0.1] * 384
    assert cosine(hosted, local) == 0.0


def test_a_mismatched_row_is_dropped_rather_than_ranked():
    rows = [{"id": "same", "embedding": [1.0, 0.0, 0.0]},
            {"id": "other-model", "embedding": [1.0, 0.0, 0.0, 0.0, 0.0]}]
    out = nearest([1.0, 0.0, 0.0], rows, floor=0.01)
    assert [r["id"] for r in out] == ["same"]


# ── retrieval ───────────────────────────────────────────────────────

def test_nearest_sorts_closest_first():
    rows = [{"id": "far", "embedding": [0.0, 1.0]},
            {"id": "near", "embedding": [1.0, 0.1]}]
    assert [r["id"] for r in nearest([1.0, 0.0], rows)] == ["near", "far"]


def test_nearest_respects_the_limit():
    rows = [{"id": i, "embedding": [1.0, i / 100]} for i in range(20)]
    assert len(nearest([1.0, 0.0], rows, limit=5)) == 5


def test_the_vector_is_not_handed_back_to_the_caller():
    """A thousand floats per row through the API for nothing."""
    rows = [{"id": "a", "embedding": [1.0, 0.0]}]
    assert "embedding" not in nearest([1.0, 0.0], rows)[0]


def test_a_vector_stored_as_json_text_is_read():
    """It comes out of a jsonb column, which is sometimes a list and
    sometimes the string that list was written as."""
    rows = [{"id": "a", "embedding": "[1.0, 0.0]"}]
    assert nearest([1.0, 0.0], rows)[0]["score"] == pytest.approx(1.0)


def test_unreadable_and_missing_vectors_are_skipped_not_crashed():
    rows = [{"id": "broken", "embedding": "not json"},
            {"id": "empty", "embedding": None},
            {"id": "fine", "embedding": [1.0, 0.0]}]
    assert [r["id"] for r in nearest([1.0, 0.0], rows)] == ["fine"]


# ── grouping ────────────────────────────────────────────────────────

def test_passages_from_one_meeting_are_one_result():
    """Otherwise the first page is the same meeting eight times and the
    person concludes the search is broken."""
    out = group_by_meeting([
        {"meeting_id": "m1", "title": "Contract", "text": "a", "score": 0.9},
        {"meeting_id": "m1", "title": "Contract", "text": "b", "score": 0.7},
        {"meeting_id": "m2", "title": "Site visit", "text": "c", "score": 0.8}])
    assert [r["meeting_id"] for r in out] == ["m1", "m2"]
    assert len(out[0]["passages"]) == 2


def test_a_meeting_scores_as_high_as_its_best_passage():
    out = group_by_meeting([
        {"meeting_id": "m1", "text": "a", "score": 0.4},
        {"meeting_id": "m1", "text": "b", "score": 0.95}])
    assert out[0]["score"] == 0.95
    assert out[0]["passages"][0]["text"] == "b"


# ── choosing the provider ───────────────────────────────────────────

async def test_no_key_means_local_and_no_warning():
    provider, model, warning = await pick_provider(FakeClient(), "", HOSTED, LOCAL)
    assert (provider, model, warning) == ("local", LOCAL, "")


async def test_a_working_key_means_hosted():
    client = FakeClient(FakeResponse(200, {"data": [{"embedding": [0.1]}]}))
    provider, model, warning = await pick_provider(client, "sk-x", HOSTED, LOCAL)
    assert (provider, model) == ("hosted", HOSTED)
    assert warning == ""


async def test_a_key_with_no_credit_falls_back_and_says_so():
    """A key can be present and have nothing behind it. There is no local way
    to know, which is why the run spends one token asking."""
    client = FakeClient(FakeResponse(429, {"error": {"code": "insufficient_quota"}}))
    provider, model, warning = await pick_provider(client, "sk-x", HOSTED, LOCAL)
    assert (provider, model) == ("local", LOCAL)
    assert "credit" in warning.lower()


async def test_any_other_failure_also_falls_back_and_says_so():
    client = FakeClient(FakeResponse(500, {"error": {"code": "server_error"}}))
    provider, model, warning = await pick_provider(client, "sk-x", HOSTED, LOCAL)
    assert provider == "local"
    assert "500" in warning


async def test_the_network_being_down_is_not_an_exception_to_the_caller():
    client = FakeClient(boom=OSError("connection refused"))
    provider, _, warning = await pick_provider(client, "sk-x", HOSTED, LOCAL)
    assert provider == "local"
    assert "unreachable" in warning


# ── the budget ──────────────────────────────────────────────────────

def test_the_budget_counts_what_the_api_reported():
    b = Budget(cap_usd=1.0, usd_per_million=0.02)
    b.add(1_000_000)
    assert b.spent == pytest.approx(0.02)


def test_the_budget_stops_the_run():
    b = Budget(cap_usd=0.01, usd_per_million=0.02)
    with pytest.raises(BudgetSpent):
        b.add(1_000_000)


def test_the_message_says_how_much_and_how_many():
    b = Budget(cap_usd=0.01, usd_per_million=0.02)
    try:
        b.add(1_000_000)
    except BudgetSpent as e:
        assert "1000000 tokens" in str(e)
