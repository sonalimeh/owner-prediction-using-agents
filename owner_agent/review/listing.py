import json

STATUSES = ("pending", "approved", "applied", "rejected", "declined")

QUERY = """
SELECT p.proposal_id, p.run_id, p.asset_id, a.qualified_name, p.asset_version,
       p.proposed_owner, u.name AS owner_name, p.confidence, p.confidence_group, p.reason,
       p.signal_ids, p.edge_ids, p.model, p.prompt_version,
       p.input_tokens, p.output_tokens, p.cost_usd, p.latency_ms, p.created_at,
       CASE WHEN EXISTS (SELECT 1 FROM audit_log l WHERE l.proposal_id = p.proposal_id) THEN 'applied'
            WHEN d.decision IS NOT NULL THEN d.decision
            WHEN p.proposed_owner IS NULL THEN 'declined'
            ELSE 'pending' END AS status,
       d.actor AS decided_by, d.reason AS decision_reason, d.decided_at
FROM proposals p
JOIN assets a ON a.id = p.asset_id
LEFT JOIN users u ON u.id = p.proposed_owner
LEFT JOIN decisions d ON d.proposal_id = p.proposal_id
WHERE p.run_id = ?
ORDER BY p.asset_id
"""


class ListingError(Exception):
    pass


def resolve_run(conn, run_id):
    if run_id and run_id != "latest":
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise ListingError(f"no run {run_id}")
        return row
    row = conn.execute("SELECT * FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
    if row is None:
        raise ListingError("no proposals yet; run propose first")
    return row


def proposals(conn, run_id=None, status=None):
    run = resolve_run(conn, run_id)
    rows = [dict(r) for r in conn.execute(QUERY, (run["run_id"],))]
    for r in rows:
        r["signal_ids"] = json.loads(r["signal_ids"])
        r["edge_ids"] = json.loads(r["edge_ids"])
    if status:
        rows = [r for r in rows if r["status"] == status]
    return dict(run), rows


def runs(conn):
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT r.run_id, r.mode, r.model, r.prompt_version, r.started_at,
                   count(p.proposal_id) AS proposals,
                   sum(p.proposed_owner IS NOT NULL) AS owners,
                   round(coalesce(sum(p.cost_usd), 0), 6) AS cost_usd
            FROM runs r LEFT JOIN proposals p ON p.run_id = r.run_id
            GROUP BY r.run_id
            ORDER BY r.started_at, r.rowid
            """
        )
    ]


def table(run, rows):
    lines = [
        f"run {run['run_id']}  mode={run['mode']}  model={run['model'] or '-'}  "
        f"prompt={run['prompt_version']}  started={run['started_at']}  proposals={len(rows)}",
    ]
    header = ("PROPOSAL", "ASSET", "QUALIFIED_NAME", "OWNER", "CONF", "STATUS", "DECIDED_BY", "MODEL")
    body = [
        (
            r["proposal_id"],
            r["asset_id"],
            r["qualified_name"],
            f"{r['proposed_owner']} ({r['owner_name']})" if r["proposed_owner"] else "-",
            f"{r['confidence']:.2f}",
            r["status"],
            r["decided_by"] or "-",
            r["model"] or "rules",
        )
        for r in rows
    ]
    widths = [max(len(str(c)) for c in col) for col in zip(header, *body)]
    for cells in [header, *body]:
        lines.append("  ".join(str(c).ljust(w) for c, w in zip(cells, widths)).rstrip())
    return lines
