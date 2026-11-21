import dataclasses
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from owner_agent import tools
from owner_agent.proposing import rules, text_facts
from owner_agent.proposing.llms import LLMError
from owner_agent.tools import MODEL, READ, WRITE, Param

BASELINE_VERSION = "scorer-v1"
DEFAULT_WORKERS = 8


class ProposeError(Exception):
    pass


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _cached(ctx, key, build):
    if key not in ctx.state:
        ctx.state[key] = build()
    return ctx.state[key]


def _roster(ctx):
    return _cached(ctx, "roster", lambda: rules.Roster(tools.call(ctx, "read_users")))


@tools.tool("read_users", READ)
def read_users(ctx):
    """Every user, by id."""
    return {
        r["id"]: rules.User(r["id"], r["name"], r["team"], r["user_type"], bool(r["active"]))
        for r in ctx.conn.execute("SELECT id, name, team, user_type, active FROM users")
    }


@tools.tool("find_orphans", READ)
def find_orphans(ctx):
    """The assets with no owner that are not deleted, by id."""
    return [
        rules.Asset(r["id"], r["qualified_name"], r["description"], r["version"])
        for r in ctx.conn.execute(
            "SELECT id, qualified_name, description, version FROM assets WHERE owner IS NULL AND deleted = 0 ORDER BY id"
        )
    ]


@tools.tool("read_evidence", READ, asset_id=Param(str))
def read_evidence(ctx, asset_id):
    """An asset's signals, and its neighbours across edges with their owners."""
    signals = [
        rules.Signal(r["signal_id"], r["user_id"], r["signal_type"], r["note"])
        for r in ctx.conn.execute(
            "SELECT signal_id, user_id, signal_type, note FROM signals WHERE asset_id = ? ORDER BY signal_id",
            (asset_id,),
        )
    ]
    neighbours = [
        rules.Neighbour(r["edge_id"], r["neighbour"], r["owner"])
        for r in ctx.conn.execute(
            """
            SELECT e.edge_id, n.id AS neighbour, n.owner
            FROM edges e JOIN assets n ON n.id = CASE WHEN e.from_id = ? THEN e.to_id ELSE e.from_id END
            WHERE ? IN (e.from_id, e.to_id)
            ORDER BY e.edge_id
            """,
            (asset_id, asset_id),
        )
    ]
    return {"signals": signals, "neighbours": neighbours}


@tools.tool("read_text", READ, asset=Param(rules.Asset), signals=Param(list))
def read_text(ctx, asset, signals):
    """The ownership statements the rule patterns recognise, and the texts they could not read."""
    mentions = rules.read_text(_roster(ctx), asset, signals)
    return {"mentions": mentions, "unread": rules.unread_text(asset, signals, {m.source for m in mentions})}


@tools.tool("extract_text_facts", MODEL, asset=Param(rules.Asset), signals=Param(list), unread=Param(list))
def extract_text_facts(ctx, asset, signals, unread):
    """What the unread texts say about people, read by the model and checked by code, as Mentions."""
    if ctx.client is None:
        raise ProposeError("model-assisted propose needs a model client")
    extractor = _cached(ctx, "extractor", lambda: text_facts.Extractor(ctx.client, _roster(ctx)))
    return extractor.extract(asset, signals, unread)


@tools.tool(
    "decide_owner", READ,
    asset=Param(rules.Asset), signals=Param(list), neighbours=Param(list),
    mentions=Param(list), model_read=Param(list), notes=Param(list),
)
def decide_owner(ctx, asset, signals, neighbours, mentions, model_read, notes):
    """Apply the owner rules to the evidence: an owner or a decline, with its group's measured confidence."""
    return rules.decide(asset, signals, neighbours, _roster(ctx), ctx.state["confidence"], mentions, model_read, notes)


