# freight-ops

[![tests](https://github.com/Giovanni4402/freight-ops/actions/workflows/ci.yml/badge.svg)](https://github.com/Giovanni4402/freight-ops/actions/workflows/ci.yml)

Parts of the system my company runs on. I work at a freight forwarder and I
built the software we use every day.

The problem it solves is narrow and, as far as I can tell, common to the whole
industry. Everything you need in order to work already exists, but it exists as
email. Twenty thousand messages a year across shared mailboxes, no ticket
system, no discipline about subject lines. To find out where a shipment was,
you asked the colleague who had handled it.

So the system reads the mailboxes, groups the messages into jobs, reads the
attachments, works out where every shipment is, and reconciles that against
what we have invoiced.

Some numbers from the running instance:

| | |
|---|---|
| Emails ingested | 100,911 |
| Attachments | 11,537 |
| Mailboxes read | 11 |
| Jobs assembled | 1,234 |
| Invoice lines tracked | 3,598 |

## What is in this repository

The full system is private, because it contains our customers, our rates and
our correspondence. These are the pieces that stand on their own and that I
would want to be judged on. They are the real code with the identifying parts
taken out, not rewrites for show.

**[`intake/cluster_threads.py`](intake/cluster_threads.py)** turns a pile of
mailboxes into jobs. Union-find over about 45,000 messages, three passes,
loosest last. No model, no API calls, runs in a couple of minutes. Writing the
passes was easy. Working out what stops each one from over-merging took weeks,
and that reasoning is what the comments are about.

**[`intake/deduplicate_documents.sql`](intake/deduplicate_documents.sql)** is a
one-query fix for a counting bug that had gone unnoticed. The archive grouped
attachments by filename and date, which was wrong in both directions at once:
it split one document across two rows when it arrived on two days, and it
merged two different documents into one when they shared a name. `invoice.pdf`
alone was thirteen attachments and five distinct documents. Every row already
carried a content hash and nobody had used it.

**[`extraction/ai_extraction.py`](extraction/ai_extraction.py)** is the only
place a language model is called. Everything a regular expression can do is
done by a regular expression, for free, every two hours. The model gets the
rest: a route written in prose in whichever language the counterparty felt
like, a vessel name, whether a shipment was cancelled or delivered. It has a
hard spend cap enforced on the token counts the API actually returns rather
than on an estimate, and a source hierarchy that stops it overwriting a fact a
human entered by hand.

**[`privacy/confidential_attachments.py`](privacy/confidential_attachments.py)**
keeps staff records out of an archive that indexes everything. Payslips travel
through the same mailboxes as bills of lading. My first attempt flagged them
and filtered them out of the queries, which fixed nothing: the file was still
on disk and the OCR text was still reachable by full-text search. The check
now runs before the payload is decoded, so the file is never written at all.

**[`state/shipment_state.py`](state/shipment_state.py)** and
**[`state/shipmentState.ts`](state/shipmentState.ts)** answer the only question
anybody asks all day. The hard part is not parsing email, it is that the
sources contradict each other constantly, and something has to decide which one
wins.

**[`notes/the-first-4000-characters.md`](notes/the-first-4000-characters.md)**
is a write-up of a bug an operator reported. The screen said a job had 29
emails and he was certain there were more. He was right, but not for any of the
reasons I assumed, and the fix I nearly shipped would have been worse than the
bug.

## Tests

```bash
pip install pytest "psycopg[binary]"
pytest
```

63 tests. Most of them are pinned behaviours rather than coverage: a guard
against over-merging is one line of code, and nothing in that line says which
mailbox disaster put it there. The tests say it.

The dedup query runs against a real Postgres, with fixtures that rebuild the
two failure modes it was written to fix. Without `DATABASE_URL` those thirteen
skip and the rest still run on a laptop. CI brings up Postgres 16 and runs the
lot on Python 3.11, 3.12 and 3.13.

Writing them was not free. `test_form_codes_survive_an_underscore` exists
because it failed the first time I ran it: `\bw-?2\b` does not match
`W-2_2025.pdf`, since `2` and `_` are both word characters and there is no
boundary between them. That is a tax form walking into the archive.

## How it is built

Python with FastAPI and async SQLAlchemy, Postgres, React, all of it in Docker
Compose on one box. The mailboxes are read strictly read-only: `BODY.PEEK`,
folders opened readonly, nothing marked as read, moved or deleted, and the
system never sends mail on anyone's behalf.

## Two things I would say in an interview

The classifier that labels documents used to be tuned by eye, on two or three
examples. It now has 67 hand-labelled documents and a score, currently 87.3%,
and the command exits non-zero below the threshold. That is how I found out
that one of my keyword rules was quietly stealing customs paperwork from
another category.

And the rule that keeps the rest honest: derived data never overwrites entered
data. Customer, route and dates guessed from correspondence are a hypothesis.
When a human has filled in the job record, the job record wins. It is the only
way I have found to let a system that guesses coexist with numbers that have to
be exact.

## Licence

MIT. See [LICENSE](LICENSE).
