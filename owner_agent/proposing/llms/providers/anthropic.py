from owner_agent.proposing.llms.base import Completion, LLMError, post_json

DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"


def send(spec, api_key, system, user):
    body = {
        "model": spec.model,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "max_tokens": spec.max_output_tokens,
    }
    if spec.temperature is not None:
        body["temperature"] = spec.temperature
    url = (spec.base_url or DEFAULT_BASE_URL).rstrip("/") + "/messages"
    headers = {"x-api-key": api_key, "anthropic-version": API_VERSION}
    response = post_json(url, headers, body, spec.timeout_s)
    try:
        text = "".join(block.get("text", "") for block in response["content"] if block.get("type") == "text")
        usage = response.get("usage") or {}
        return Completion(text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)))
    except (KeyError, TypeError) as e:
        raise LLMError(f"unexpected response shape from {spec.name}: {e}") from None
