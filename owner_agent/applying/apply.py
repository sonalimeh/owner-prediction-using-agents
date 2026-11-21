"""Write approved proposals to the catalog.

Approved proposals are processed in approval order (decided_at, then proposal_id), one
transaction each. A proposal is applied exactly when it has an audit entry; the owner write and
that entry commit together, so a crash can never leave one without the other.

A proposal is skipped, and the asset left alone, when any of these hold:
  - the asset was deleted;
  - the asset already has an owner (set by a person, or by an earlier agent write);
  - the asset's version differs from the version the proposal saw;
  - the proposed owner is now inactive or a service account.
"""

from datetime import datetime, timezone

from owner_agent.storage import db

APPROVED_QUERY = """
SELECT p.proposal_id, p.asset_id, p.asset_version, p.proposed_owner, d.actor, d.decided_at,
       EXISTS (SELECT 1 FROM audit_log l WHERE l.proposal_id = p.proposal_id) AS applied
FROM decisions d JOIN proposals p ON p.proposal_id = d.proposal_id
WHERE d.decision = 'approved'
ORDER BY d.decided_at, p.proposal_id
"""


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _cause(conn, column, value, ops):
    """The latest applied change of the given ops on an asset or user, as ' (change c_1 by human:x)'."""
    row = conn.execute(
        f"""
        SELECT change_id, actor FROM changes
        WHERE {column} = ? AND outcome = 'applied' AND op IN ({', '.join('?' * len(ops))})
        ORDER BY applied_at DESC, rowid DESC LIMIT 1
        """,
        (value, *ops),
    ).fetchone()
    return f" (change {row['change_id']} by {row['actor']})" if row else ""


def _skip_reason(conn, asset, user, proposal):
    asset_id = proposal["asset_id"]
    if asset["deleted"]:
        return "asset was deleted" + _cause(conn, "asset_id", asset_id, ("delete_asset",))
    if asset["owner"] is not None:
        if asset["owner_set_by"] == "human":
            return f"asset already has owner {asset['owner']} set by a person" + _cause(
                conn, "asset_id", asset_id, ("set_owner",)
            )
        return f"asset already has owner {asset['owner']} set by the agent"
    if asset["version"] != proposal["asset_version"]:
        return (
            f"asset version changed from {proposal['asset_version']} to {asset['version']} since the proposal"
            + _cause(conn, "asset_id", asset_id, ("set_owner", "delete_asset"))
        )
    if user["user_type"] != "person":
        return f"proposed owner {user['id']} is a service account"
    if not user["active"]:
        return f"proposed owner {user['id']} is no longer active" + _cause(
            conn, "user_id", user["id"], ("deactivate_user",)
        )
    return None


def _apply_one(conn, p):
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("SELECT 1 FROM audit_log WHERE proposal_id = ?", (p["proposal_id"],)).fetchone():
            conn.execute("ROLLBACK")
            return "already_applied", None
        asset = conn.execute(
            "SELECT owner, owner_set_by, version, deleted FROM assets WHERE id = ?", (p["asset_id"],)
        ).fetchone()
        user = conn.execute(
            "SELECT id, user_type, active FROM users WHERE id = ?", (p["proposed_owner"],)
        ).fetchone()
        reason = _skip_reason(conn, asset, user, p)
        if reason:
            conn.execute("ROLLBACK")
            return "skipped", reason

        updated = conn.execute(
            """
            UPDATE assets SET owner = ?, owner_set_by = 'agent', version = version + 1
            WHERE id = ? AND version = ? AND owner IS NULL AND deleted = 0
            """,
            (p["proposed_owner"], p["asset_id"], p["asset_version"]),
        ).rowcount
        if updated != 1:
            raise RuntimeError(f"asset {p['asset_id']} changed during apply")
        conn.execute(
            """
            INSERT INTO audit_log (proposal_id, actor, asset_id, old_owner, new_owner, version_before, version_after, ts)
            VALUES (?, ?, ?, NULL, ?, ?, ?, ?)
            """,
            (p["proposal_id"], p["actor"], p["asset_id"], p["proposed_owner"],
             p["asset_version"], p["asset_version"] + 1, _now()),
        )
        db.commit(conn)
        return "applied", None
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def apply(conn):
    """Apply every approved, unapplied proposal. Returns the report: one result per approved proposal."""
    db.crash_if_due()
    results = []
    for p in conn.execute(APPROVED_QUERY).fetchall():
        if p["applied"]:
            outcome, reason = "already_applied", None
        else:
            outcome, reason = _apply_one(conn, p)
        result = {
            "proposal_id": p["proposal_id"],
            "asset_id": p["asset_id"],
            "owner": p["proposed_owner"],
            "outcome": outcome,
        }
        if reason:
            result["reason"] = reason
        results.append(result)
    counts = {k: sum(r["outcome"] == k for r in results) for k in ("applied", "skipped", "already_applied")}
    return {**counts, "results": results}
