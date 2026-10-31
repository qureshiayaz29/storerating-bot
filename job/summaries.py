"""Phase 7 — Summaries. See PLAN.md → Summary, Budget guardrails.

New Reviews are filtered, ranked and capped, then summarised by Claude Haiku 5.5:
top 3 complaint and praise themes with a rough share each, plus a one-line
verdict. Scheduled work goes through the Batch API; Apps still waiting at the
deadline fall back to a standard call. Every call is checked against the
budget first and written to the Spend Ledger after.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import budget
from .db import Jsonb, JobRun, log_error
from .fetch import FetchOutcome
from .sources.base import Review

MODEL = budget.MODEL
MIN_NEW_REVIEWS = 5
MAX_PER_SIDE = 150
MAX_REVIEW_CHARS = 500
MAX_TOKENS = 1200  # JSON reply is ~300 tokens; headroom so it is never cut off
POLL_SECONDS = 60

SYSTEM = (
    "You summarise Google Play reviews for app developers. Be specific and concrete; "
    "name the feature or screen involved. Never include reviewer names, emails, phone numbers "
    "or other personal data. Paraphrase; do not quote whole reviews. Reply with JSON only."
)

PROMPT = """App: {name} ({package})
{context}
Below are new reviews since the last update, split into complaints (1–2★) and praise (4–5★).
Each line: [stars★, 👍thumbs-up] text

COMPLAINTS ({n_bad} reviews):
{bad}

PRAISE ({n_good} reviews):
{good}

Return JSON exactly in this shape:
{{"complaints": [{{"theme": "<4–6 words>", "share": <0–100, % of complaint reviews>, "example": "<short paraphrase, max 12 words>"}}],
  "praise": [{{"theme": "<4–6 words>", "share": <0–100, % of praise reviews>, "example": "<short paraphrase, max 12 words>"}}],
  "verdict": "<one line, max 90 characters, what changed and why it matters>"}}
