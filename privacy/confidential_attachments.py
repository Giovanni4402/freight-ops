"""
What must never reach the document archive.

Our system reads the company's shared mailboxes and files every attachment
it finds. That is the whole point: the signed delivery note, the customs
form, the bill of lading, all of it findable without asking the colleague
who happened to handle that shipment.

The problem is that "every attachment" includes the payslips. Our
administrator sends them out one email per person, through the same
mailboxes we read. They went in like anything else. For a few hours two
colleagues could each read the other's salary, because I had widened access
to the archive by looking at a menu definition instead of looking at what
was actually in there.

Two things I got wrong, in order:

FIRST TRY, WRONG. I added a `confidential` flag, set it on the rows, and
filtered them out of the queries. That is a curtain in front of an open
window. The PDF was still on disk. The OCR text was still in the database,
and full-text search reaches text you never render: the OCR of a payslip
contains the word "salary".

SECOND TRY. The check runs where the attachment is first encountered,
before the payload is decoded. The file is never downloaded, never goes
through OCR, never gets a row. "It must not be stored" and "it must not be
shown" are different requirements, and only the first one is worth
implementing.

The flag stayed, for what was already inside and for the code paths that
re-fetch known files from the mailbox. Two defences, not one.

HOW IT DECIDES. Filename and subject line. Never the body, never the
extracted text. A haulage contract quotes the crew's pay rates and is
entirely operational; a document does not become confidential because it
contains a word. It is confidential because of what it is.

The subject rule additionally requires that we wrote the email. A customer
whose subject line says "new hire" is talking about their own company. Our
payroll consultant, and our own internal mail, are talking about us.

AND IT ERRS ONE WAY. If an operational document gets held back, someone
notices within a day and asks for it. The other direction nobody notices.
"""
import re

# The filename alone is enough. Nobody names a delivery note "payslip".
FILENAME = re.compile(
    r"pay[\s_-]?slip|payroll|wage[\s_-]?slip|salary[\s_-]?statement|"
    r"\bp60\b|\bp45\b|\bw-?2\b|form[\s_-]?16|"
    r"severance|attendance[\s_-]?sheet|timesheet",
    re.I)

# Only trusted when the message came from us. See the note above.
SUBJECT_IF_OURS = re.compile(
    r"payslip|payroll|salary|salaries|wages|severance|"
    r"new\s+hire|onboarding|resignation|dismissal|termination|"
    r"sick\s+leave|maternity|paternity|injury\s+report|"
    r"employment\s+contract|payroll\s+consultant|"
    r"attendance|clock[\s_-]?in|hours\s+worked",
    re.I)

OUR_DOMAIN = re.compile(r"@example\.", re.I)

# Deliberately absent from FILENAME: the statutory payroll and social
# security filings that subcontractors send us. They carry other people's
# staff data, but the customer's compliance portal will not accept a
# haulier without them, so without those files the job does not move. They
# stay, in an archive only two roles can open. Ours are caught by the
# subject rule, which requires that we were the sender.


def is_confidential(filename: str | None,
                    subject: str | None = None,
                    sender: str | None = None) -> bool:
    """True if this attachment must not be stored at all."""
    if FILENAME.search(filename or ""):
        return True
    if OUR_DOMAIN.search(sender or "") and SUBJECT_IF_OURS.search(subject or ""):
        return True
    return False


# ── where it is called ──────────────────────────────────────────────
#
# Inside the loop that walks the MIME parts, positioned above the line
# that decodes the payload:
#
#     for part in message.walk():
#         name = decoded_filename(part)
#         if not name or not USEFUL_EXTENSION.search(name):
#             continue
#         if is_confidential(name, subject, sender):
#             held_back.append(name)
#             continue
#         raw = part.get_payload(decode=True)      # never reached
#         ...
#
# `held_back` is counted and printed per mailbox at the end of each run.
# If that number jumps, the rule has started catching things it should not,
# and I want to see it the same day rather than discover in six months that
# half the customs paperwork quietly stopped arriving.
