"""
What must never be stored.

This is the one suite where a failure is not a bug report, it is an incident.
Everything else in this repository can be wrong for a week and the cost is
somebody asking a colleague where a document went. If this stops working,
payroll goes into a searchable archive.

So the cases are written from both ends: the things that must be caught, and
the operational documents that must keep coming through. The second half
matters as much as the first. A filter that holds back the customs paperwork
gets switched off within a month, and then it protects nothing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "privacy"))

from confidential_attachments import is_confidential

OURS = "administration@example.com"
THEIRS = "operations@haulier.co.uk"


# ── caught on the filename alone ────────────────────────────────────

def test_a_payslip_is_caught_whoever_sent_it():
    assert is_confidential("PAYSLIP 07-26 A.Rossi.pdf")


def test_the_separators_people_actually_use():
    for name in ("pay slip july.pdf", "pay_slip_july.pdf", "pay-slip-july.pdf",
                 "Payslips 8.26.pdf", "wage slip.pdf"):
        assert is_confidential(name), name


def test_year_end_tax_forms():
    for name in ("P60 2026.pdf", "p45 leaver.pdf", "W-2_2025.pdf",
                 "Form 16 FY26.pdf", "salary statement Q3.xlsx"):
        assert is_confidential(name), name


def test_form_codes_survive_an_underscore():
    """The underscore is what broke this the first time. \\b does not fire
    between 2 and _, because both are word characters, so W-2_2025.pdf went
    straight through."""
    for name in ("W-2_2025.pdf", "P60_2026.pdf", "p45_leaver.pdf"):
        assert is_confidential(name), name


def test_a_form_code_does_not_swallow_a_year():
    """W-2025 is a year in a filename, not a tax form."""
    assert not is_confidential("Booking W-2025 confirmation.pdf")


def test_attendance_and_timesheets():
    assert is_confidential("attendance sheet august.xlsx")
    assert is_confidential("timesheet_week32.csv")


# ── caught on the subject, but only when we wrote it ────────────────

def test_a_harmless_filename_inside_a_payroll_email_from_us():
    """The scan that arrives as document.pdf. The filename tells you nothing
    and the subject line tells you everything."""
    assert is_confidential("document.pdf", "Payslips August", OURS)


def test_the_same_subject_from_outside_is_left_alone():
    """A customer whose subject line says new hire is talking about their own
    company, and their attachment is almost certainly a shipping document."""
    assert not is_confidential("document.pdf", "New hire starting Monday", THEIRS)


def test_the_subject_rule_needs_a_sender():
    assert not is_confidential("document.pdf", "Payslips August", None)


def test_our_own_employment_paperwork():
    for subject in ("Employment contract signed", "Resignation letter",
                    "Sick leave certificate", "Attendance for the payroll consultant"):
        assert is_confidential("scan001.pdf", subject, OURS), subject


# ── and the operational mail keeps flowing ──────────────────────────

def test_ordinary_shipping_documents_pass():
    for name in ("Bill of Lading 842113.pdf", "arrival notice.pdf",
                 "customs declaration.pdf", "CMR signed.pdf",
                 "packing list rev2.xlsx", "invoice_2601570.pdf"):
        assert not is_confidential(name, "Shipment update", THEIRS), name


def test_a_contract_that_merely_mentions_pay_is_operational():
    """A haulage contract quotes the crew's rates. It is not a payslip. This
    is why the rule never reads the body or the extracted text."""
    assert not is_confidential("haulage contract rev3.pdf",
                               "Contract draft for review", THEIRS)


def test_subcontractor_payroll_filings_still_come_through():
    """They carry other people's staff data, and I decided to keep them: the
    customer's compliance portal will not accept a haulier without them, so
    holding them back stops the job. They are ours to protect, not to lose."""
    assert not is_confidential("LUL June 2026.pdf",
                               "Documents for the compliance portal", THEIRS)


def test_nothing_at_all_is_not_confidential():
    assert not is_confidential(None)
    assert not is_confidential("", None, None)


# ── case does not matter, because filenames are a free-for-all ──────

def test_shouting_and_whispering_are_the_same():
    assert is_confidential("PAYSLIP.PDF")
    assert is_confidential("payslip.pdf")
    assert is_confidential("document.pdf", "PAYROLL AUGUST", OURS)
