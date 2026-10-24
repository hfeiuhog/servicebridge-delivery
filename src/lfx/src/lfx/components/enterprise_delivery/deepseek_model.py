"""Small operator-configured DeepSeek adapter; no API keys enter flow data."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import httpx


class DeepSeekModel:
    def __init__(self, api_key: str, model: str = "deepseek-flash", *, transport=None):
        if not api_key:
            raise ValueError("Provision DEEPSEEK_API_KEY in the operator environment")
        if model not in {"deepseek-flash", "deepseek-v4-pro"}:
            raise ValueError("Use an operator-approved DeepSeek model")
        self._key, self.model, self._transport = api_key, model, transport

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("DEEPSEEK_API_KEY", ""), os.environ.get("SERVICEBRIDGE_MODEL", "deepseek-flash"))

    def with_structured_output(self, schema, *, include_raw=True):
        return _StructuredDeepSeek(self, schema, include_raw)


class _StructuredDeepSeek:
    def __init__(self, model, schema, include_raw):
        self.model, self.schema, self.include_raw = model, schema, include_raw

    async def ainvoke(self, messages):
        prompt = [
            {"role": {"human": "user", "ai": "assistant"}.get(role, role), "content": content}
            for role, content in messages
        ]
        prompt[0]["content"] += "\nReturn one JSON object matching this JSON schema:\n" + json.dumps(
            self.schema.model_json_schema()
        )
        async with httpx.AsyncClient(timeout=25, trust_env=False, transport=self.model._transport) as client:
            response = await client.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": "Bearer " + self.model._key},
                json={
                    "model": self.model.model,
                    "messages": prompt,
                    "response_format": {"type": "json_object"},
                    "thinking": {"type": "disabled"},
                    "max_tokens": 1536,
                },
            )
            if response.status_code != 200:
                raise ValueError("Model provider rejected the request")
            data = response.json()
        choice = data["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("Model response was incomplete")
        parsed = self.schema.model_validate_json(choice["message"]["content"])
        usage = data.get("usage", {})
        raw = SimpleNamespace(
            usage_metadata={
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            }
        )
        return {"parsed": parsed, "raw": raw, "parsing_error": None} if self.include_raw else parsed

    def invoke(self, messages):
        """Synchronous CLI path uses the same schema and provider settings."""
        prompt = [
            {"role": {"human": "user", "ai": "assistant"}.get(role, role), "content": content}
            for role, content in messages
        ]
        prompt[0]["content"] += "\nReturn one JSON object matching this JSON schema:\n" + json.dumps(
            self.schema.model_json_schema()
        )
        with httpx.Client(timeout=25, trust_env=False, transport=self.model._transport) as client:
            response = client.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": "Bearer " + self.model._key},
                json={
                    "model": self.model.model,
                    "messages": prompt,
                    "response_format": {"type": "json_object"},
                    "thinking": {"type": "disabled"},
                    "max_tokens": 1536,
                },
            )
            if response.status_code != 200:
                raise ValueError("Model provider rejected the request")
            data = response.json()
        choice = data["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("Model response was incomplete")
        parsed = self.schema.model_validate_json(choice["message"]["content"])
        usage = data.get("usage", {})
        raw = SimpleNamespace(
            usage_metadata={
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            }
        )
        return {"parsed": parsed, "raw": raw, "parsing_error": None} if self.include_raw else parsed
