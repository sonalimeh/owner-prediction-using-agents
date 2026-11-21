"""Fixed lists used by the generator: domains and teams, robots, names, asset names, and the texts
that appear in descriptions and notes. No logic and no randomness here.

  {n} in a template is a person's name, {t} a team, {ty} the asset type.
"""
from datetime import datetime, timezone

# ------------------------------------------------------------------ domains, teams and robots
DOMAINS = ["finance", "growth", "marketing", "platform", "risk"]
TEAM = {"finance": "finance-data", "growth": "growth-analytics", "marketing": "marketing-ops",
        "platform": "platform-eng", "risk": "risk-analytics"}
TEAMS = [TEAM[d] for d in DOMAINS]
DOMAIN_OF = {t: d for d, t in TEAM.items()}
TEAM_SIZE = {"platform-eng": 38}            # the other teams have 39 people
SERVICES = [("svc_airflow", "platform-eng"), ("svc_dbt", "platform-eng"), ("svc_looker", "platform-eng"),
            ("svc_tableau", "platform-eng"), ("svc_finance_etl", "finance-data"), ("svc_risk_scorer", "risk-analytics")]
OWN_SVC = {"finance": "svc_finance_etl", "risk": "svc_risk_scorer"}
QUERY_SVC = ["svc_airflow", "svc_dbt", "svc_looker", "svc_tableau"]
TYPE_CYCLE = ["table"] * 5 + ["view"] * 2 + ["pipeline"] * 2 + ["dashboard"]

# ------------------------------------------------------------------ the time window
START = datetime(2026, 8, 1, tzinfo=timezone.utc)
DAY = 86400
WINDOW = 91 * DAY                           # 2026-08-01T00:00:00Z .. 2026-10-30T23:59:59Z
WINDOWS = {"early": (0, 35), "mid": (28, 63), "late": (56, 91), "spread": (0, 91)}

# ------------------------------------------------------------------ names
# First and last names are reused across teams so that names collide, as in real companies.
FIRST = """Aarav Abena Adele Akira Alba Amara Anil Anouk Arjun Astrid Ayaan Beatriz Bianca Bodhi Brigid Camila Carys
Chen Chidi Clara Dara Daria Dev Dilnoza Eamon Elif Elio Emeka Esme Farah Felix Freya Gael Goran Greta Hana Hugo Idris
Ines Ingrid Isak Jalen Jonas Jun Kai Keiko Kofi Lars Leila Lior Luca Maeve Malik Mateo Mei Mira Nadia Naveen Niamh Nico
Noor Olu Omar Oren Paloma Pavel Petra Priya Quinn Rafael Rania Rhea Rohan Rosa Sana Santiago Selin Sione Sofia Soren
Tara Tariq Thea Tomas Uma Ugo Valeria Vikram Wei Wren Ximena Yara Yusuf Zainab Zane Zoe Zuri Anika Bao Cyrus Dunya
Eitan Fatima Gwen Hiro Imani Joss Kira Laszlo Mina Nia Otis Pia Reza Signe Teo Ulla Vera Wanjiru Xavi Yuki Zofia""".split()
LAST = """Abara Adeyemi Bauer Bose Brennan Castillo Costa Dahl Demir Duarte Eze Ferreira Haddad Iyer Jensen Kaur
Kimura Lindqvist Mbeki Moreau Nakamura Novak Okafor Ortiz Petrov Quispe Rao Rossi Sato Schmidt Silva Tanaka Trent
Ueda Varga Vasquez Wong Yilmaz Zhou""".split()
UNKNOWN_FIRST = ["Lena", "Marek", "Odile", "Bram", "Sunita", "Tobias", "Halima", "Ruben"]   # never users
UNKNOWN_LAST = ["Fischer", "Holloway", "Marchetti", "Oyelaran", "Lindgren", "Stavros"]
# ------------------------------------------------------------------ asset names: warehouse.<domain>.<stem>_<suffix>
STEMS = """claims fx_rates refunds orders sessions leads invoices payouts ledger budgets forecasts accounts signups
funnels cohorts churn retention campaigns clicks impressions spend attribution deploys jobs builds alerts latency
capacity incidents costs fraud_scores limits exposures defaults chargebacks kyc_checks watchlist revenue margin tax
payroll vendors contracts renewals trials activations referrals emails segments audiences creatives events pageviews
experiments features users devices regions tickets surveys""".split()
SUFFIXES = ["clean", "agg", "daily", "snapshot", "rollup", "hourly", "latest", "v2", "staging", "history", "weekly", "raw"]

