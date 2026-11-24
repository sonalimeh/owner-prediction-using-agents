"""Model-assisted text reading, for the text the rule patterns cannot read.

rules.read_text() knows a fixed set of wordings. Any other text that is not routine goes to the model,
which reports what it says about people, with a quote. Code checks every fact and drops it unless:
the user is in the roster, the source is one it was shown, the quote is an exact substring of that
source, and the quote names the user the way the rules accept a name (a full name, or a first name with
the team). A fact about the note's own writer needs no name, but a writer called maintainer needs
first-person wording in the quote ("I", "me", "my"): a note that only mentions someone else, such as
"Paired with Rohan on the backfill", is not a claim by its author. The surviving facts become the same
Mentions the patterns produce. The model never picks an owner and never sets the points.
"""

import json
import re
from dataclasses import dataclass, field

from owner_agent.proposing.llms import LLMError
from owner_agent.proposing.rules import Mention

PROMPT_VERSION = "text-facts-v3"  # v3: an author's maintainer claim needs first-person wording in the quote
_FIRST_PERSON = re.compile(r"(?<!\w)(I|I've|I'm|me|my|mine)(?!\w)", re.IGNORECASE)

ROLES = {
    "maintainer": "currently maintains, builds, runs or is accountable for this asset",
    "former_maintainer": "used to maintain it but no longer does (left, handed over, moved on)",
    "not_owner": "the text says or clearly implies they do not own it (reader, requester, escalated to someone else)",
    "mentioned": "named, but the text does not say what their role is",
}

SYSTEM_PROMPT = f"""You read short texts from a data catalog and report what they say about people.
You do not decide who owns the asset; another program does that from your facts.

For every person in the roster that a text says something about, output one fact per source:
- user_id: an id from the roster. Never invent ids. A note written in the first person ("I", "me", "my")
  is about that note's author. If a name could mean more than one roster person, or nobody in the roster,
  output no fact for it.
- role: one of
{chr(10).join(f'    {role}: {meaning}' for role, meaning in ROLES.items())}
- source: the id of the source the fact comes from ("description" or a signal id).
- quote: the shortest exact substring of that source that supports the fact, copied character for character.
  It must contain the person's name as written, or the first-person words when it is about the author.

Only report what the text states. Do not use team names, activity or guesses. If a text says nothing
about anyone, output no facts for it.

Reply with JSON only: {{"facts": [{{"user_id": "...", "role": "...", "source": "...", "quote": "..."}}]}}"""


@dataclass
class Extraction:
    mentions: list = field(default_factory=list)
    read: list = field(default_factory=list)  # the sources the model read; empty unless the reply was usable
    called: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    notes: list = field(default_factory=list)


def _normalise(text):
    return " ".join(text.split()).casefold()


class Extractor:
    def __init__(self, client, roster):
        self.client = client
        self.roster = roster
        tokens = {}
        for u in roster.users.values():
            for token in {u.name, *u.name.split()}:
                if len(token) >= 3:
                    tokens.setdefault(token.casefold(), set()).add(u.id)
        self._tokens = tokens
        self._token_pattern = re.compile(
            r"(?<!\w)(" + "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True)) + r")(?!\w)",
            re.IGNORECASE,
        ) if tokens else None

    def _mentioned_users(self, text):
        if not text or not self._token_pattern:
            return set()
        return {uid for m in self._token_pattern.finditer(text) for uid in self._tokens[m.group(1).casefold()]}

    def sources(self, asset, signals, unread):
        """The unread texts, as the model sees them."""
        unread = set(unread)
        sources = []
        if "description" in unread and asset.description:
            sources.append({"source": "description", "text": asset.description})
        for s in signals:
            if s.signal_id in unread and s.note:
                sources.append({"source": s.signal_id, "author": s.user_id, "kind": s.signal_type, "text": s.note})
        return sources

    def _roster(self, sources):
        ids = set()
        for src in sources:
            ids |= self._mentioned_users(src["text"])
            if "author" in src:
                ids.add(src["author"])
        users = self.roster.users
        return [{"user_id": uid, "name": users[uid].name, "team": users[uid].team} for uid in sorted(ids)]

    def extract(self, asset, signals, unread):
        sources = self.sources(asset, signals, unread)
        if not sources:
            return Extraction()
        roster = self._roster(sources)
        payload = {"asset": asset.qualified_name, "sources": sources, "roster": roster}
        try:
            completion = self.client.complete(SYSTEM_PROMPT, json.dumps(payload, indent=1))
        except LLMError as e:
            return Extraction(called=True, error=str(e))
        result = Extraction(
            called=True,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cost_usd=self.client.cost(completion.input_tokens, completion.output_tokens),
        )
        try:
            facts = _parse(completion.text)
        except ValueError as e:
            result.error = f"unreadable model reply: {e}"
            return result
        result.mentions, result.notes = self._mentions(facts, sources, {r["user_id"] for r in roster})
        result.read = [src["source"] for src in sources]
        return result

    def _mentions(self, facts, sources, roster_ids):
        by_source = {src["source"]: src for src in sources}
        kept, dropped = [], 0
        for fact in facts:
            if not isinstance(fact, dict):
                dropped += 1
                continue
            uid, role, source, quote = (fact.get(k) for k in ("user_id", "role", "source", "quote"))
            src = by_source.get(source)
            writer = src.get("author") if src else None
            ok = (
                uid in roster_ids
                and role in ROLES
                and src is not None
                and isinstance(quote, str)
                and quote.strip()
                and _normalise(quote) in _normalise(src["text"])
                and (
                    self.roster.identifies(uid, quote)
                    or (uid == writer and (role != "maintainer" or _FIRST_PERSON.search(quote)))
                )
            )
            if ok:
                kept.append((uid, role, source, writer))
            else:
                dropped += 1

        mentions = []
        for uid, role, source, writer in dict.fromkeys(kept):
            if role == "maintainer":
                kind = "claims" if uid == writer else "names"
            elif role in ("former_maintainer", "not_owner"):
                kind = "disowns" if uid == writer else "rules_out"
            else:
                continue
            problem = None if self.roster.can_own(uid) or kind in ("disowns", "rules_out") else self.roster.why_not(uid)
            mentions.append(Mention(kind, source, writer, self.roster.users[uid].name, None if problem else uid, problem))

        summary = ", ".join(f"{uid} {role} ({source})" for uid, role, source, _ in dict.fromkeys(kept)) or "none"
        notes = [f"Model read {', '.join(by_source)}: {summary}"]
        if dropped:
            notes.append(f"Dropped {dropped} unsupported model fact(s)")
        return mentions, notes


def _parse(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(str(e)) from None
    facts = data.get("facts") if isinstance(data, dict) else None
    if not isinstance(facts, list):
        raise ValueError("missing facts list")
    return facts
