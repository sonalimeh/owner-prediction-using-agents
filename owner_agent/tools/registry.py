"""Every action as a named tool: checked arguments, one log line per call.

A tool declares its effect:
  read   does not write the database;
  write  writes the database; refused when the command opened it read-only;
  model  calls a model; refused outside propose, the only command allowed to.

call() checks the arguments against the tool's parameters and its effect against the context,
runs the tool, and logs one JSON line: the tool, its arguments, the outcome, how long it took and
the call it ran inside. Logging is off unless OWNER_AGENT_TOOL_LOG names a file to append to
("-" for stderr). The log is a trace for review; the database stays the only record.
"""

import dataclasses
import itertools
import json
import os
import sys
import time
from datetime import datetime, timezone

READ, WRITE, MODEL = "read", "write", "model"
LOG_ENV = "OWNER_AGENT_TOOL_LOG"


class ToolError(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class Param:
    type: type | tuple
    optional: bool = False
    choices: tuple | None = None


@dataclasses.dataclass(frozen=True)
class Tool:
    name: str
    effect: str
    params: dict
    fn: object
    description: str


@dataclasses.dataclass
class Context:
    """What a tool runs against: the open database, the command running, and its model client."""

    conn: object
    command: str
    readonly: bool = False
    client: object = None
    state: dict = dataclasses.field(default_factory=dict)
    stack: list = dataclasses.field(default_factory=list, repr=False)


_TOOLS = {}
_call_ids = itertools.count(1)


def tool(name, effect, **params):
    """Register the decorated function as a tool. It is called as fn(ctx, **args)."""
    if effect not in (READ, WRITE, MODEL):
        raise ValueError(f"unknown effect {effect!r}")

    def register(fn):
        if name in _TOOLS:
            raise ValueError(f"tool {name} registered twice")
        doc = (fn.__doc__ or "").strip().splitlines()
        _TOOLS[name] = Tool(name, effect, params, fn, doc[0] if doc else "")
        return fn

    return register


def catalog():
    """Every registered tool with its effect, description and parameters, for an agent or a reviewer."""
    return [
        {
            "name": t.name,
            "effect": t.effect,
            "description": t.description,
            "params": {
                name: {
                    "type": _type_name(p.type),
                    "optional": p.optional,
                    **({"choices": list(p.choices)} if p.choices else {}),
                }
                for name, p in t.params.items()
            },
        }
        for t in sorted(_TOOLS.values(), key=lambda t: t.name)
    ]


def call(ctx, name, **args):
    t = _TOOLS.get(name)
    record = {
        "ts": _now(),
        "call_id": next(_call_ids),
        "parent": ctx.stack[-1] if ctx.stack else None,
        "command": ctx.command,
        "tool": name,
        "effect": t.effect if t else None,
        "args": {k: _brief(v) for k, v in args.items()},
    }
    started = time.perf_counter()
    ctx.stack.append(record["call_id"])
    try:
        if t is None:
            raise ToolError(f"unknown tool {name}")
        _check(ctx, t, args)
        result = t.fn(ctx, **args)
    except BaseException as e:
        _log({**record, "ok": False, "error": f"{type(e).__name__}: {e}", "ms": _ms(started)})
        raise
    finally:
        ctx.stack.pop()
    _log({**record, "ok": True, "result": _brief(result), "ms": _ms(started)})
    return result


def _check(ctx, t, args):
    if t.effect == MODEL and ctx.command != "propose":
        raise ToolError(f"{t.name} calls a model, and only propose may; refused in {ctx.command}")
    if t.effect == WRITE and ctx.readonly:
        raise ToolError(f"{t.name} writes, and {ctx.command} opened the database read-only")
    unknown = sorted(set(args) - set(t.params))
    if unknown:
        raise ToolError(f"{t.name}: unknown argument(s) {', '.join(unknown)}")
    for name, p in t.params.items():
        value = args.get(name)
        if value is None:
            if not p.optional:
                raise ToolError(f"{t.name}: {name} is required")
            continue
        if not _is_type(value, p.type):
            raise ToolError(f"{t.name}: {name} must be {_type_name(p.type)}, got {type(value).__name__}")
        if p.choices is not None and value not in p.choices:
            raise ToolError(f"{t.name}: {name} must be one of {', '.join(map(str, p.choices))}, got {value!r}")


def _is_type(value, types):
    types = types if isinstance(types, tuple) else (types,)
    if isinstance(value, bool):
        return bool in types
    if isinstance(value, int) and float in types:
        return True
    return isinstance(value, types)


def _type_name(types):
    types = types if isinstance(types, tuple) else (types,)
    return " or ".join(t.__name__ for t in types)


def _brief(value, depth=0):
    """A short JSON-able summary of a value for the log: long text cut, big collections counted."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 120 else value[:117] + "..."
    name = type(value).__name__
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
    if depth >= 2:
        return f"<{name}>"
    if isinstance(value, dict):
        if len(value) > 12:
            return {"count": len(value)}
        return {str(k): _brief(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        value = sorted(value, key=str)
    if isinstance(value, (list, tuple)):
        if len(value) > 5:
            return {"count": len(value)}
        return [_brief(v, depth + 1) for v in value]
    return f"<{name}>"


def _log(record):
    target = os.environ.get(LOG_ENV)
    if not target:
        return
    line = json.dumps(record, sort_keys=True, default=str) + "\n"
    if target == "-":
        sys.stderr.write(line)
        return
    with open(target, "a", encoding="utf-8") as f:
        f.write(line)


def _ms(started):
    return round((time.perf_counter() - started) * 1000, 3)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
