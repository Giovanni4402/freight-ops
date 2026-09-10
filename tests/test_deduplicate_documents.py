"""
The dedup query, run against a real Postgres.

I did not want to reimplement this one in Python to test it. DISTINCT ON, the
window function that has to run before it, and the ORDER BY that decides which
copy survives are all Postgres behaviour, and a reimplementation would be
testing my second version rather than the query that ships.

So the fixtures rebuild the exact situation that was miscounted for months:
the same document landing in two mailboxes on two different days, and two
genuinely different documents that happen to share a filename on one day. The
old grouping got both of them wrong, in opposite directions.

Skipped without DATABASE_URL so the rest of the suite still runs on a laptop.
CI brings up a Postgres service and these run there.
"""
import os
import re
from datetime import date, datetime
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="no DATABASE_URL, needs Postgres")

QUERY = (Path(__file__).resolve().parents[1] / "intake" / "deduplicate_documents.sql").read_text()

# :name -> %(name)s, leaving ::date alone. The lookbehind is the whole trick:
# in "::date" the second colon is preceded by a colon, so it never matches.
PLACEHOLDER = re.compile(r"(?<!:):(\w+)")


def to_pyformat(sql):
    """The application runs this through SQLAlchemy, which takes :name. psycopg
    wants %(name)s, and once you are in pyformat every literal percent has to
    be doubled or the ILIKE '%@' in the counterparty rule reads as a broken
    placeholder. Doubling first, then substituting, so the %( we introduce
    does not get doubled in turn."""
    return PLACEHOLDER.sub(r"%(\1)s", sql.replace("%", "%%"))


def run(cur, **params):
    cur.execute(to_pyformat(QUERY),
                {"own_domain": "example.com", "limit": 100, "offset": 0, **params})
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


SCHEMA = """
CREATE TABLE jobs      (id int PRIMARY KEY, reference text);
CREATE TABLE files     (id int PRIMARY KEY, job_id int REFERENCES jobs);
CREATE TABLE messages  (id int PRIMARY KEY, received_at timestamptz,
                        subject text, sender text, recipients text);
CREATE TABLE message_files (message_id int REFERENCES messages, file_id int REFERENCES files);
CREATE TABLE attachments (id int PRIMARY KEY, message_id int REFERENCES messages,
                          fingerprint text, name text, kind text,
                          draft_or_final text, document_date date, stored_path text);
"""


@pytest.fixture
def cur():
    with psycopg.connect(DSN, autocommit=False) as cn:
        with cn.cursor() as c:
            c.execute(SCHEMA)
            yield c
        cn.rollback()          # every test starts from nothing


def message(cur, mid, day, sender="agent@haulier.co.uk",
            recipients="ops@example.com", subject="Shipment"):
    cur.execute("INSERT INTO messages VALUES (%s,%s,%s,%s,%s)",
                (mid, datetime(2026, 8, day, 9, 0), subject, sender, recipients))


def attachment(cur, aid, mid, fingerprint, name, stored_path="/data/x", **over):
    row = {"kind": "invoice", "draft_or_final": "final", "document_date": None}
    row.update(over)
    cur.execute("INSERT INTO attachments VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (aid, mid, fingerprint, name, row["kind"], row["draft_or_final"],
                 row["document_date"], stored_path))


# ── the two ways the old grouping was wrong ─────────────────────────

def test_one_document_in_four_mailboxes_is_one_row(cur):
    """It re-encodes at every hop so the bytes differ, but the content hash
    does not."""
    for i in range(1, 5):
        message(cur, i, day=3)
        attachment(cur, 10 + i, i, "abc123", "invoice.pdf")

    rows = run(cur)
    assert len(rows) == 1
    assert rows[0]["copies"] == 4


def test_same_document_arriving_on_two_days_is_one_row(cur):
    message(cur, 1, day=3)
    message(cur, 2, day=5)
    attachment(cur, 10, 1, "abc123", "invoice.pdf")
    attachment(cur, 11, 2, "abc123", "invoice.pdf")

    rows = run(cur)
    assert len(rows) == 1
    assert rows[0]["copies"] == 2


