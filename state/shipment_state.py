"""
Where is this shipment, and how do we know?

Context
-------
I run a freight forwarding company. We move roughly 200 shipments a year for
industrial clients, and the single question everyone asks all day is "where is
it?". The answer used to live in four mailboxes and in people's heads.

So I built a system that reads those mailboxes, groups the messages into jobs,
and works out where every shipment is. This function is the part that decides.

The hard part is not parsing email. The hard part is that the sources
contradict each other, constantly:

  - a Bill of Lading says the goods were loaded on the 12th
  - an email from the 14th says "we plan to load on Monday"
  - the accounting system says the job was closed and paid in full
  - and a reminder about an unpaid invoice, sent yesterday, looks to a
    naive parser exactly like fresh activity on a live shipment

A shipment state that flips because someone chased an invoice is worse than
useless: people stop trusting the screen, and then they stop looking at it.

The rule
--------
Read top to bottom. The first line that answers, wins. Proof lives at the top,
hints at the bottom. We never guess when a document above already says it.

Two things I got wrong before landing on this, both of which cost me trust
with my own team:

  1. Email used to outrank accounting. It shouldn't. Nobody closes a job and
     collects payment on goods that never arrived, so a closed and settled
     job beats any amount of recent chatter. Recent email on a closed job is
     almost always administration.

  2. A shipment with no pickup date used to be reported as "waiting to load".
     It isn't waiting for anything, because nothing has been scheduled.
     Saying so out loud is more useful than a tidy status that hides an
     empty record.

Every branch returns the reason along with the verdict. The UI always shows
it, so anyone can disagree with the machine and see exactly which line fired.
Returning the reason did more for adoption than any accuracy improvement.
"""
from datetime import date

# Travel states the parser can extract from email, ordered by how far along
# the journey they are. These are the values that actually occur in our
# corpus, not the ones I imagined would.
LOADED = ("loaded", "departed", "on_board", "berthed", "customs_cleared", "delayed")
ARRIVED = ("delivered", "picked_up_by_consignee")

# After this many days with nobody writing about a job, we stop pretending we
# know where the goods are. Two weeks was chosen by watching real threads: it
# is long enough to survive a holiday, short enough that a genuinely lost
# shipment surfaces before the client calls.
SILENCE_DAYS = 15


def shipment_state(row, today: date) -> tuple[str, str]:
    """Return (state, reason). The reason is not decoration: it is shown to
    the user, and it is how we debug the classifier in production."""

    # ── proof ────────────────────────────────────────────────────────
    # A signed delivery note is not an opinion. If we hold one, we are done.
    if row["proof_date"]:
        return "delivered", f"arrival document ({row['proof_kinds']}) dated {row['proof_date']}"
    if row["travel_state"] in ARRIVED:
        return "delivered", "the correspondence says delivered"

    # ── accounting outranks correspondence ───────────────────────────
    # Money moving in both directions is the strongest signal we have that a
    # job is finished, and it is immune to the parser mistaking an invoice
    # reminder for a shipping event.
    nothing_outstanding = (not (row["revenue_to_invoice"] or 0)
                           and not (row["cost_to_receive"] or 0))
    if row["job_status"] == "closed" and nothing_outstanding:
        return "settled", "job closed, fully invoiced and paid"

    # ── silence ──────────────────────────────────────────────────────
    # We would rather say "I don't know" than draw a shipment on a map in a
    # position we are guessing at.
    silent_for = (today - row["last_message"].date()).days if row["last_message"] else None
    if silent_for is not None and silent_for > SILENCE_DAYS:
        if not row["job_ref"]:
            return "archived", f"no job reference, nothing heard for {silent_for} days"
        last = f", last we heard: {row['travel_state']}" if row["travel_state"] else ""
        return "unclear", f"job open, nothing heard for {silent_for} days{last}"

    # An open job that has been collected and whose costs are paid is also
    # finished. The money does not move in both directions for goods that are
    # still sitting in a yard.
    if row["fully_settled"] and nothing_outstanding:
        return "settled", "collected and paid: the job is closed"

    # ── in motion ────────────────────────────────────────────────────
    if row["travel_state"] in LOADED:
        return "in_transit", f"the correspondence says {row['travel_state']}"

    # ── not yet moving: is it late? ──────────────────────────────────
    scheduled = row["pickup_date"] or row["goods_ready_date"] or row["departure_date"]
    if scheduled and scheduled < today:
        return "loading_overdue", f"loading due {scheduled}, no departure recorded"
    if scheduled:
        return "awaiting_loading", f"loading due {scheduled}"

    # A job opened this morning with nothing in it is not "scheduled for
    # loading". Nothing is scheduled. Say that.
    if not row["line_count"]:
        return "awaiting_loading", "job just opened, nothing filled in yet"
    return "awaiting_loading", "no loading date yet"