Up to 3 items each, sorted by share, biggest first. Praise lists only things users like: never put a bug, complaint or feature request in praise.
Use an empty list when a side has no reviews."""

DAILY_CONTEXT = """Today: {today}
Complaint themes from recent days:
{history}
For each complaint add "status": "persisting" and "since": "<first date it appears above>" when it matches one of these themes, otherwise "status": "new" and "since": "{today}"."""


@dataclass
class Summary:
    complaints: list[dict]
    praise: list[dict]
    verdict: str


@dataclass
class PreparedRequest:
    package: str
    prompt: str
    est_cost: float


@dataclass
class SummaryRun:
    done: dict[str, Summary] = field(default_factory=dict)
    skipped_budget: list[str] = field(default_factory=list)
    skipped_few_reviews: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


# --- review selection ------------------------------------------------------------

_JUNK = re.compile(r"^[\W\d_]*$", re.UNICODE)  # emoji, punctuation, numbers only


def is_junk(text: str) -> bool:
    t = text.strip()
    return len(t.split()) < 3 or bool(_JUNK.match(t))


def select(reviews: list[Review]) -> tuple[list[Review], list[Review]]:
    """Drop junk and duplicates; rank by thumbs-up then recency; cap each side."""
    seen: set[str] = set()
    bad, good = [], []
    for r in reviews:
        key = " ".join(r.text.lower().split())
        if is_junk(r.text) or key in seen:
            continue
        seen.add(key)
        if r.stars <= 2:
            bad.append(r)
        elif r.stars >= 4:
            good.append(r)
    def rank(rs: list[Review]) -> list[Review]:
        # Most thumbs-up first; newest first among ties (ISO UTC strings sort by time).
        return sorted(rs, key=lambda r: (r.thumbs_up, r.created_at), reverse=True)[:MAX_PER_SIDE]

    return rank(bad), rank(good)


def _lines(rs: list[Review]) -> str:
    if not rs:
        return "(none)"
    return "\n".join(f"[{r.stars}★, 👍{r.thumbs_up}] {' '.join(r.text.split())[:MAX_REVIEW_CHARS]}" for r in rs)


def build_prompt(out: FetchOutcome, context: str = "") -> str:
    bad, good = select(out.reviews)
    name = out.listing.name if out.listing else out.package
    return PROMPT.format(name=name, package=out.package, context=context, n_bad=len(bad), bad=_lines(bad),
                         n_good=len(good), good=_lines(good))


def prepare(out: FetchOutcome, batch: bool, context: str = "", allow_first_run: bool = False) -> PreparedRequest | None:
    if (out.first_run and not allow_first_run) or len(out.reviews) < MIN_NEW_REVIEWS:
        return None
    prompt = build_prompt(out, context)
    tokens_in = budget.estimate_tokens(SYSTEM + prompt)
    if tokens_in > budget.MAX_PROMPT_TOKENS:
        raise ValueError(f"prompt for {out.package} is {tokens_in} tokens, over the 100K tier limit")
    return PreparedRequest(out.package, prompt, budget.cost(tokens_in, MAX_TOKENS, batch))


# --- parsing -----------------------------------------------------------------------

def parse(text: str) -> Summary:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    data = json.loads(m.group(0))

    def items(key: str) -> list[dict]:
        out = []
        for it in (data.get(key) or [])[:3]:
            theme = str(it.get("theme", "")).strip()
            if not theme:
                continue
            share = it.get("share")
            share = max(0, min(100, round(float(share)))) if isinstance(share, (int, float)) else None
            item = {"theme": theme[:60], "share": share, "example": str(it.get("example", "")).strip()[:120]}
            if it.get("status") in ("persisting", "fixed", "new"):  # daily (with history) and reports
                item["status"] = it["status"]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(it.get("since", ""))):
                item["since"] = it["since"]
            out.append(item)
        return out

    def by_share(xs: list[dict]) -> list[dict]:
        return sorted(xs, key=lambda x: -1 if x["share"] is None else x["share"], reverse=True)

    return Summary(by_share(items("complaints")), by_share(items("praise")), str(data.get("verdict", "")).strip()[:120])


def daily_context(conn, package: str, today: date, days: int = 7) -> str:
    """Recent complaint themes, so Claude can mark each complaint new or persisting. Empty without history."""
    rows = conn.execute(
        """select period_start, complaints from summaries
           where package = %s and period = 'daily' and period_start >= %s and period_start < %s
           order by period_start""", (package, today - timedelta(days=days), today)).fetchall()
    lines = [f"{r['period_start']}: " + "; ".join(c["theme"] for c in r["complaints"]) for r in rows if r["complaints"]]
    return DAILY_CONTEXT.format(today=today, history="\n".join(lines)) if lines else ""


def store(conn, package: str, today: date, s: Summary, period: str = "daily") -> None:
    conn.execute(
        """insert into summaries (package, period, period_start, complaints, praise, verdict)
           values (%s, %s, %s, %s, %s, %s)
           on conflict (package, period, period_start) do update set
             complaints = excluded.complaints, praise = excluded.praise, verdict = excluded.verdict,
             created_at = now()""",
        (package, period, today, Jsonb(s.complaints), Jsonb(s.praise), s.verdict),
    )


# --- Claude calls ----------------------------------------------------------------

def _params(prompt: str) -> dict:
    # Haiku 5.5 thinks adaptively by default and thinking counts against max_tokens.
    # This is plain extraction, so thinking is off: cheaper, and the JSON is never starved.
    return {"model": MODEL, "max_tokens": MAX_TOKENS, "system": SYSTEM, "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": prompt}]}


def _text(message) -> str:
    return "".join(getattr(b, "text", "") for b in message.content)


def _finish(conn, run: JobRun, result: SummaryRun, today: date, package: str, message, batch: bool, source: str):
    usage = message.usage
    budget.record_spend(conn, source, package, usage.input_tokens, usage.output_tokens, batch)
    run.add("claude_calls")
    run.add("claude_tokens_in", usage.input_tokens)
    run.add("claude_tokens_out", usage.output_tokens)
    try:
        s = parse(_text(message))
    except (ValueError, json.JSONDecodeError) as e:
        stop = getattr(message, "stop_reason", None)
        log_error(conn, "claude", package, f"unparseable summary ({stop}): {e}; reply starts {_text(message)[:200]!r}")
        result.failed.append(package)
        return
    store(conn, package, today, s)
    result.done[package] = s


def standard_call(conn, client, run: JobRun, result: SummaryRun, today: date, req: PreparedRequest, source: str,
                  now: datetime | None = None):
    est = budget.cost(budget.estimate_tokens(SYSTEM + req.prompt), MAX_TOKENS, batch=False)
    if not budget.can_spend(conn, est, now):
        result.skipped_budget.append(req.package)
        return
    try:
        message = client.messages.create(**_params(req.prompt))
    except Exception as e:  # noqa: BLE001
        log_error(conn, "claude", req.package, f"{type(e).__name__}: {e}")
        result.failed.append(req.package)
        return
    _finish(conn, run, result, today, req.package, message, batch=False, source=source)


def run_summaries(conn, client, outcomes: list[FetchOutcome], today: date, run: JobRun,
                  deadline: float, use_batch: bool = True, sleep=time.sleep, clock=time.monotonic,
                  contexts: dict[str, str] | None = None, now: datetime | None = None) -> SummaryRun:
    """Summarise every App with enough new reviews, most-watched first, within budget."""
    result = SummaryRun()
    if not budget.claude_enabled():
        result.skipped_budget = [o.package for o in outcomes if o.ok]
        return result

    # Admit requests in Priority Order until the estimated cost would pass today's budget.
    state = budget.state(conn, now)
    room = 0.0 if state.degraded else min(state.remaining_today, budget.DEGRADE_AT * state.available_today - state.spent_today)
    admitted: list[PreparedRequest] = []
    for out in budget.priority_order([o for o in outcomes if o.ok]):
        ctx = contexts.get(out.package, "") if contexts is not None else daily_context(conn, out.package, today)
        req = prepare(out, batch=use_batch, context=ctx)
        if req is None:
            result.skipped_few_reviews.append(out.package)
        elif req.est_cost <= room:
            admitted.append(req)
            room -= req.est_cost
        else:
            result.skipped_budget.append(out.package)
    if not admitted:
        return result

    if not use_batch:
        for req in admitted:
            standard_call(conn, client, run, result, today, req, "daily", now)
        return result

    by_id = {f"r{i}": req for i, req in enumerate(admitted)}
    try:
        batch = client.messages.batches.create(
            requests=[{"custom_id": cid, "params": _params(req.prompt)} for cid, req in by_id.items()])
    except Exception as e:  # noqa: BLE001 — fall back to standard calls
        log_error(conn, "claude", None, f"batch create failed: {type(e).__name__}: {e}")
        for req in admitted:
            standard_call(conn, client, run, result, today, req, "daily", now)
        return result

    while clock() < deadline and client.messages.batches.retrieve(batch.id).processing_status != "ended":
        sleep(POLL_SECONDS)

    pending = dict(by_id)
    if client.messages.batches.retrieve(batch.id).processing_status == "ended":
        for item in client.messages.batches.results(batch.id):
            req = pending.pop(item.custom_id, None)
            if req is None:
                continue
            if item.result.type == "succeeded":
                _finish(conn, run, result, today, req.package, item.result.message, batch=True, source="daily")
            else:
                log_error(conn, "claude", req.package, f"batch item {item.result.type}")
                pending[item.custom_id] = req  # retry below as a standard call
    else:
        try:
            client.messages.batches.cancel(batch.id)
        except Exception:  # noqa: BLE001
            pass
    for req in pending.values():  # deadline reached or item failed: never let the digest be late
        standard_call(conn, client, run, result, today, req, "daily", now)
    return result
