"""Minimal client for any OpenAI-compatible Chat Completions API.

Works with OpenAI, vLLM, Ollama, LM Studio, llama.cpp server, etc.
Configuration (environment variables or arguments):
    LLM_BASE_URL  e.g. https://api.groq.com/openai/v1, http://localhost:11434/v1 (Ollama)
    LLM_API_KEY   optional for local servers
    LLM_MODEL     e.g. qwen/qwen3.8-27b, openai/gpt-oss-120b, qwen2.5:7b
"""
from __future__ import annotations

import os
import time
from typing import Protocol

import requests


class ChatClient(Protocol):
    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Return the assistant message (dict with 'content' and optional 'tool_calls')."""


class OpenAICompatibleClient:
    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str | None = None, temperature: float = 0.0, timeout: int = 120,
                 max_retries: int = 5):
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")).rstrip("/")
        self.model = model or os.environ.get("LLM_MODEL", "qwen2.5:7b")
        self.api_key = api_key if api_key is not None else os.environ.get("LLM_API_KEY", "")
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        body = {"model": self.model, "messages": messages, "temperature": self.temperature}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        for attempt in range(self.max_retries + 1):
            r = requests.post(f"{self.base_url}/chat/completions", json=body, headers=headers, timeout=self.timeout)
            # Rate limits (429) and transient server errors are retried, honouring Retry-After.
            if r.status_code not in (429, 500, 502, 503) or attempt == self.max_retries:
                break
            wait = float(r.headers.get("retry-after") or 2 ** (attempt + 1))
            time.sleep(min(wait, 60))
        r.raise_for_status()
        return r.json()["choices"][0]["message"]
