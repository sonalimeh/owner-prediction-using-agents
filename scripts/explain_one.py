"""Explain one asset with the real propose pipeline, without writing anything.
usage: python explain_one.py <asset_id> [--model] [--db catalog.db]"""
import json, sys, dataclasses
from owner_agent import tools
from owner_agent.storage import db
from owner_agent.proposing import rules, text_facts, llms, propose  # noqa: registers tools
from owner_agent.tools import commands  # noqa

asset_id = sys.argv[1]
use_model = "--model" in sys.argv
db_path = sys.argv[sys.argv.index("--db") + 1] if "--db" in sys.argv else "catalog.db"
conn = db.open_existing(db_path, readonly=True)
client = None
if use_model:
    llms.load_dotenv(); client = llms.get_client()
ctx = tools.Context(conn, "propose", readonly=True, client=client)
ctx.state["confidence"] = rules.load_confidence()
row = conn.execute("SELECT id, qualified_name, description, version, owner FROM assets WHERE id=?", (asset_id,)).fetchone()
if row is None: sys.exit(f"no asset {asset_id}")
asset = rules.Asset(row["id"], row["qualified_name"], row["description"], row["version"])
ev = tools.call(ctx, "read_evidence", asset_id=asset.id)
text = tools.call(ctx, "read_text", asset=asset, signals=ev["signals"])
extraction = text_facts.Extraction()
if use_model and text["unread"]:
    extraction = tools.call(ctx, "extract_text_facts", asset=asset, signals=ev["signals"], unread=text["unread"])
notes = list(extraction.notes) + ([f"Model call failed ({extraction.error})"] if extraction.error else [])
d = tools.call(ctx, "decide_owner", asset=asset, signals=ev["signals"], neighbours=ev["neighbours"],
               mentions=text["mentions"] + extraction.mentions, model_read=extraction.read, notes=notes)
print(json.dumps({"asset": dataclasses.asdict(asset), "current_owner": row["owner"],
                  "unread_text": text["unread"], "model_called": extraction.called,
                  "tokens": [extraction.input_tokens, extraction.output_tokens], "cost_usd": extraction.cost_usd,
                  "decision": dataclasses.asdict(d)}, indent=2))
