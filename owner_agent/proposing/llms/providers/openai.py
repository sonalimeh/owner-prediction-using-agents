from owner_agent.proposing.llms.base import Completion, LLMError, post_json

DEFAULT_BASE_URL = "https://api.openai.com/v1"


def send(spec, api_key, system, user):
    body = {
        "model": spec.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "max_completion_tokens": spec.max_output_tokens,
    }
    if spec.temperature is not None:
        body["temperature"] = spec.temperature
    url = (spec.base_url or DEFAULT_BASE_URL).rstrip("/") + "/chat/completions"
    response = post_json(url, {"authorization": f"Bearer {api_key}"}, body, spec.timeout_s)
    try:
        text = response["choices"][0]["message"]["content"] or ""
        usage = response.get("usage") or {}
        return Completion(text, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)))
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"unexpected response shape from {spec.name}: {e}") from None
