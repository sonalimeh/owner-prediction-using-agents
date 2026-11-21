"""Human decisions on proposals.

A proposal is pending until a person approves or rejects it. Decisions are final: repeating the
same decision changes nothing (the first actor, reason and time are kept), and a contradicting one
is an error. Only a proposal that names an owner can be approved.
"""

from datetime import datetime, timezone

APPROVED, REJECTED = "approved", "rejected"


class DecisionError(Exception):
    pass


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _clean_actor(actor):
    actor = (actor or "").strip()
    if not actor:
        raise DecisionError("--actor must not be empty")
    return actor


def approve_batch(conn, run_id, min_confidence, actor):
    """Approve every pending proposal of a run that names an owner with confidence >= min_confidence.

    All approvals are written in one transaction. Proposals that are already decided, declined or
    below the threshold are left alone and counted in the report.
    """
    actor = _clean_actor(actor)
    if not 0 <= min_confidence <= 1:
        raise DecisionError("--min-confidence must be between 0 and 1")

    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone() is None:
            raise DecisionError(f"no run {run_id}")
        rows = conn.execute(
            """
            SELECT p.proposal_id, p.asset_id, p.proposed_owner, p.confidence, d.decision
            FROM proposals p LEFT JOIN decisions d ON d.proposal_id = p.proposal_id
            WHERE p.run_id = ?
            ORDER BY p.asset_id
            """,
            (run_id,),
        ).fetchall()

        approved, skipped = [], {"already_decided": 0, "declined": 0, "below_threshold": 0}
        decided_at = _now()
        reason = f"approve-batch --min-confidence {min_confidence:g}"
        for r in rows:
            if r["decision"] is not None:
                skipped["already_decided"] += 1
            elif r["proposed_owner"] is None:
                skipped["declined"] += 1
            elif r["confidence"] < min_confidence:
                skipped["below_threshold"] += 1
            else:
                conn.execute(
                    "INSERT INTO decisions (proposal_id, decision, actor, reason, decided_at) VALUES (?, ?, ?, ?, ?)",
                    (r["proposal_id"], APPROVED, actor, reason, decided_at),
                )
                approved.append(
                    {"proposal_id": r["proposal_id"], "asset_id": r["asset_id"],
                     "owner": r["proposed_owner"], "confidence": r["confidence"]}
                )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return {
        "run_id": run_id,
        "min_confidence": min_confidence,
        "actor": actor,
        "approved_count": len(approved),
        "skipped": skipped,
        "approved": approved,
    }


def record(conn, proposal_id, decision, actor, reason=None):
    """Record a decision. Returns (outcome, decision row), outcome being "recorded" or "unchanged"."""
    if decision not in (APPROVED, REJECTED):
        raise ValueError(decision)
    actor = _clean_actor(actor)
    reason = reason.strip() if reason else None
    if decision == REJECTED and not reason:
        raise DecisionError("a rejection needs a --reason")

    conn.execute("BEGIN IMMEDIATE")
    try:
        proposal = conn.execute(
            "SELECT proposal_id, proposed_owner FROM proposals WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        if proposal is None:
            raise DecisionError(f"no proposal {proposal_id}")

        existing = conn.execute("SELECT * FROM decisions WHERE proposal_id = ?", (proposal_id,)).fetchone()
        if existing is not None:
            if existing["decision"] != decision:
                raise DecisionError(
                    f"proposal {proposal_id} was already {existing['decision']} by {existing['actor']}"
                    f" at {existing['decided_at']}; decisions are final"
                )
            conn.execute("COMMIT")
            return "unchanged", dict(existing)

        if decision == APPROVED and proposal["proposed_owner"] is None:
            raise DecisionError(f"proposal {proposal_id} declined to name an owner, so it cannot be approved")

        conn.execute(
            "INSERT INTO decisions (proposal_id, decision, actor, reason, decided_at) VALUES (?, ?, ?, ?, ?)",
            (proposal_id, decision, actor, reason, _now()),
        )
        row = conn.execute("SELECT * FROM decisions WHERE proposal_id = ?", (proposal_id,)).fetchone()
        conn.execute("COMMIT")
        return "recorded", dict(row)
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