# ------------------------------------------------------------------ text templates
# Every template used on an orphan is also planted on owned assets (see generate.py, step 5).
GENERIC_DESC = ["Source of truth for reporting; see runbook.", "Incremental load, partitioned by day.",
                "Deduplicated and typed; safe for joins.", "Feeds the weekly business review.",
                "Used by downstream dashboards.", "Rebuilt nightly from source extracts.",
                "Nightly extract; see runbook.", "Legacy extract."]
DESC = {  # {n} = a name, {t} = a team
    "maint": "Maintained by {n}.",
    "built": "Built and maintained by {n}; rebuilt nightly by the scheduler.",
    "owned_team": "Owned and maintained by {n} ({t}). Ask them before changing the schema.",
    "owned": "Owned by {n}.",
    "maintainer": "Maintainer: {n}.",
    "team_only": "Maintained by the {t} team.",
}
FULL_KEYS = ["maint", "built", "owned_team", "owned", "maintainer"]     # for full names
SHORT_KEYS = ["maint", "owned", "maintainer"]                           # for first names
NOTE = {  # {n} = a name, {ty} = the asset type
    "esc": "Escalated to {n}, who maintains this {ty}; they fixed the upstream filter.",
    "questions": "Questions go to {n}; they maintain this.",
    "hand_sep": "Handed this to {n} in September.",
    "hand_oct": "Handed this to {n} in October.",
    "disown": "I no longer maintain this.",
    "self_claim": "Fixed the late partition; I maintain this job.",   # the brief's own example note
}
INCIDENT_NOTES = ["Restarted the load; data caught up.", "Re-ran after the upstream delay.",
                  "Backfilled the missing partition.", "Investigated row-count alert; false alarm."]
EDIT_NOTES = ["Added a column for the new source field.", "Fixed a type cast."]
QUERY_NOTES = ["Ad hoc check for the weekly review."]

# ------------------------------------------------------------------ other wordings
# The same kinds of statement in words the rules scorer does not know. Reading them is the model's job.
# Each entry is (wordings used in both splits, wordings kept for the held_out split only).
# {n} is a full name; {f} is a first name.
OTHER_WORDINGS = {
    # a description that names the maintainer
    "names_desc": (["{n} looks after this one.", "{n} is responsible for this dataset."],
                   ["Speak to {n}, who runs this."]),
    # a note, written by someone else, that names the maintainer
    "names_note": (["Passed to {n}, who looks after this.", "Raised with {n}, who is responsible for this dataset."],
                   ["Pinged {n}, who runs this."]),
    # a note in which the writer hands the asset over to {n}
    "hands_over": (["{n} has taken this over from me.", "Passing this one to {n} from now on."],
                   ["{n} is picking this up; I have moved to another project."]),
    # a note in which the writer says they stopped maintaining it
    "disowns": (["Not mine any more.", "I've stopped looking after this."],
                ["I have moved off this; please stop assigning it to me."]),
    # text that says nothing about who owns the asset
    "harmless_note": (["Chatted with {f} about the row counts.", "Thanks {f} for the quick review."],
                      ["Paired with {f} on the backfill."]),
    "harmless_desc": (["Ask in the data channel before running a backfill.", "See the wiki page for column definitions."],
                      ["Do not query during the nightly load."]),
}
