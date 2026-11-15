"""Ollama adapter: local models through Ollama's native /api/chat endpoint (stdlib only, no cost)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any, Literal

from myriad.llm.actions import (
    decision_from_text,
    decision_from_tool_call,
    text_from_decision,
    tool_call_from_decision,
)
from myriad.llm.base import Completion, ProviderError
from myriad.models import DECISION_ADAPTER, ENGINE_PREFIX, ChatMessage, parse_decision
from myriad.resources.tokens import estimate_messages, estimate_tokens

Transport = Callable[[str, dict[str, Any], float], dict[str, Any]]
"""(url, JSON payload, timeout in seconds) -> parsed JSON response. Replaceable in tests."""


def _http_post(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def decision_schema() -> dict[str, Any]:
    """JSON schema of the decision union for Ollama's `format` (constrained decoding).

    Pydantic emits `oneOf` plus a `discriminator`; grammar converters handle `anyOf` more
    reliably and ignore discriminators, so both are normalized here.
    """

    def convert(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                ("anyOf" if key == "oneOf" else key): convert(value)
                for key, value in node.items()
                if key != "discriminator"
            }
        if isinstance(node, list):
            return [convert(value) for value in node]
        return node

    return convert(DECISION_ADAPTER.json_schema())


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8")
        return str(json.loads(body).get("error", body))[:300]
    except Exception:  # the body is optional and may be anything
        return str(exc.reason or "")


class OllamaProvider:
    name = "ollama"
    simulated = False

    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://127.0.0.1:11434",
        context_window: int = 8192,
        timeout: float = 180.0,
        decision_mode: Literal["json", "tools"] = "json",
        transport: Transport | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.context_window = context_window
        self.timeout = timeout
        self.decision_mode = decision_mode
        self._transport = transport or _http_post
        self._schema = decision_schema()

    def complete(
        self,
        messages: Sequence[ChatMessage],
        max_output_tokens: int,
        temperature: float | None = None,
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        think: bool = False,
        thinking_budget: int = 0,
    ) -> Completion:
        options: dict[str, Any] = {
            "num_ctx": self.context_window,  # otherwise Ollama silently uses a small window
            "num_predict": max_output_tokens + (thinking_budget if think else 0),
        }
        if temperature is not None:
            options["temperature"] = temperature
        payload: dict[str, Any] = {"model": self.model, "stream": False, "think": think, "options": options}
        if self.decision_mode == "tools":
            payload["messages"] = self._tool_mode_messages(messages)
            payload["tools"] = list(tools or [])
        else:
            payload["messages"] = [{"role": m.role, "content": m.content} for m in messages]
            payload["format"] = self._schema  # constrained decoding to the decision schema

        data = self._post("/api/chat", payload)
        try:
            message = data["message"]
            content = message.get("content") or ""
        except (KeyError, TypeError, AttributeError) as exc:
            raise ProviderError(f"unexpected response from Ollama: {str(data)[:200]}") from exc

        visible = content
        if self.decision_mode == "tools":
            calls = message.get("tool_calls") or []
            if calls:  # exactly one decision per step: the first call wins
                function = calls[0].get("function", {})
                text = decision_from_tool_call(function.get("name", ""), function.get("arguments"))
                visible = json.dumps(function.get("arguments"), ensure_ascii=False)
            else:
                text = decision_from_text(content) if content.strip() else content
        else:
            text = content

        prompt_tokens, output_tokens = data.get("prompt_eval_count"), data.get("eval_count")
        return Completion(
            text=text,
            input_tokens=prompt_tokens if prompt_tokens is not None else estimate_messages(messages),
            output_tokens=output_tokens if output_tokens is not None else estimate_tokens(text),
            estimated=prompt_tokens is None or output_tokens is None,
            simulated=False,
            truncated=data.get("done_reason") == "length",
            answer_tokens=estimate_tokens(visible) if think else None,
        )

    @staticmethod
    def _tool_mode_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
        """Render the history the way tool-trained models expect it: earlier delegations and
        tool calls as assistant tool calls, the engine's feedback on them as tool results."""
        rendered: list[dict[str, Any]] = []
        after_tool_call = False
        for message in messages:
            if message.role == "assistant":
                decision, _ = parse_decision(message.content)
                call = tool_call_from_decision(decision) if decision is not None else None
                if call is not None:
                    rendered.append({"role": "assistant", "content": "", "tool_calls": [{"function": call}]})
                    after_tool_call = True
                    continue
                text = text_from_decision(decision) if decision is not None else message.content
                rendered.append({"role": "assistant", "content": text})
            elif after_tool_call and message.from_engine and message.content.startswith(ENGINE_PREFIX):
                rendered.append({"role": "tool", "content": message.content})
            else:
                rendered.append({"role": message.role, "content": message.content})
            after_tool_call = False
        return rendered

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._transport(self.base_url + path, payload, self.timeout)
        except urllib.error.HTTPError as exc:
            detail = _error_detail(exc)
            if exc.code == 404 and "not found" in detail.lower():
                raise ProviderError(
                    f"model '{self.model}' is not installed - run: ollama pull {self.model}"
                ) from exc
            raise ProviderError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
        except TimeoutError as exc:
            raise ProviderError(f"Ollama did not answer within {self.timeout:.0f} s") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ProviderError(f"Ollama did not answer within {self.timeout:.0f} s") from exc
            raise ProviderError(
                f"Ollama is not reachable at {self.base_url} - is it running? ({exc.reason})"
            ) from exc
        except OSError as exc:
            raise ProviderError(f"connection to Ollama failed: {exc}") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderError("Ollama sent an invalid response") from exc
