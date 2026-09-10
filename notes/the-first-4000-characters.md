# The bug was in the first 4,000 characters

One of our operators told me a job was missing emails. The screen said 29. He
was sure there were more. He was right, but not for any of the reasons I
assumed, and the fix I nearly shipped would have been worse than the bug.

## What the system does

I run a freight forwarder and I built the software we work in. It reads four
shared mailboxes, pulls references out of every message, and groups them into
jobs. Job references have the shape `842.113/26` (the numbers here are made
up, the shape is ours). Any email carrying that
string belongs to that job. Everything else hangs off that one idea.

## The gap wasn't what it looked like

Job `842.113/26` showed 29 emails. A raw text search for `842.113` across the
mailboxes came back with 147 rows, which looked like a disaster until I
checked what they were. The same email lands in four colleagues' mailboxes,
so 147 rows are 59 actual messages. The engine deduplicates by `Message-ID`
and that part was working.

So: 59 messages talk about this job, 29 were linked to it.

## I guessed wrong

People write the reference three ways. `842.113/26`, `842.113-26`, sometimes
with a space in the middle. In our correspondence the dash form is much more
common, 81 occurrences against 13, so my first thought was that the extractor
only handled the slash.

I ran the extractor's own regex over the stored bodies of all 59. It matched
53. The six it didn't match were other things that happen to share three
digits with us: `842.113-C`, `842.113/24`, `842.113-25`. So the regex was
fine and I'd spent most of an afternoon on it.

## Where they actually went

The clustering engine loads mail like this:

```sql
SELECT m.id, m.subject, left(m.body, 4000) AS body, ...
```

That truncation is a memory decision, not laziness. Full bodies for the
current year are 616 MB. Cut at 4,000 they're 118 MB, and the engine holds
all of them in Python at once while it runs union-find over them.

This particular job is a long-running project. Its threads are reply chains
twelve to fifteen thousand characters deep, `R: R: R: Scheduling, Naples
Metro`, and the reference sits down in the quoted history where nobody's
retyped it. Of the thirty messages that weren't linked, 17 had the reference
past character 4,000. The worst one had it at 14,317 in a body of 14,900.

Seven more had the reference inside the first 4,000 and still no key. I never
worked out why. They're on a list.

Across the whole archive, 8,932 emails mention a job reference, the engine
saw 8,641, and 291 were invisible to it. Three percent, which sounds
tolerable until you notice they aren't spread evenly. They pile up on the
jobs with the longest threads, which are the ones people actually ask about.

## Raising the limit was the wrong fix

Twenty thousand characters would have caught nearly all of them. It would
also have taken the working set from 118 MB to 314 MB, held in memory every
night, on a box that's also running Postgres, MinIO and the API. For 3%.

What I'd missed is that Postgres already has all 616 MB and doesn't need to
send any of it anywhere to search it.

```sql
INSERT INTO email_keys (email_id, kind, value, confidence)
SELECT DISTINCT ON (t.msg, t.ref) t.id, 'job', t.ref, 99
  FROM (
    SELECT m.id, m.sent_at,
           coalesce(nullif(m.message_id,''), m.id::text) AS msg,
           '842.' || g[1] || '/' || g[2] AS ref
      FROM messages m,
           LATERAL regexp_matches(
               coalesce(m.subject,'') || ' ' || coalesce(m.body,''),
               '100[.\s]?([0-9]{3})\s*[/-]\s*(2[0-9])', 'g') AS g
     WHERE m.sent_at >= :from
  ) t
 ORDER BY t.msg, t.ref, ...
ON CONFLICT DO NOTHING
```

13.4 seconds over 45,637 emails, inside a nightly run that already takes
five. The 4,000-character cut stays exactly where it was, because clustering
doesn't need the tail of a thread. Only key extraction does. I'd been
treating those as the same job for two years.

## Then I made it worse

First version put 140 emails on the job. Up from 29 and completely wrong.

Four mailboxes, four rows per message, and I was writing the key on all four.
The count roughly tripled. I'd have swapped an undercount nobody could see
for an overcount everybody could, which is the worse one. Nobody files a
ticket about an email they never knew was missing.

The `DISTINCT ON (msg, ref)` above fixes it, but the `ORDER BY` underneath is
the part that matters: it prefers the copy that already carries the key. If
one does, the insert is a no-op and no second row appears. If none does, it
falls back to the copy the engine kept, so the new key lands on the same row
as all the other keys for that message.

## Where it ended up

52 emails on the job. I read all 52. Every one contains the reference, and 52
rows are 52 distinct messages.

Archive-wide, 7,643 key rows and 7,643 distinct message/reference pairs,
which is the invariant I wanted.

The thing I'd do differently is obvious in hindsight. The regex theory was
plausible and cost five minutes to disprove, and I tested it fourth.
