"""
Which model computes the vectors, and the trap underneath that question.

I wanted the hosted API when it works and my own model when it does not.
That sounds like a one-line fallback. It is not, and the reason is worth
writing down because nothing about it produces an error message.

ONE INDEX, ONE MODEL. The hosted embeddings are 1,536 numbers. The model I
run myself returns 384. Comparing them raises nothing: `zip` stops at the
shorter sequence and cosine hands back a number that looks like every other
number. Results keep arriving, ranked, plausible, and wrong. This is the
worst shape a bug can have, and a fallback that switches provider mid-run
builds exactly that index.

So the provider is chosen once, at the start of a run, and does not change
halfway. If the budget or the credit runs out mid-run the job stops rather
than switching: half an index that is internally consistent is worth more
than a complete one that has to be thrown away. Search then asks which model
built the index and queries with that one, whatever the configuration says.
And cosine returns zero on a length mismatch, because a result that does not
appear gets noticed and a wrong one does not.

PROBING IS THE ONLY WAY TO KNOW. A key can be present and have no credit
behind it. There is no local check for that, so the run starts by spending
one token on the word "test". Cheaper than finding out at the end of an
otherwise successful pass.

THE PREFIX. The e5 family wants "query: " in front of a question and
"passage: " in front of indexed text, and it was trained that way. Get it
wrong and nothing breaks, results just get quietly worse, which is why
`kind` is a required argument here instead of having a default.

SCORES DO NOT MEAN WHAT THEY LOOK LIKE. On same-language text e5 returns
values bunched between 0.82 and 0.86. The ordering is meaningful; the
absolute number is not. Do not put a relevance threshold on it.
"""
import json
import math

# Codes the hosted API returns when the account is out of money, as opposed
# to being rate limited, which is a different problem with a different fix.
NO_CREDIT = ("insufficient_quota", "credit_balance_exhausted",
             "billing_hard_limit_reached")


class BudgetSpent(Exception):
    """Not an error. This is the limit doing its job."""


class Budget:
    """Counts what the API says it counted, not what we guessed.

    Estimates are wrong in the comfortable direction and you find out how
    wrong at the end of the month.
    """

    def __init__(self, cap_usd: float, usd_per_million: float):
        self.cap = cap_usd
        self.usd_per_million = usd_per_million
        self.tokens = 0

    @property
    def spent(self) -> float:
        return self.tokens * self.usd_per_million / 1_000_000

    def add(self, tokens: int):
        self.tokens += tokens
        if self.spent > self.cap:
            raise BudgetSpent(f"{self.spent:.4f} of {self.cap:.2f} USD, "
                              f"{self.tokens} tokens")


async def pick_provider(client, api_key: str, hosted_model: str,
                        local_model: str) -> tuple[str, str, str]:
    """(provider, model, warning) decided once, by actually trying it.

    The warning is not for the log. It goes back to whoever asked, because
    an index that silently changed model is the thing you most want to know
    about and the thing you are least likely to notice.
    """
    if not api_key:
        return "local", local_model, ""
    try:
        r = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": hosted_model, "input": "test"}, timeout=30)
        if r.status_code == 200:
            return "hosted", hosted_model, ""
        code = ""
        try:
            code = r.json().get("error", {}).get("code", "") or ""
        except Exception:
            pass
        if r.status_code == 429 and code in NO_CREDIT:
            return ("local", local_model,
                    "hosted embeddings have no credit left: using the local "
                    "model. Top up and reindex to switch back.")
        return ("local", local_model,
                f"hosted embeddings answered {r.status_code} {code}: using the "
                f"local model.")
    except Exception as e:
        return ("local", local_model,
                f"hosted embeddings unreachable ({str(e)[:60]}): using the "
                f"local model.")


async def hosted_vectors(client, texts: list[str], api_key: str, model: str,
                         budget: Budget) -> list[list[float]]:
    """One call for many texts. One call per passage means 160 round trips to
    index seven meetings, and almost all of that is latency."""
    r = await client.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "input": texts})
    r.raise_for_status()
    body = r.json()
    budget.add(body.get("usage", {}).get("total_tokens", 0))
    return [x["embedding"] for x in sorted(body["data"], key=lambda x: x["index"])]


async def local_vectors(client, texts: list[str], kind: str,
                        url: str) -> list[list[float]]:
    """Vectors from the model we run ourselves. No budget: the only thing
    being spent is CPU, which means reindexing everything to try a change to
    the chunker is something you can just do rather than something you decide.

    `kind` is "query" or "passage" and has no default on purpose. See the
    note about prefixes at the top.
    """
    r = await client.post(f"{url}/vectors",
                          json={"texts": texts, "kind": kind}, timeout=180)
    r.raise_for_status()
    return r.json()["vectors"]


def cosine(a, b) -> float:
    # Two models give vectors of different lengths, and zip would stop at the
    # shorter one and return a plausible, false number.
    if len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if not na or not nb:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def nearest(query_vector, rows, limit: int = 8, floor: float = 0.0) -> list[dict]:
    """The closest passages, closest first.

    Brute force, in Python, over every row. At a few hundred passages this is
    milliseconds and there is no index to keep in step with the data. It holds
    to a few thousand; past that it needs a real vector index, and this
    comment is the reminder that the day comes.
    """
    out = []
    for row in rows:
        vector = row.get("embedding")
        if isinstance(vector, str):
            try:
                vector = json.loads(vector)
            except (TypeError, ValueError):
                continue
        if not vector:
            continue
        score = cosine(query_vector, vector)
        if score < floor:
            continue
        out.append({**{k: v for k, v in row.items() if k != "embedding"},
                    "score": round(score, 4)})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit]


def group_by_meeting(passages: list[dict]) -> list[dict]:
    """Eight passages from one meeting are one result, not eight.

    Keep the best score, list the passages underneath it. Without this the
    first page of results is one meeting eight times and the person concludes
    the search is broken.
    """
    seen: dict = {}
    for p in passages:
        key = str(p.get("meeting_id"))
        entry = seen.setdefault(key, {"meeting_id": key, "title": p.get("title"),
                                      "score": 0.0, "passages": []})
        entry["score"] = max(entry["score"], p["score"])
        entry["passages"].append({"ordinal": p.get("ordinal"), "text": p.get("text"),
                                  "from_char": p.get("from_char"), "score": p["score"]})
    out = list(seen.values())
    for entry in out:
        entry["passages"].sort(key=lambda x: x["score"], reverse=True)
    out.sort(key=lambda x: x["score"], reverse=True)
    return out
