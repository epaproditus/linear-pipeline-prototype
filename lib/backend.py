"""OpenAI-compatible backend client for pipeline prototype stages."""

from __future__ import annotations

import os
from typing import Any

import httpx


class BackendSettings:
    def __init__(self) -> None:
        self.url = os.getenv("BACKEND_URL", "http://127.0.0.1:8642/v1")
        self.key = os.getenv("BACKEND_KEY", "")
        self.model = os.getenv("MODEL", "hermes-agent")


_settings = BackendSettings()


def chat(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 400,
    temperature: float = 0.2,
    timeout_read: float = 180.0,
) -> str:
    """Simple chat completion — no streaming, no tools."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if _settings.key:
        headers["Authorization"] = f"Bearer {_settings.key}"
    payload: dict[str, Any] = {
        "model": _settings.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    with httpx.Client(timeout=httpx.Timeout(connect=5.0, read=timeout_read, write=10.0, pool=5.0)) as client:
        resp = client.post(f"{_settings.url}/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
    return content.strip()


def agent_chat(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 1000,
    temperature: float = 0.2,
    timeout_read: float = 600.0,
) -> str:
    """Agent-style chat via Hermes API — enables tool use via stream_tool_progress."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if _settings.key:
        headers["Authorization"] = f"Bearer {_settings.key}"
    payload: dict[str, Any] = {
        "model": _settings.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_tool_progress": True,
    }
    with httpx.Client(timeout=httpx.Timeout(connect=5.0, read=timeout_read, write=10.0, pool=5.0)) as client:
        resp = client.post(f"{_settings.url}/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        # Read all streaming chunks and extract final content
        content_parts: list[str] = []
        for line in resp.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            chunk = line.removeprefix("data: ")
            if chunk == "[DONE]":
                break
            import json as _json
            try:
                data = _json.loads(chunk)
                delta = data.get("choices", [{}])[0].get("delta", {})
                if delta.get("content"):
                    content_parts.append(delta["content"])
            except _json.JSONDecodeError:
                continue
    return "".join(content_parts).strip()