@tools.tool(
    "record_proposal", WRITE,
    run_id=Param(str), asset=Param(rules.Asset), decision=Param(rules.Decision),
    extraction=Param(text_facts.Extraction), latency_ms=Param(float),
)
def record_proposal(ctx, run_id, asset, decision, extraction, latency_ms):
    """Write one proposal. Proposals are never changed after this."""
    proposal_id = _new_id("p")
    ctx.conn.execute(
        """
        INSERT INTO proposals (
            proposal_id, run_id, asset_id, asset_version, proposed_owner, confidence, confidence_group,
            signal_ids, edge_ids, reason, model, prompt_version,
            input_tokens, output_tokens, cost_usd, latency_ms, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            proposal_id, run_id, asset.id, asset.version, decision.owner, decision.confidence, decision.group,
            json.dumps(decision.signal_ids), json.dumps(decision.edge_ids), decision.reason,
            ctx.client.name if extraction.called else None, ctx.state["prompt_version"],
            extraction.input_tokens, extraction.output_tokens, extraction.cost_usd,
            latency_ms, _now(),
        ),
    )
    return proposal_id


def _extract(ctx, asset, signals, unread):
    """Run in a worker thread: the model call only, on its own call stack, never touching the database."""
    started = time.perf_counter()
    worker = dataclasses.replace(ctx, stack=list(ctx.stack))
    extraction = tools.call(worker, "extract_text_facts", asset=asset, signals=signals, unread=unread)
    return extraction, time.perf_counter() - started


@tools.tool("propose_owners", WRITE, baseline=Param(bool), workers=Param(int, optional=True))
def propose_owners(ctx, baseline, workers=None):
    """Start a run and record one proposal per orphaned asset. Returns the run_id and counts.

    The model is called only for assets with text the rule patterns could not read. Those calls run on
    up to `workers` threads; evidence reads and proposal writes stay on this one.
    """
    workers = DEFAULT_WORKERS if workers is None else workers
    if workers < 1:
        raise ProposeError("--workers must be at least 1")
    if baseline:
        mode, model_name, prompt_version = "baseline", None, BASELINE_VERSION
    else:
        if ctx.client is None:
            raise ProposeError("model-assisted propose needs a model client")
        try:
            ctx.client.check()
        except LLMError as e:
            raise ProposeError(str(e)) from None
        mode, model_name, prompt_version = "model", ctx.client.name, f"{BASELINE_VERSION}+{text_facts.PROMPT_VERSION}"
    ctx.state["prompt_version"] = prompt_version
    ctx.state["confidence"] = rules.load_confidence()
    # Built once up front so no proposal's latency includes them.
    roster = _roster(ctx)
    if not baseline:
        ctx.state["extractor"] = text_facts.Extractor(ctx.client, roster)

    run_id = _new_id("r")
    counts = {"proposals": 0, "owners": 0, "declined": 0, "model_calls": 0, "model_errors": 0, "cost_usd": 0.0}
    ctx.conn.execute(
        "INSERT INTO runs (run_id, mode, model, prompt_version, started_at) VALUES (?, ?, ?, ?, ?)",
        (run_id, mode, model_name, prompt_version, _now()),
    )
    # A proposal's latency is the sum of its own steps, so time queued behind other calls is not counted.
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        jobs = []
        for asset in tools.call(ctx, "find_orphans"):
            started = time.perf_counter()
            evidence = tools.call(ctx, "read_evidence", asset_id=asset.id)
            text = tools.call(ctx, "read_text", asset=asset, signals=evidence["signals"])
            read_s = time.perf_counter() - started
            future = None
            if not baseline and text["unread"]:
                future = pool.submit(_extract, ctx, asset, evidence["signals"], text["unread"])
            jobs.append((asset, evidence, text, read_s, future))

        for asset, evidence, text, read_s, future in jobs:
            extraction, extract_s = future.result() if future else (text_facts.Extraction(), 0.0)
            started = time.perf_counter()
            notes = list(extraction.notes)
            if extraction.error:
                notes.append(f"Model call failed ({extraction.error}); its texts stay unread")
            decision = tools.call(
                ctx, "decide_owner",
                asset=asset, signals=evidence["signals"], neighbours=evidence["neighbours"],
                mentions=text["mentions"] + extraction.mentions, model_read=extraction.read, notes=notes,
            )
            latency_ms = round((read_s + extract_s + time.perf_counter() - started) * 1000, 3)

            tools.call(
                ctx, "record_proposal",
                run_id=run_id, asset=asset, decision=decision, extraction=extraction, latency_ms=latency_ms,
            )
            counts["proposals"] += 1
            counts["owners" if decision.owner else "declined"] += 1
            counts["model_calls"] += extraction.called
            counts["model_errors"] += extraction.error is not None
            counts["cost_usd"] += extraction.cost_usd
    finally:
        pool.shutdown(cancel_futures=True)
    counts["cost_usd"] = round(counts["cost_usd"], 6)
    return {"run_id": run_id, "counts": counts}


def propose(conn, use_baseline, client=None, workers=None):
    """Record one proposal per orphaned asset in a new run. Returns (run_id, counts).

    With a client, the model reads the text the rule patterns cannot; the rules decide in both modes.
    """
    ctx = tools.Context(conn, "propose", client=client)
    result = tools.call(ctx, "propose_owners", baseline=use_baseline, workers=workers)
    return result["run_id"], result["counts"]
