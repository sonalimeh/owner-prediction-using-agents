"""LLM registry. Models are declared in models.toml; API keys come from the environment or .env."""

import os
import tomllib
from dataclasses import fields
from pathlib import Path

from owner_agent.proposing.llms.base import Completion, LLMClient, LLMError, ModelSpec
from owner_agent.proposing.llms.providers import PROVIDERS

__all__ = ["Completion", "LLMClient", "LLMError", "ModelSpec", "get_client", "load_dotenv", "load_registry"]

MODELS_FILE = Path(__file__).with_name("models.toml")


def load_dotenv(path=".env"):
    """Set variables from a KEY=VALUE file without overriding ones already in the environment."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.removeprefix("export ").split("=", 1)
            key, value = key.strip(), value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def load_registry(path=None):
    path = Path(path or os.environ.get("OWNER_AGENT_MODELS") or MODELS_FILE)
    try:
        with open(path, "rb") as f:
            config = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise LLMError(f"cannot read model registry {path}: {e}") from None
    allowed = {f.name for f in fields(ModelSpec)} - {"name"}
    specs = {}
    for name, entry in config.get("models", {}).items():
        unknown = set(entry) - allowed
        if unknown:
            raise LLMError(f"model {name} in {path} has unknown keys: {', '.join(sorted(unknown))}")
        if entry.get("provider") not in PROVIDERS:
            raise LLMError(f"model {name} in {path} has unknown provider {entry.get('provider')!r}")
        try:
            specs[name] = ModelSpec(name=name, **entry)
        except TypeError as e:
            raise LLMError(f"model {name} in {path}: {e}") from None
    return config.get("default"), specs


def get_client(name=None, registry_path=None):
    default, specs = load_registry(registry_path)
    name = name or os.environ.get("OWNER_AGENT_MODEL") or default
    if name not in specs:
        raise LLMError(f"unknown model {name!r}; available: {', '.join(sorted(specs)) or 'none'}")
    spec = specs[name]
    return LLMClient(spec, PROVIDERS[spec.provider])