def test_two_documents_sharing_a_name_on_one_day_are_two_rows(cur):
    """This is the direction that loses data. 178 documents were invisible
    because a namesake arrived the same day."""
    message(cur, 1, day=3)
    attachment(cur, 10, 1, "aaa", "invoice.pdf")
    attachment(cur, 11, 1, "bbb", "invoice.pdf")

    rows = run(cur)
    assert len(rows) == 2
    assert {r["copies"] for r in rows} == {1}


# ── which copy survives ─────────────────────────────────────────────

def test_the_copy_we_can_actually_open_wins(cur):
    """Some copies were ingested before we kept the file. A row that counts a
    document but cannot show it is worse than useless at the counter."""
    message(cur, 1, day=9)                    # newest, but no file kept
    message(cur, 2, day=3)
    attachment(cur, 10, 1, "abc123", "cmr.pdf", stored_path=None)
    attachment(cur, 11, 2, "abc123", "cmr.pdf", stored_path="/data/cmr.pdf")

    rows = run(cur)
    assert len(rows) == 1
    assert rows[0]["id"] == 11
    assert rows[0]["openable"] is True


def test_among_openable_copies_the_most_recent_wins(cur):
    message(cur, 1, day=3)
    message(cur, 2, day=9)
    attachment(cur, 10, 1, "abc123", "cmr.pdf")
    attachment(cur, 11, 2, "abc123", "cmr.pdf")

    assert run(cur)[0]["id"] == 11


# ── the fields the archive screen depends on ────────────────────────

def test_the_counterparty_is_the_recipient_when_we_are_the_sender(cur):
    """Otherwise half the archive is filed under our own domain."""
    message(cur, 1, day=3, sender="ops@example.com", recipients="mario@haulier.co.uk")
    attachment(cur, 10, 1, "abc", "quote.pdf")

    assert run(cur)[0]["counterparty"] == "haulier.co.uk"


def test_the_counterparty_is_the_sender_when_they_wrote_to_us(cur):
    message(cur, 1, day=3, sender="mario@haulier.co.uk", recipients="ops@example.com")
    attachment(cur, 10, 1, "abc", "quote.pdf")

    assert run(cur)[0]["counterparty"] == "haulier.co.uk"


def test_the_document_date_beats_the_day_it_arrived(cur):
    """An invoice dated the 1st that reaches us on the 20th belongs to the
    1st. Sorting the archive by arrival puts it in the wrong month."""
    message(cur, 1, day=20)
    attachment(cur, 10, 1, "abc", "invoice.pdf", document_date=date(2026, 8, 1))

    assert run(cur)[0]["happened_on"] == date(2026, 8, 1)


def test_it_falls_back_to_the_arrival_date(cur):
    message(cur, 1, day=20)
    attachment(cur, 10, 1, "abc", "invoice.pdf", document_date=None)

    assert run(cur)[0]["happened_on"] == date(2026, 8, 20)


def test_the_job_reference_comes_through_when_there_is_one(cur):
    cur.execute("INSERT INTO jobs VALUES (1, '842.113/26')")
    cur.execute("INSERT INTO files VALUES (1, 1)")
    message(cur, 1, day=3)
    cur.execute("INSERT INTO message_files VALUES (1, 1)")
    attachment(cur, 10, 1, "abc", "bl.pdf")

    assert run(cur)[0]["job_ref"] == "842.113/26"


def test_a_document_with_no_job_still_appears(cur):
    """The joins are LEFT for a reason: an unfiled document is the one most
    likely to be looked for."""
    message(cur, 1, day=3)
    attachment(cur, 10, 1, "abc", "orphan.pdf")

    rows = run(cur)
    assert len(rows) == 1
    assert rows[0]["job_ref"] is None


def test_paging_does_not_repeat_or_skip(cur):
    for i in range(1, 8):
        message(cur, i, day=i)
        attachment(cur, 10 + i, i, f"fp{i}", f"doc{i}.pdf")

    first = run(cur, limit=3, offset=0)
    second = run(cur, limit=3, offset=3)
    third = run(cur, limit=3, offset=6)
    ids = [r["id"] for r in first + second + third]
    assert len(ids) == 7
    assert len(set(ids)) == 7


def test_an_empty_archive_returns_nothing(cur):
    assert run(cur) == []
