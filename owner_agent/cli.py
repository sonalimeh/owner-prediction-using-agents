import argparse
import json
import os
import sys

from owner_agent import tools
from owner_agent.catalog import changes, load
from owner_agent.evaluating import score
from owner_agent.proposing import llms, propose, rules
from owner_agent.review import decisions, listing
from owner_agent.storage import db
from owner_agent.tools import commands  # noqa: F401  registers the command tools


def _run(args, tool, readonly=False, client=None, **tool_args):
    """Open the database, run one tool against it, and close it."""
    conn = db.open_existing(args.db, readonly=readonly)
    try:
        ctx = tools.Context(conn, args.command, readonly=readonly, client=client)
        return tools.call(ctx, tool, **tool_args)
    finally:
        conn.close()


def _print_json(value):
    print(json.dumps(value, sort_keys=True))


def cmd_load(args):
    counts = tools.call(tools.Context(None, args.command), "load_catalog", seed_path=args.seed, db_path=args.db)
    _print_json({"db": args.db, **counts})


def _print_lines(lines):
    for line in lines:
        sys.stdout.write(line + "\n")


def cmd_dump(args):
    _print_lines(_run(args, "dump_assets", readonly=True))


def cmd_audit(args):
    _print_lines(_run(args, "read_audit_log", readonly=True))


def cmd_propose(args):
    client = None
    if not args.baseline:
        llms.load_dotenv()
        client = llms.get_client(args.model)
    result = _run(args, "propose_owners", client=client, baseline=args.baseline, workers=args.workers)
    print(result["run_id"])
    print(json.dumps(result["counts"], sort_keys=True), file=sys.stderr)


def cmd_approve(args):
    _print_json(_run(args, "approve_proposal", proposal_id=args.proposal_id, actor=args.actor))


def cmd_reject(args):
    _print_json(_run(args, "reject_proposal", proposal_id=args.proposal_id, actor=args.actor, reason=args.reason))


def cmd_approve_batch(args):
    _print_json(_run(args, "approve_batch", run_id=args.run, min_confidence=args.min_confidence, actor=args.actor))


def cmd_changes(args):
    _print_json(_run(args, "apply_changes", changes_path=args.changes_file))


def cmd_apply(args):
    _print_json(_run(args, "apply_approved"))


def cmd_proposals(args):
    if args.runs:
        for run in _run(args, "list_runs", readonly=True):
            _print_json(run)
        return
    result = _run(args, "list_proposals", readonly=True, run_id=args.run, status=args.status)
    run, rows = result["run"], result["proposals"]
    if args.json:
        for r in rows:
            print(json.dumps(r, sort_keys=True))
    else:
        print("\n".join(listing.table(run, rows)))


