"""
AI pass over new shipment correspondence.

I run a freight forwarder. A rules engine already reads our shared mailboxes
and pulls out everything that can be pulled out deterministically: job refs,
booking numbers, dates in known formats, travel states. That part is free and
runs every two hours.

This is the layer on top. It calls an LLM only for the things rules can't do:
a route written in prose in whatever language the counterparty felt like
using, a vessel name, whether a shipment was actually cancelled or actually
delivered. Only on threads that changed since the last pass.

Design constraints, all of which cost me money or trust before I wrote them
down:

  · hard spend cap, enforced on real tokens from msg.usage, not estimates
  · dry run by default, --apply to spend anything
  · cheap model, never the big one in bulk
  · API error means nothing gets written; next run picks it up
  · below the confidence threshold the answer never reaches the operational
    view. It's stored as an observation you can look at, not as fact.
  · the model writes at source level 5. Accounting is 1, documents are 2.
    It fills gaps, it never overwrites, and it never touches a record a
    human has verified.

That last one is the whole point. The model is good at reading email and bad
at knowing when it's wrong, so it gets to answer but not to decide.
"""
import os
import re
import sys

MODEL = "claude-haiku-4-5-20251001"
PRICE_IN, PRICE_OUT = 1.0 / 1_000_000, 5.0 / 1_000_000     # USD per token

APPLY = "--apply" in sys.argv
CAP = float(sys.argv[sys.argv.index("--cap") + 1]) if "--cap" in sys.argv else 0.50

# Below this, the answer stays an observation and never reaches the board.
# Better a visible gap than a wrong number nobody questions.
MIN_CONFIDENCE = 70

# Trimmed. The real one is ~90 lines of domain rules, each added after the
# model got a specific case wrong.
PROMPT = """You are the operations desk at a freight forwarder. Read this
shipment thread the way a colleague would. Answer with JSON only:

{"origin": "...|null", "destination": "...|null",
 "departed": "YYYY-MM-DD|null", "actually_departed": true/false,
 "eta": "YYYY-MM-DD|null", "actually_arrived": true/false,
 "carrier": "vessel, line or haulier|null",
 "phase": "to_load|late_loading|in_transit|late_arrival|delivered|cancelled|not_a_shipment",
 "confidence": 0-100,
 "why": "the sentence you got it from, max 20 words"}

How to read these threads, learned by getting it wrong:

1. The pickup address is in the FIRST emails, not the last. The opening
   request says "EXW Via X 25, 35030 Somewhere". By the end of the thread
   everyone is talking about bills of lading and invoices.

2. Watch for signatures. An address that appears in every email from one
   sender is their office, not a destination.

3. A quote request is not a shipment. If the client hasn't awarded the job
   there is nothing moving.
"""


def build_thread(rows) -> str:
    """First 3 and last 5 emails of the thread, plus any attachment text.

    The middle is almost always quoted replies of the two ends. Sending the
    whole thread tripled the token count and made the answers worse, not
    better: the model started quoting the quoted text back at me."""
    parts = []
    for r in rows:
        body = re.sub(r"\n{3,}", "\n", (r["body"] or "").strip())
        block = f"--- {r['sent_at']:%d/%m/%Y} from {r['sender']}\nSubject: {r['subject']}\n{body}"
        if r["attachment_text"]:
            block += f"\n[ATTACHMENTS]\n{r['attachment_text'][:1800]}"
        parts.append(block)
    return "\n\n".join(parts)[:9000]


def run(jobs, threads, db):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("no ANTHROPIC_API_KEY, not starting.")
        return

    # Estimate before spending. Refuse rather than truncate the batch: a run
    # that silently does half the work is worse than one that doesn't start.
    estimate = sum(len(threads[j["id"]]) / 3.5 + 400 for j in jobs) * PRICE_IN \
        + len(jobs) * 220 * PRICE_OUT
    print(f"{len(jobs)} threads, estimated ${estimate:.2f}, cap ${CAP:.2f}")
    if estimate > CAP:
        print("estimate over cap. Raise it deliberately or shrink the batch.")
        return
    if not APPLY:
        print("[dry run] no API calls. Re-run with --apply.")
        return

    from anthropic import Anthropic
    client = Anthropic(api_key=key)
    spent, results, errors = 0.0, [], 0

    for i, job in enumerate(jobs, 1):
        if spent >= CAP:
            print(f"cap reached (${spent:.3f}), stopping after {i - 1}")
            break
        try:
            msg = client.messages.create(
                model=MODEL, max_tokens=400, system=PROMPT,
                messages=[{"role": "user", "content": threads[job["id"]]}])
        except Exception as e:
            errors += 1
            print(f"  {job['code']}: {e}")
            continue

        # Real usage, not the estimate. The estimate was out by 30% on long
        # threads and that is how you find out you spent four times the cap.
        spent += msg.usage.input_tokens * PRICE_IN + msg.usage.output_tokens * PRICE_OUT

        text = "".join(b.text for b in msg.content if b.type == "text")
        found = re.search(r"\{.*\}", text, re.S)
        if not found:
            errors += 1
            continue
        results.append({**parse(found.group(0)), "_id": job["id"], "_code": job["code"]})

    print(f"{len(results)} read, {errors} errors, spent ${spent:.3f}")
    write(results, db)


def write(results, db):
    """Two writes per result, and they are not the same thing.

    Every answer is logged as an observation with its confidence and the
    sentence it came from, whether we trust it or not. That log is how I tell
    whether a prompt change made things better.

    Only the confident ones touch the operational record, only where it is
    still empty, and never on a job someone has verified by hand."""
    for r in results:
        confident = (r.get("confidence") or 0) >= MIN_CONFIDENCE

        for field in ("origin", "destination", "carrier", "phase"):
            value = r.get(field)
            if not value or value in ("null", "unknown"):
                continue
            db.execute("""
                INSERT INTO ai_observations
                       (entity, entity_id, field, value, source_level,
                        source, confidence, rationale)
                VALUES ('job', %s, %s, %s, 5, 'ai', %s, %s)""",
                (r["_id"], field, str(value)[:200],
                 r.get("confidence"), (r.get("why") or "")[:400]))

        db.execute("""
            UPDATE jobs SET
                origin      = CASE WHEN origin      IS NULL AND %(ok)s THEN %(o)s ELSE origin END,
                destination = CASE WHEN destination IS NULL AND %(ok)s THEN %(d)s ELSE destination END,
                carrier     = CASE WHEN carrier     IS NULL AND %(ok)s THEN %(c)s ELSE carrier END,
                route_source = COALESCE(route_source, CASE WHEN %(ok)s THEN 'ai' END),
                ai_seen_at = now()
            WHERE id = %(id)s AND NOT verified""",
            {"id": r["_id"], "o": r.get("origin"), "d": r.get("destination"),
             "c": r.get("carrier"), "ok": confident})
