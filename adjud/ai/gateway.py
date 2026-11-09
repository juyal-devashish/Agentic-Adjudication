"""The only module that talks to the Anthropic API (AGENTS.md, plan step 12).

    Gateway(store, cfg, replay_dir).run(case_id, FUNCTION, input, toolbox) -> AiResult

Modes (`study.yaml` → `ai.mode`, plan P-27):
  off     no call; the step continues without AI (people handle the judgement)
  replay  the recorded output for this exact input (function, prompt version, model,
          input) from `<config>/<study>/ai_replays/<function>/<input hash>.json`; outputs
          written by hand are marked `simulated` and shown as "Simulated AI"
  live    the API, with a manual tool loop over local tools. Needs ANTHROPIC_API_KEY in
          the environment; without it the gateway runs as `off` and says so

Every call (replay or live, valid or not) is saved to `cases/<id>/ai/` and referenced by
an `ai.called` event, and its tokens and cost go to the hash-chained ledger
`data/ai/usage.jsonl`. Live calls stop when the ledger reaches `ai.budget_usd`.
A schema-invalid answer is re-asked up to `ai.max_retries` times; then the step continues
without AI. Nothing here moves a case: the gates and the runner do.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from adjud.ai.config import AiConfig
from adjud.ai.functions import AgentFunction
from adjud.ai.tools import Toolbox, ToolError
from adjud.chain import ChainedLog
from adjud.events import EventDraft, canonical_json
from adjud.store import FileCaseStore, json_bytes

log = logging.getLogger("adjud.ai")
ACTOR = "system:ai-gateway"


@dataclass
class AiResult:
    function: str
    mode: str  # off | replay | live
    status: str  # ok | unavailable | invalid | error
    output: dict[str, Any] | None = None
    simulated: bool = False
    note: str = ""
    call: dict[str, str] | None = None  # {"path", "sha256"} of the saved call record
    cost_usd: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def label(self) -> str:
        """How the UI names the source of a result."""
        return "Simulated AI" if self.simulated else "AI"


def input_hash(function: AgentFunction, model: str, payload: dict[str, Any]) -> str:
    key = {"function": function.prompt_id, "model": model, "input": payload}
    return hashlib.sha256(canonical_json(key).encode()).hexdigest()


def _usage_dict(usage: Any) -> dict[str, int]:
    keys = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    return {k: int(getattr(usage, k, 0) or 0) for k in keys}


@dataclass
class Gateway:
    store: FileCaseStore
    cfg: AiConfig
    replay_dir: Path
    client: Any = None  # an anthropic.Anthropic; built lazily in live mode
    env: Mapping[str, str] = field(default_factory=lambda: os.environ)
    # replay inputs with no recorded output: (function, input hash, input), for tools that
    # record or simulate answers (tools/simulate_ai.py)
    missing: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    @classmethod
    def for_study(
        cls, store: FileCaseStore, config_dir: Path, study: str = "DEMO01", **kw: Any
    ) -> Gateway:
        """Settings from `<config>/<study>/study.yaml`; replays from its `ai_replays/`.
        `ADJUD_AI_MODE` in the environment overrides the mode (off / replay / live)."""
        import yaml

        folder = Path(config_dir) / study
        raw = dict((yaml.safe_load((folder / "study.yaml").read_text()) or {}).get("ai") or {})
        if os.environ.get("ADJUD_AI_MODE"):  # operational override, e.g. a replay demo
            raw["mode"] = os.environ["ADJUD_AI_MODE"]
        cfg = AiConfig.model_validate(raw)
        return cls(store, cfg, folder / "ai_replays", **kw)

    @property
    def ledger(self) -> ChainedLog:
        return ChainedLog(self.store.paths.ai / "usage.jsonl", clock=self.store.clock)

    def spent_usd(self) -> float:
        return sum(float(r.get("cost_usd", 0)) for r in self.ledger.records())

    # -- entry point -------------------------------------------------------------------------

    def run(
        self,
        case_id: str,
        function: AgentFunction,
        payload: dict[str, Any],
        toolbox: Toolbox,
        images: list[bytes] | None = None,
    ) -> AiResult:
        """`images`: PNG pages sent with the request (AI-1). Their SHA-256 must be in
        `payload` so that the input hash, and therefore replay, covers them."""
        if not self.cfg.enabled(function.name):
            return AiResult(function.name, "off", "unavailable", note="AI is off")
        h = input_hash(function, self.cfg.model, payload)
        if self.cfg.mode == "replay":
            return self._replay(case_id, function, payload, h)
        if not self.env.get("ANTHROPIC_API_KEY") and self.client is None:
            return AiResult(
                function.name, "off", "unavailable",
                note="no API key in the environment; continuing without AI",
            )  # fmt: skip
        if self.spent_usd() >= self.cfg.budget_usd:
            return AiResult(
                function.name, "off", "unavailable",
                note=f"AI budget of ${self.cfg.budget_usd:.2f} reached; continuing without AI",
            )  # fmt: skip
        return self._live(case_id, function, payload, h, toolbox, images or [])

    # -- replay ------------------------------------------------------------------------------

    def _replay(
        self, case_id: str, function: AgentFunction, payload: dict[str, Any], h: str
    ) -> AiResult:
        path = self.replay_dir / function.name / f"{h}.json"
        if not path.exists():
            self.missing.append((function.name, h, payload))
            return AiResult(
                function.name, "replay", "unavailable",
                note="no recorded output for this input; continuing without AI",
            )  # fmt: skip
        rec = json.loads(path.read_text())
        simulated = bool(rec.get("simulated", True))
        try:
            output = function.output_model.model_validate(rec["output"]).model_dump()
            status, note = "ok", ""
        except ValidationError as exc:
            output, status, note = None, "invalid", f"recorded output invalid: {exc.error_count()}"
        record = {
            "function": function.name,
            "prompt": function.prompt_id,
            "mode": "replay",
            "simulated": simulated,
            "model": rec.get("model", self.cfg.model),
            "input_hash": h,
            "input": payload,
            "output": output,
            "valid": status == "ok",
            "tool_calls": rec.get("tool_calls", []),
            "usage": {},
            "cost_usd": 0.0,
            "latency_ms": 0,
            "note": note,
        }
        result = AiResult(function.name, "replay", status, output, simulated, note)
        return self._save(case_id, record, result)

    # -- live --------------------------------------------------------------------------------

    def _client(self) -> Any:
        if self.client is None:
            import anthropic  # imported here: the rest of the package never needs the SDK

            self.client = anthropic.Anthropic(
                api_key=self.env["ANTHROPIC_API_KEY"],
                timeout=self.cfg.timeout_seconds,
                max_retries=2,
            )
        return self.client

    def _live(
        self,
        case_id: str,
        function: AgentFunction,
        payload: dict[str, Any],
        h: str,
        toolbox: Toolbox,
        images: list[bytes],
    ) -> AiResult:
        import anthropic

        client = self._client()
        content: list[dict[str, Any]] = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.standard_b64encode(img).decode()}}
            for img in images
        ]  # fmt: skip
        content.append({"type": "text", "text": json.dumps(payload, ensure_ascii=False)})
        messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
        usage = dict.fromkeys(
            ("input_tokens", "output_tokens", "cache_creation_input_tokens",
             "cache_read_input_tokens"), 0,
        )  # fmt: skip
        tool_calls: list[dict[str, Any]] = []
        retries, requests = 0, 0
        status, output, note = "error", None, ""
        started = time.monotonic()
        try:
            while True:
                if requests >= self.cfg.max_tool_turns + self.cfg.max_retries + 1:
                    note = "too many turns without an answer"
                    break
                requests += 1
                tools = toolbox.definitions()
                response = client.messages.create(
                    model=self.cfg.model,
                    max_tokens=self.cfg.max_tokens,
                    system=function.system_prompt(),
                    messages=messages,
                    **({"tools": tools} if tools else {}),
                    output_config={
                        "effort": self.cfg.effort,
                        "format": {"type": "json_schema", "schema": function.output_schema()},
                    },
                )
                for k, v in _usage_dict(response.usage).items():
                    usage[k] += v
                if response.stop_reason == "refusal":
                    note = "the model declined the request"
                    break
                if response.stop_reason == "max_tokens":
                    note = "the answer was cut off (max_tokens)"
                    break
                messages.append({"role": "assistant", "content": response.content})
                uses = [b for b in response.content if b.type == "tool_use"]
                if uses:
                    results = self._run_tools(uses, toolbox, tool_calls)
                    messages.append({"role": "user", "content": results})
                    continue
                text = next((b.text for b in response.content if b.type == "text"), "")
                try:
                    output = function.output_model.model_validate_json(text).model_dump()
                    status = "ok"
                    break
                except ValidationError as exc:
                    if retries >= self.cfg.max_retries:
                        status, note = "invalid", "answer did not match the schema after retries"
                        break
                    retries += 1
                    messages.append({"role": "user", "content": (
                        "Your answer did not match the response schema: "
                        f"{exc.errors(include_url=False)[:3]}. Reply with the JSON object only."
                    )})  # fmt: skip
        except anthropic.APIConnectionError:
            note = "could not reach the API"
        except anthropic.RateLimitError:
            note = "rate limited by the API"
        except anthropic.APIStatusError as exc:
            note = f"API error {exc.status_code}"
        cost = self.cfg.cost(usage)
        record = {
            "function": function.name,
            "prompt": function.prompt_id,
            "mode": "live",
            "simulated": False,
            "model": self.cfg.model,
            "input_hash": h,
            "input": payload,
            "output": output,
            "valid": status == "ok",
            "tool_calls": tool_calls,
            "usage": usage,
            "requests": requests,
            "cost_usd": round(cost, 6),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "note": note,
        }
        if status != "ok":
            log.warning("AI %s on %s: %s", function.name, case_id, note or status)
        result = AiResult(function.name, "live", status, output, False, note, cost_usd=cost)
        return self._save(case_id, record, result)

    @staticmethod
    def _run_tools(
        uses: list[Any], toolbox: Toolbox, tool_calls: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Run every tool call of one turn; all results go back in a single message."""
        results = []
        for use in uses:
            args = dict(use.input or {})
            try:
                content, is_error = toolbox.call(use.name, args), False
            except (ToolError, KeyError, ValueError, TypeError) as exc:
                content, is_error = f"Error: {exc}", True
            tool_calls.append({
                "tool": use.name, "input": args, "error": is_error,
                "result_sha256": hashlib.sha256(content.encode()).hexdigest(),
            })  # fmt: skip
            results.append({"type": "tool_result", "tool_use_id": use.id, "content": content,
                            "is_error": is_error})  # fmt: skip
        return results

    # -- records -----------------------------------------------------------------------------

    def _save(self, case_id: str, record: dict[str, Any], result: AiResult) -> AiResult:
        folder = self.store.case_dir(case_id) / "ai"
        n = len(list(folder.glob("*.json"))) + 1 if folder.exists() else 1
        rel = f"ai/{n:04d}_{record['function']}.json"
        sha = self.store.put_file(case_id, rel, json_bytes(record))
        result.call = {"path": rel, "sha256": sha}
        self.store.append_event(
            case_id,
            EventDraft(
                type="ai.called",
                actor=ACTOR,
                data={
                    "function": record["function"],
                    "prompt": record["prompt"],
                    "mode": record["mode"],
                    "simulated": record["simulated"],
                    "valid": record["valid"],
                    "cost_usd": record["cost_usd"],
                    "call": result.call,
                },
            ),
        )
        self.ledger.append({
            "case_id": case_id, "function": record["function"], "mode": record["mode"],
            "model": record["model"], "valid": record["valid"], "usage": record["usage"],
            "cost_usd": record["cost_usd"],
        })  # fmt: skip
        return result