def cmd_eval(args):
    result = _run(
        args, "evaluate_run", readonly=True,
        truth_path=args.truth, run_id=args.run, compare=args.compare, split=args.split,
    )
    if args.calibrate:
        existing = None
        if os.path.isfile(args.calibrate):
            with open(args.calibrate, encoding="utf-8") as f:
                try:
                    existing = json.load(f)
                except ValueError as e:
                    raise score.EvalError(f"{args.calibrate} is not valid JSON: {e}") from None
        table = score.calibration_file(result, existing, args.target)
        with open(args.calibrate, "w", encoding="utf-8", newline="\n") as f:
            json.dump(table, f, indent=2)
            f.write("\n")
        print(f"wrote {args.calibrate}", file=sys.stderr)
    if args.json:
        _print_json(result)
    else:
        print("\n".join(score.report(result)))


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--db",
        default=os.environ.get("OWNER_AGENT_DB", "catalog.db"),
        help="SQLite file (default: $OWNER_AGENT_DB or catalog.db)",
    )
    parser = argparse.ArgumentParser(prog="owner_agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("load", prog="load", parents=[common], help="create the database from a seed file, replacing any existing one")
    p.add_argument("seed")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("propose", prog="propose", parents=[common], help="record one proposal per orphaned asset; prints the run_id")
    p.add_argument("--baseline", action="store_true", help="deterministic rules only, no model call")
    p.add_argument("--model", help="model name from owner_agent/proposing/llms/models.toml (default: $OWNER_AGENT_MODEL or the file's default)")
    p.add_argument(
        "--workers", type=int, default=propose.DEFAULT_WORKERS,
        help=f"model calls in flight at once (default: {propose.DEFAULT_WORKERS}; 1 for one at a time)",
    )
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser("proposals", prog="proposals", parents=[common], help="show the proposals of a run and their status")
    p.add_argument("--run", default="latest", help="run_id to show (default: the latest run)")
    p.add_argument("--status", choices=listing.STATUSES, help="only show proposals with this status")
    p.add_argument("--json", action="store_true", help="print full records as JSONL")
    p.add_argument("--runs", action="store_true", help="list all runs instead")
    p.set_defaults(func=cmd_proposals)

    p = sub.add_parser("approve", prog="approve", parents=[common], help="approve a pending proposal that names an owner")
    p.add_argument("proposal_id")
    p.add_argument("--actor", required=True, help="the person approving")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser(
        "approve-batch", prog="approve-batch", parents=[common],
        help="approve every pending proposal of a run that names an owner with confidence >= x",
    )
    p.add_argument("--run", required=True, help="run_id whose proposals to approve")
    p.add_argument("--min-confidence", required=True, type=float, help="approve proposals at or above this confidence")
    p.add_argument("--actor", required=True, help="the person approving")
    p.set_defaults(func=cmd_approve_batch)

    p = sub.add_parser("reject", prog="reject", parents=[common], help="reject a pending proposal")
    p.add_argument("proposal_id")
    p.add_argument("--actor", required=True, help="the person rejecting")
    p.add_argument("--reason", required=True, help="why it is rejected")
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("changes", prog="changes", parents=[common], help="apply edits people made, from a changes file")
    p.add_argument("changes_file")
    p.set_defaults(func=cmd_changes)

    p = sub.add_parser("apply", prog="apply", parents=[common], help="write approved, unapplied proposals to the catalog")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("evals", prog="evals", parents=[common], help="score a run against a truth file; writes nothing")
    p.add_argument("truth")
    p.add_argument("--run", required=True, help='run_id to score ("latest" for the newest)')
    p.add_argument("--compare", nargs="+", action="extend", metavar="RUN_ID", help="other runs, for consistency")
    p.add_argument("--split", choices=score.SPLITS, help="only score truth rows of this split")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    p.add_argument(
        "--calibrate", metavar="CONFIDENCE_JSON",
        help="write the measured group confidences to this file, keeping groups this run did not answer "
             f"(needs --split calibration; propose reads ${rules.CONFIDENCE_ENV} or the packaged confidence.json)",
    )
    p.add_argument("--target", type=float, default=0.95, help="threshold recorded with --calibrate (default 0.95)")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("dump", prog="dump", parents=[common], help="print all assets as sorted JSONL")
    p.set_defaults(func=cmd_dump)

    p = sub.add_parser("audit", prog="audit", parents=[common], help="print the audit log as sorted JSONL")
    p.set_defaults(func=cmd_audit)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (
        load.LoadError, db.DatabaseError, propose.ProposeError, llms.LLMError,
        decisions.DecisionError, listing.ListingError, changes.ChangesError, tools.ToolError,
        score.EvalError, rules.ConfidenceError,
    ) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        sys.stderr.close()
        return 0
    return 0


def _command(name):
    return lambda: main([name, *sys.argv[1:]])


load_command = _command("load")
propose_command = _command("propose")
proposals_command = _command("proposals")
approve_command = _command("approve")
approve_batch_command = _command("approve-batch")
reject_command = _command("reject")
changes_command = _command("changes")
apply_command = _command("apply")
evals_command = _command("evals")
dump_command = _command("dump")
audit_command = _command("audit")
