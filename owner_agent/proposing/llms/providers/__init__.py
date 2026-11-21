from owner_agent.proposing.llms.providers import anthropic, openai

PROVIDERS = {"openai": openai.send, "anthropic": anthropic.send}
