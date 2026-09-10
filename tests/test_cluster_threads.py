"""
The clustering rules, written down so they stay true.

Most of these exist because the pass got something wrong on real mail and I
had to add a guard. The guard is one line; the reason it is there is not
obvious from reading it. That is what a test is for here.

The invariant at the bottom is the one I care about most: every message ends
up in exactly one cluster. A message that falls out of the partition never
shows up on any screen, and nobody reports a missing email they cannot see.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "intake"))

from cluster_threads import MAX_PER_KEY, MAX_PER_SUBJECT, cluster


def mail(eid, message_id=None, in_reply_to=None, references=None, subject_key=""):
    return {"id": eid,
            "message_id": message_id or f"<{eid}@example.com>",
            "in_reply_to": in_reply_to,
            "references": references,
            "subject_key": subject_key}


def groups(result):
    """Clusters as a set of frozensets, so the root id does not matter."""
    return {frozenset(v) for v in result.values()}


def together(result, *ids):
    return any(set(ids) <= set(v) for v in result.values())


# ── pass 1: a shared reference ──────────────────────────────────────

def test_one_shared_reference_merges():
    emails = [mail("a"), mail("b")]
    keys = {"a": {("job", "842.113/26")}, "b": {("job", "842.113/26")}}
    assert together(cluster(emails, keys), "a", "b")


def test_different_references_stay_apart():
    emails = [mail("a"), mail("b")]
    keys = {"a": {("job", "842.113/26")}, "b": {("job", "842.114/26")}}
    assert not together(cluster(emails, keys), "a", "b")


def test_a_roundup_email_does_not_bridge_the_jobs_it_lists():
    """The duties-to-pay email that names eight jobs at once.

    Left alone it welds eight unrelated shipments into one cluster, which is
    how I found this. An email only merges on a reference type when it cites
    exactly one reference of that type.
    """
    emails = [mail("a"), mail("b"), mail("roundup")]
    keys = {"a": {("job", "842.113/26")},
            "b": {("job", "842.114/26")},
            "roundup": {("job", "842.113/26"), ("job", "842.114/26")}}
    out = cluster(emails, keys)
    assert not together(out, "a", "b")
    assert not together(out, "a", "roundup")


def test_a_roundup_still_merges_on_a_type_it_cites_only_once():
    """The rule is per type, not per email. An email listing four job numbers
    and one container number is still trustworthy about the container."""
    emails = [mail("a"), mail("roundup")]
    keys = {"a": {("container", "MSKU1234567")},
            "roundup": {("job", "842.113/26"), ("job", "842.114/26"),
                        ("container", "MSKU1234567")}}
    assert together(cluster(emails, keys), "a", "roundup")


def test_weak_reference_types_do_not_merge():
    """Clients reuse their own purchase order numbers across shipments."""
    emails = [mail("a"), mail("b")]
    keys = {"a": {("po", "4500123")}, "b": {("po", "4500123")}}
    assert not together(cluster(emails, keys), "a", "b")


def test_a_reference_in_too_many_emails_is_not_a_reference():
    """Past the cap it is a template footer or somebody's signature block."""
    n = MAX_PER_KEY + 5
    emails = [mail(f"e{i}") for i in range(n)]
    keys = {f"e{i}": {("ref", "SAME")} for i in range(n)}
    out = cluster(emails, keys)
    assert len(groups(out)) == n


# ── pass 2: the conversation headers ────────────────────────────────

def test_in_reply_to_merges():
    emails = [mail("a", message_id="<one@example.com>"),
              mail("b", in_reply_to="<one@example.com>")]
    assert together(cluster(emails, {}), "a", "b")


def test_references_header_with_several_ids_merges_all_of_them():
    emails = [mail("a", message_id="<one@example.com>"),
              mail("b", message_id="<two@example.com>"),
              mail("c", references="<one@example.com> <two@example.com>")]
    out = cluster(emails, {})
    assert together(out, "a", "b", "c")


def test_a_reply_to_an_unknown_message_merges_nothing():
    """Half our threads start outside our mailboxes."""
    emails = [mail("a"), mail("b", in_reply_to="<never-seen@elsewhere.com>")]
    assert not together(cluster(emails, {}), "a", "b")


# ── pass 3: the subject, which is the one that misfires ─────────────

def test_a_long_specific_subject_merges():
    emails = [mail("a", subject_key="rotterdam transformer crate"),
              mail("b", subject_key="rotterdam transformer crate")]
    assert together(cluster(emails, {}), "a", "b")


def test_a_generic_subject_merges_nothing():
    """Otherwise every buongiorno in the archive becomes one shipment."""
    emails = [mail("a", subject_key="documenti"), mail("b", subject_key="documenti")]
    assert not together(cluster(emails, {}), "a", "b")


def test_a_short_subject_merges_nothing():
    emails = [mail("a", subject_key="ddt 4/26"), mail("b", subject_key="ddt 4/26")]
    assert not together(cluster(emails, {}), "a", "b")


def test_a_subject_shared_by_too_many_emails_merges_nothing():
    n = MAX_PER_SUBJECT + 5
    emails = [mail(f"e{i}", subject_key="monthly shipping report") for i in range(n)]
    assert len(groups(cluster(emails, {}))) == n


def test_strong_only_skips_the_subject_pass():
    """What the nightly run uses when it wants precision over recall."""
    emails = [mail("a", subject_key="rotterdam transformer crate"),
              mail("b", subject_key="rotterdam transformer crate")]
    assert not together(cluster(emails, {}, strong_only=True), "a", "b")


# ── the passes compose ──────────────────────────────────────────────

def test_two_chains_join_through_a_shared_reference():
    """a-b linked by a reply, c-d by a subject, and b-c by a job number.
    Union-find is here so that comes out as one cluster and not three."""
    emails = [mail("a", message_id="<one@example.com>"),
              mail("b", in_reply_to="<one@example.com>"),
              mail("c", subject_key="genoa crane mobilisation"),
              mail("d", subject_key="genoa crane mobilisation")]
    keys = {"b": {("job", "842.113/26")}, "c": {("job", "842.113/26")}}
    out = cluster(emails, keys)
    assert groups(out) == {frozenset({"a", "b", "c", "d"})}


def test_every_email_lands_in_exactly_one_cluster():
    """The invariant. A message outside the partition is invisible, and
    nobody reports an email they cannot see."""
    emails = [mail(f"e{i}", subject_key="genoa crane mobilisation" if i % 3 else "")
              for i in range(30)]
    keys = {"e1": {("job", "842.113/26")}, "e2": {("job", "842.113/26")},
            "e7": {("booking", "BK-99")}, "e8": {("booking", "BK-99")}}
    out = cluster(emails, keys)
    landed = [e for v in out.values() for e in v]
    assert sorted(landed) == sorted(e["id"] for e in emails)
    assert len(landed) == len(set(landed))


def test_no_emails_at_all():
    assert cluster([], {}) == {}
