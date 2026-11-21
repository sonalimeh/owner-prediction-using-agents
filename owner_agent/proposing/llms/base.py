import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


class LLMError(Exception):
    pass


@dataclass(frozen=True)
class ModelSpec:
    name: str
    provider: str
    model: str
    api_key_env: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    temperature: float | None = None
    base_url: str | None = None
    max_output_tokens: int = 1024
    timeout_s: float = 60


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int
    output_tokens: int


class LLMClient:
    """One configured model. complete() sends a system and a user message and returns the text."""

    def __init__(self, spec, send):
        self.spec = spec
        self._send = send

    @property
    def name(self):
        return self.spec.name

    def api_key(self):
        key = os.environ.get(self.spec.api_key_env, "").strip()
        if not key:
            raise LLMError(
                f"model {self.spec.name} needs {self.spec.api_key_env}; set it in your environment or a .env file"
            )
        return key

    def check(self):
        self.api_key()

    def complete(self, system, user):
        return self._send(self.spec, self.api_key(), system, user)

    def cost(self, input_tokens, output_tokens):
        return (input_tokens * self.spec.input_usd_per_mtok + output_tokens * self.spec.output_usd_per_mtok) / 1e6


RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


def _retry_after(error):
    try:
        return min(float(error.headers.get("retry-after", "")), 30.0)
    except (TypeError, ValueError):
        return None


def post_json(url, headers, body, timeout_s, attempts=5):
    data = json.dumps(body).encode()
    for attempt in range(attempts):
        request = urllib.request.Request(url, data=data, headers={"content-type": "application/json", **headers})
        delay = 2**attempt
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code not in RETRY_STATUS or attempt == attempts - 1:
                raise LLMError(f"HTTP {e.code} from {url}: {detail}") from None
            delay = _retry_after(e) or delay
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == attempts - 1:
                raise LLMError(f"could not reach {url}: {e}") from None
        time.sleep(delay)
