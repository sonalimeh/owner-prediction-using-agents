"""The commands as tools. Each wraps one module's entry point; propose_owners lives in propose.

Approving and rejecting are tools so they are checked and logged like the rest, but the actor is
always the person running the command: nothing here decides on its own.
"""

from owner_agent import tools
from owner_agent.applying import apply
from owner_agent.catalog import changes, dump, load
from owner_agent.evaluating import score
from owner_agent.proposing import propose  # noqa: F401  registers propose_owners and its step tools
from owner_agent.review import decisions, listing
from owner_agent.tools import READ, WRITE, Param, ToolError


@tools.tool("load_catalog", WRITE, seed_path=Param(str), db_path=Param(str))
def load_catalog(ctx, seed_path, db_path):
    """Create the database from a seed file, replacing any existing one. Returns record counts."""
    return load.load(seed_path, db_path)


@tools.tool("list_proposals", READ, run_id=Param(str), status=Param(str, optional=True, choices=listing.STATUSES))
def list_proposals(ctx, run_id, status=None):
    """The proposals of a run ("latest" for the newest) with their status."""
    run, rows = listing.proposals(ctx.conn, run_id, status)
    return {"run": run, "proposals": rows}


@tools.tool("list_runs", READ)
def list_runs(ctx):
    """Every propose run with its proposal, owner and cost totals."""
    return listing.runs(ctx.conn)


@tools.tool("approve_proposal", WRITE, proposal_id=Param(str), actor=Param(str))
def approve_proposal(ctx, proposal_id, actor):
    """Record a person's approval of a pending proposal that names an owner."""
    outcome, row = decisions.record(ctx.conn, proposal_id, decisions.APPROVED, actor)
    return {"outcome": outcome, **row}


@tools.tool("reject_proposal", WRITE, proposal_id=Param(str), actor=Param(str), reason=Param(str))
def reject_proposal(ctx, proposal_id, actor, reason):
    """Record a person's rejection of a pending proposal, with the reason."""
    outcome, row = decisions.record(ctx.conn, proposal_id, decisions.REJECTED, actor, reason)
    return {"outcome": outcome, **row}


@tools.tool("approve_batch", WRITE, run_id=Param(str), min_confidence=Param(float), actor=Param(str))
def approve_batch(ctx, run_id, min_confidence, actor):
    """Record a person's approval of every pending owner proposal of a run at or above a confidence."""
    return decisions.approve_batch(ctx.conn, run_id, min_confidence, actor)


@tools.tool("apply_changes", WRITE, changes_path=Param(str))
def apply_changes(ctx, changes_path):
    """Apply the edits people made, from a changes file."""
    return changes.apply_changes(ctx.conn, changes_path)


@tools.tool("apply_approved", WRITE)
def apply_approved(ctx):
    """Write every approved, unapplied proposal to the catalog. Returns the apply report."""
    return apply.apply(ctx.conn)


@tools.tool(
    "evaluate_run", READ, truth_path=Param(str), run_id=Param(str),
    compare=Param(list, optional=True), split=Param(str, optional=True, choices=score.SPLITS),
)
def evaluate_run(ctx, truth_path, run_id, compare=None, split=None):
    """Score a run's proposals against a truth file; compare adds consistency across runs."""
    if ctx.command != "eval":
        raise ToolError(f"evaluate_run reads truth, and only eval may; refused in {ctx.command}")
    return score.evaluate(ctx.conn, truth_path, run_id, compare or [], split)


@tools.tool("dump_assets", READ)
def dump_assets(ctx):
    """Every asset as a sorted JSON line."""
    return list(dump.asset_lines(ctx.conn))


@tools.tool("read_audit_log", READ)
def read_audit_log(ctx):
    """Every audit entry as a sorted JSON line."""
    return list(dump.audit_lines(ctx.conn))
