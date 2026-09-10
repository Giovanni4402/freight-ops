"""
The precedence order, pinned.

Every rule in shipment_state is easy on its own. What is not obvious is the
order, and the order is the whole design: proof, then accounting, then
silence, then movement, then dates. Half these tests exist to stop somebody
moving a rule up because it looked more specific.

The last one is a promise to the user interface: there is always a reason,
and it is never empty. The screen shows it next to the state, which is how
an operator disagrees with the machine and how I debug it in production.
"""
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "state"))

from shipment_state import SILENCE_DAYS, shipment_state

TODAY = date(2026, 9, 10)


def job(**over):
    row = {"proof_date": None, "proof_kinds": None, "travel_state": None,
           "job_status": "open", "job_ref": "842.113/26", "fully_settled": False,
           "revenue_to_invoice": 0, "cost_to_receive": 0,
           "last_message": datetime(2026, 9, 9, 10, 0),
           "pickup_date": None, "goods_ready_date": None, "departure_date": None,
           "line_count": 3}
    row.update(over)
    return row


def state(**over):
    return shipment_state(job(**over), TODAY)[0]


# ── proof outranks everything ───────────────────────────────────────

def test_a_signed_arrival_document_ends_it():
    assert state(proof_date=date(2026, 8, 20), proof_kinds="POD") == "delivered"


def test_proof_wins_even_over_a_closed_and_paid_job():
    """Both say finished, but only one of them can name the document."""
    s, why = shipment_state(job(proof_date=date(2026, 8, 20), proof_kinds="POD",
                                job_status="closed", fully_settled=True), TODAY)
    assert s == "delivered"
    assert "POD" in why


def test_correspondence_saying_delivered_is_enough_without_a_document():
    assert state(travel_state="delivered") == "delivered"


# ── accounting outranks correspondence ──────────────────────────────

def test_a_closed_and_paid_job_beats_recent_shipping_chatter():
    """Nobody closes a job and settles it for goods that never arrived. The
    chatter on a closed job is an invoice reminder, not a departure."""
    assert state(job_status="closed", travel_state="loaded") == "settled"


def test_a_closed_job_with_money_still_out_is_not_settled():
    assert state(job_status="closed", travel_state="loaded",
                 revenue_to_invoice=1200) == "in_transit"


def test_a_closed_job_with_a_supplier_invoice_still_to_come_is_not_settled():
    assert state(job_status="closed", cost_to_receive=800,
                 travel_state="loaded") == "in_transit"


# ── silence outranks movement ───────────────────────────────────────

def test_a_long_silence_beats_the_last_known_movement():
    """Two weeks after the last email we stop drawing it on the map at a
    position we are guessing at."""
    quiet = datetime(2026, 8, 1, 9, 0)
    assert state(last_message=quiet, travel_state="loaded") == "unclear"


def test_the_last_known_state_is_still_reported_in_the_reason():
    quiet = datetime(2026, 8, 1, 9, 0)
    _, why = shipment_state(job(last_message=quiet, travel_state="loaded"), TODAY)
    assert "loaded" in why


def test_silence_without_a_job_reference_is_archived_not_unclear():
    """No reference means nobody is working it. Different problem, different
    word, and it keeps the operators' list of open questions honest."""
    quiet = datetime(2026, 8, 1, 9, 0)
    assert state(last_message=quiet, job_ref=None, travel_state="loaded") == "archived"


def test_the_silence_threshold_is_exclusive():
    """Exactly SILENCE_DAYS is still fine. Off by one here means a fortnightly
    reporting cycle flips every shipment to unclear the day before the call."""
    on_the_line = datetime.combine(TODAY - timedelta(days=SILENCE_DAYS), time(9, 0))
    assert state(last_message=on_the_line, travel_state="loaded") == "in_transit"
    a_day_more = datetime.combine(TODAY - timedelta(days=SILENCE_DAYS + 1), time(9, 0))
    assert state(last_message=a_day_more, travel_state="loaded") == "unclear"


def test_a_job_nobody_has_ever_written_about_is_not_treated_as_silent():
    assert state(last_message=None, travel_state="loaded") == "in_transit"


# ── in motion, and not yet moving ───────────────────────────────────

def test_collected_and_paid_closes_an_open_job():
    assert state(fully_settled=True) == "settled"


def test_the_correspondence_puts_it_in_transit():
    assert state(travel_state="berthed") == "in_transit"


def test_a_loading_date_in_the_past_with_no_departure_is_overdue():
    assert state(pickup_date=date(2026, 9, 1)) == "loading_overdue"


def test_a_loading_date_in_the_future_is_simply_awaited():
    assert state(pickup_date=date(2026, 9, 20)) == "awaiting_loading"


def test_goods_ready_stands_in_when_there_is_no_pickup_date():
    assert state(goods_ready_date=date(2026, 9, 1)) == "loading_overdue"


def test_an_empty_job_says_so_instead_of_inventing_a_schedule():
    """It used to report waiting to load. It is not waiting for anything:
    nothing has been scheduled, and the empty record is the actual news."""
    s, why = shipment_state(job(line_count=0), TODAY)
    assert s == "awaiting_loading"
    assert "nothing filled in" in why


# ── the promise to the interface ────────────────────────────────────

def test_there_is_always_a_reason():
    cases = [job(), job(proof_date=date(2026, 8, 1), proof_kinds="POD"),
             job(job_status="closed"), job(last_message=datetime(2026, 7, 1)),
             job(travel_state="loaded"), job(pickup_date=date(2026, 9, 1)),
             job(line_count=0), job(last_message=None)]
    for row in cases:
        s, why = shipment_state(row, TODAY)
        assert s and why and why.strip()
