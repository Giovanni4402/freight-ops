"""
Turns a pile of mailboxes into jobs.

Freight forwarding. Every shipment lives across dozens of emails in four
shared mailboxes, with no ticket system and no discipline: people reply,
forward, change the subject, start a new thread halfway through. Nothing in
the data says which messages belong together.

Union-find over ~45k messages, three passes, loosest last. Comes out at
around 1,200 jobs. Runs in a couple of minutes on one box, no ML, no API
calls, costs nothing per run.

The passes are ordered by how much I trust them. What took the longest was
not writing them, it was working out what stops each one from over-merging.
Two clusters glued together is much worse than two that should have been
one: nobody notices a missing email, everybody notices a job that suddenly
contains someone else's shipment.
"""
import re
from collections import Counter, defaultdict

# Reference types strong enough to merge on their own. Job refs, RFQ numbers,
# booking numbers, container numbers. PO numbers are deliberately not here:
# clients reuse them across shipments.
STRONG = {"job", "rfq", "ref", "customs", "booking", "container", "at", "so"}

# Caps. If one reference shows up in 400 emails it isn't a reference to a
# shipment, it's a template footer or someone's signature.
MAX_PER_KEY = 120
MAX_PER_SUBJECT = 60
MIN_SUBJECT_LEN = 12

# Subjects that merge everything if you let them. Grew one entry at a time,
# each after finding a cluster that made no sense.
GENERIC_SUBJECTS = {
    "documenti", "documents", "fattura", "invoice", "ddt", "info",
    "aggiornamento", "update", "conferma", "confirmation", "buongiorno",
    "richiesta", "preventivo", "quotation", "urgente", "",
}


def _find(parent, x):
    root = x
    while parent[root] != root:
        root = parent[root]
    while parent[x] != root:              # path compression
        parent[x], x = root, parent[x]
    return root


def _union(parent, a, b):
    ra, rb = _find(parent, a), _find(parent, b)
    if ra != rb:
        parent[rb] = ra


def cluster(emails, keys_by_email, strong_only=False):
    """emails: [{id, message_id, in_reply_to, references, subject_key}]
       keys_by_email: {email_id: {(type, value), ...}}
       returns {root_id: [email_id, ...]}"""
    parent = {e["id"]: e["id"] for e in emails}
    ids = set(parent)

    # 1. shared reference.
    #
    # The catch: roundup emails. "Duties to pay" with eight job numbers in the
    # body, "3 Invoices #..." with three. Let those bridge and you glue half
    # the company into one cluster. So an email only merges on a reference
    # type when it cites exactly ONE reference of that type.
    by_key = defaultdict(list)
    for eid, keys in keys_by_email.items():
        if eid not in ids:
            continue
        seen = Counter(t for (t, _) in keys)
        for (kind, value) in keys:
            if kind in STRONG and seen[kind] == 1:
                by_key[(kind, value)].append(eid)

    for (kind, value), group in by_key.items():
        if len(group) > MAX_PER_KEY:
            continue
        for other in group[1:]:
            _union(parent, group[0], other)

    # 2. the conversation. In-Reply-To and References, when clients bother to
    # send them. Roughly two thirds do.
    by_msgid = {e["message_id"].strip(): e["id"] for e in emails if e["message_id"]}
    for e in emails:
        for header in (e["in_reply_to"], e["references"]):
            if not header:
                continue
            for mid in re.findall(r"<[^>]+>", header) or [header.strip()]:
                parent_email = by_msgid.get(mid.strip())
                if parent_email and parent_email != e["id"]:
                    _union(parent, parent_email, e["id"])

    if strong_only:
        return _components(parent, ids)

    # 3. same subject. Last resort, and the one that misfires. subject_key is
    # already stripped of Re:/Fwd:/R: and normalised. Skip anything short or
    # in the blocklist, otherwise every "Buongiorno" in the archive becomes
    # one job.
    by_subject = defaultdict(list)
    for e in emails:
        k = e["subject_key"]
        if len(k) >= MIN_SUBJECT_LEN and k not in GENERIC_SUBJECTS:
            by_subject[k].append(e["id"])

    for group in by_subject.values():
        if len(group) > MAX_PER_SUBJECT:
            continue
        for other in group[1:]:
            _union(parent, group[0], other)

    return _components(parent, ids)


def _components(parent, ids):
    out = defaultdict(list)
    for i in ids:
        out[_find(parent, i)].append(i)
    return out
