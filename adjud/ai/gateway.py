"""The only module that talks to the Anthropic API (AGENTS.md, plan step 12).

    Gateway(store, cfg, replay_dir).run(case_id, FUNCTION, input, toolbox) -> AiResult
    Gateway(store, cfg, replay_dir).converse(context, messages, toolbox) -> AgentReply

Modes (`study.yaml` → `ai.mode`, plan P-27; per function `ai.modes`, P-58):
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

The Slack Agent (P-58) is a conversation, not a JSON function: `converse` sends the
conversation so far with the person it acts for, runs the same tool loop over that
person's toolbox and returns the reply text and the tools it called. A turn about a case
is saved like any call (`cases/<id>/ai/`, `ai.called`); a turn about no case is saved to
`data/ai/calls/<date>/`, referenced by path and SHA-256 from its ledger line, which
`tools.verify_log` checks. Replay is keyed by the input hash of the whole turn (prompt
version, model, the person, the messages, the tool definitions); a recorded turn's tool
calls are run again through the toolbox, so its actions happen and the role limits of
the tools still apply.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from adjud.ai.config import SLACK_AGENT_NAME, AiConfig
from adjud.ai.functions import SLACK_AGENT, AgentFunction
from adjud.ai.tools import Toolbox, ToolError
from adjud.chain import ChainedLog
from adjud.events import EventDraft, canonical_json, format_time, sha256_bytes
from adjud.store import FileCaseStore, json_bytes, write_atomic

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


class ChatMessage(BaseModel):
    """One message of a conversation with the Slack Agent, as text (P-58)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    role: Literal["user", "assistant"]
    text: str


class AgentContext(BaseModel):
    """Who the Slack Agent acts for, and the case the conversation is about, if any."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    person: str  # user id in users.yaml
    name: str
    role: str  # role id in users.yaml, e.g. site, safety_specialist
    site: str | None = None
    case_id: str | None = None

    def prompt_text(self) -> str:
        """The part of the system prompt that changes with the conversation."""
        who = f"{self.name} (user `{self.person}`), role `{self.role}`"
        lines = ["## This conversation", "", f"- You act for: {who}"]
        if self.site:
            lines.append(f"- Their site: {self.site}")
        lines.append(
            f"- About case: {self.case_id}" if self.case_id else "- About no particular case"
        )
        return "\n".join(lines) + "\n"


@dataclass
class AgentReply:
    """The Slack Agent's answer to one turn. `status` unavailable: no AI this time (off,
    no key, budget reached, nothing recorded); the caller answers without it."""

    mode: str  # off | replay | live
    status: str  # ok | unavailable | error
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    simulated: bool = False
    note: str = ""
    call: dict[str, str] | None = None  # the saved call record, as for AiResult
    cost_usd: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def label(self) -> str:
        return "Simulated AI" if self.simulated else "AI"


def conversation_hash(
    function: AgentFunction,
    model: str,
    context: AgentContext,
    messages: list[ChatMessage],
    tools: list[dict[str, Any]],
) -> str:
    """Input hash of one Agent turn: replay finds the recorded answer by it."""
    return hashlib.sha256(canonical_json(_conversation_input(
        function, model, context, messages, tools)).encode()).hexdigest()  # fmt: skip


def _conversation_input(
    function: AgentFunction,
    model: str,
    context: AgentContext,
    messages: list[ChatMessage],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "function": function.prompt_id,
        "model": model,
        "context": context.model_dump(),
        "messages": [m.model_dump() for m in messages],
        "tools": tools,
    }


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
        `ADJUD_AI_MODE` in the environment overrides the master mode (off / replay / live);
        `ADJUD_AGENT_MODE` overrides the Slack Agent's own mode (P-58)."""
        import yaml

        folder = Path(config_dir) / study
        raw = dict((yaml.safe_load((folder / "study.yaml").read_text()) or {}).get("ai") or {})
        if os.environ.get("ADJUD_AI_MODE"):  # operational override, e.g. a replay demo
            raw["mode"] = os.environ["ADJUD_AI_MODE"]
        if os.environ.get("ADJUD_AGENT_MODE"):  # e.g. the Agent live, QC on replay
            raw["modes"] = {
                **(raw.get("modes") or {}),
                SLACK_AGENT_NAME: os.environ["ADJUD_AGENT_MODE"],
            }
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
        if function.output_model is None:
            raise ValueError(f"{function.name} is a conversation: use converse()")
        if not self.cfg.enabled(function.name):
            return AiResult(function.name, "off", "unavailable", note="AI is off")
        h = input_hash(function, self.cfg.model, payload)
        if self.cfg.mode_for(function.name) == "replay":
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

            # A key that is not scoped to a workspace must name one on every request
            workspace = self.env.get("ANTHROPIC_WORKSPACE_ID")
            self.client = anthropic.Anthropic(
                api_key=self.env["ANTHROPIC_API_KEY"],
                timeout=self.cfg.timeout_seconds,
                max_retries=2,
                default_headers={"anthropic-workspace-id": workspace} if workspace else None,
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
        except anthropic.APIError as exc:
            note = _api_error_note(exc)
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

    # -- the Slack Agent (P-58) ------------------------------------------------------------

    def converse(
        self, context: AgentContext, messages: list[ChatMessage], toolbox: Toolbox
    ) -> AgentReply:
        """One turn of the Agent: `messages` is the conversation so far, ending with the
        person's message. Never raises for AI trouble: the reply says what happened."""
        fn = SLACK_AGENT
        if not messages or messages[-1].role != "user":
            raise ValueError("a conversation turn ends with the person's message")
        mode = self.cfg.mode_for(fn.name)
        if not self.cfg.enabled(fn.name):
            return AgentReply("off", "unavailable", note="the Agent's AI is off")
        tools = toolbox.definitions()
        payload = _conversation_input(fn, self.cfg.model, context, messages, tools)
        h = conversation_hash(fn, self.cfg.model, context, messages, tools)
        if mode == "replay":
            return self._converse_replay(fn, context, payload, h, toolbox)
        if not self.env.get("ANTHROPIC_API_KEY") and self.client is None:
            return AgentReply("off", "unavailable", note="no API key in the environment")
        if self.spent_usd() >= self.cfg.budget_usd:
            return AgentReply(
                "off", "unavailable", note=f"AI budget of ${self.cfg.budget_usd:.2f} reached"
            )
        return self._converse_live(fn, context, messages, payload, h, toolbox)

    def _converse_replay(
        self,
        fn: AgentFunction,
        context: AgentContext,
        payload: dict[str, Any],
        h: str,
        toolbox: Toolbox,
    ) -> AgentReply:
        path = self.replay_dir / fn.name / f"{h}.json"
        if not path.exists():
            self.missing.append((fn.name, h, payload))
            return AgentReply("replay", "unavailable", note="no recorded answer for this turn")
        rec = json.loads(path.read_text())
        simulated = bool(rec.get("simulated", True))
        tool_calls: list[dict[str, Any]] = []
        uses = [
            _RecordedUse(f"replay_{i}", c["tool"], c.get("input") or {})
            for i, c in enumerate(rec.get("tool_calls", []))
        ]
        if uses:
            self._run_tools(uses, toolbox, tool_calls)
        text = str(rec.get("text") or "")
        status, note = ("ok", "") if text.strip() else ("error", "recorded answer has no text")
        record = {
            "function": fn.name,
            "prompt": fn.prompt_id,
            "mode": "replay",
            "simulated": simulated,
            "model": rec.get("model", self.cfg.model),
            "input_hash": h,
            "input": payload,
            "output": {"text": text},
            "valid": status == "ok",
            "tool_calls": tool_calls,
            "usage": {},
            "cost_usd": 0.0,
            "latency_ms": 0,
            "note": note,
        }
        reply = AgentReply("replay", status, text, tool_calls, simulated, note)
        reply.call = self._record(context.case_id, record)
        return reply

    def _converse_live(
        self,
        fn: AgentFunction,
        context: AgentContext,
        messages: list[ChatMessage],
        payload: dict[str, Any],
        h: str,
        toolbox: Toolbox,
    ) -> AgentReply:
        import anthropic

        client = self._client()
        system = [
            {"type": "text", "text": fn.system_prompt()},
            {"type": "text", "text": context.prompt_text()},
        ]
        convo: list[dict[str, Any]] = [{"role": m.role, "content": m.text} for m in messages]
        usage = dict.fromkeys(
            ("input_tokens", "output_tokens", "cache_creation_input_tokens",
             "cache_read_input_tokens"), 0,
        )  # fmt: skip
        tool_calls: list[dict[str, Any]] = []
        requests, status, text, note = 0, "error", "", ""
        started = time.monotonic()
        try:
            while True:
                if requests > self.cfg.max_tool_turns:
                    note = "too many tool turns without an answer"
                    break
                requests += 1
                tools = toolbox.definitions()
                response = client.messages.create(
                    model=self.cfg.model,
                    max_tokens=self.cfg.max_tokens,
                    system=system,
                    messages=convo,
                    **({"tools": tools} if tools else {}),
                    output_config={"effort": self.cfg.effort},
                )
                for k, v in _usage_dict(response.usage).items():
                    usage[k] += v
                if response.stop_reason == "refusal":
                    note = "the model declined the request"
                    break
                if response.stop_reason == "max_tokens":
                    note = "the answer was cut off (max_tokens)"
                    break
                # the whole content goes back, thinking blocks included (preserved thinking)
                convo.append({"role": "assistant", "content": response.content})
                uses = [b for b in response.content if b.type == "tool_use"]
                if uses:
                    convo.append(
                        {"role": "user", "content": self._run_tools(uses, toolbox, tool_calls)}
                    )
                    continue
                text = "\n\n".join(
                    b.text.strip() for b in response.content if b.type == "text" and b.text.strip()
                )
                status, note = ("ok", "") if text else ("error", "the answer had no text")
                break
        except anthropic.APIError as exc:
            note = _api_error_note(exc)
        cost = self.cfg.cost(usage)
        record = {
            "function": fn.name,
            "prompt": fn.prompt_id,
            "mode": "live",
            "simulated": False,
            "model": self.cfg.model,
            "input_hash": h,
            "input": payload,
            "output": {"text": text} if status == "ok" else None,
            "valid": status == "ok",
            "tool_calls": tool_calls,
            "usage": usage,
            "requests": requests,
            "cost_usd": round(cost, 6),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "note": note,
        }
        if status != "ok":
            log.warning("AI %s for %s: %s", fn.name, context.person, note or status)
        reply = AgentReply("live", status, text if status == "ok" else "", tool_calls,
                           False, note, cost_usd=cost)  # fmt: skip
        reply.call = self._record(context.case_id, record)
        return reply

    @staticmethod
    def _run_tools(
        uses: list[Any], toolbox: Toolbox, tool_calls: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Run every tool call of one turn; all results go back in a single message.
        A tool that fails for any other reason (the store refusing, a busy case, a disk
        error) is an error result too, so the call is still saved and costed. The model
        then sees only the error's type: its text may name files."""
        results = []
        for use in uses:
            args = dict(use.input or {})
            try:
                content, is_error = toolbox.call(use.name, args), False
            except (ToolError, KeyError, ValueError, TypeError) as exc:
                content, is_error = f"Error: {exc}", True
            except Exception as exc:  # noqa: BLE001 - every failure goes back to the model
                log.warning("AI tool %s failed: %s", use.name, type(exc).__name__)
                content = f"Error: the tool failed ({type(exc).__name__}); nothing more is known"
                is_error = True
            tool_calls.append({
                "tool": use.name, "input": args, "error": is_error,
                "result_sha256": hashlib.sha256(content.encode()).hexdigest(),
            })  # fmt: skip
            results.append({"type": "tool_result", "tool_use_id": use.id, "content": content,
                            "is_error": is_error})  # fmt: skip
        return results

    # -- records -----------------------------------------------------------------------------

    def _save(self, case_id: str, record: dict[str, Any], result: AiResult) -> AiResult:
        result.call = self._record(case_id, record)
        return result

    def _record(self, case_id: str | None, record: dict[str, Any]) -> dict[str, str]:
        """Save the call record, reference it, and add its cost to the ledger. About a case:
        `cases/<id>/ai/NNNN_<function>.json` and an `ai.called` event. About no case (the
        Slack Agent, P-58): `data/ai/calls/<date>/<time>_<function>_<random>.json`,
        referenced from the ledger line by its path in the data folder."""
        if case_id is None:
            return self._record_without_case(record)
        content = json_bytes(record)
        folder = self.store.case_dir(case_id) / "ai"
        n = len(list(folder.glob("*.json"))) + 1 if folder.exists() else 1
        while True:  # another process (the runner, the Slack app) may take a number first
            rel = f"ai/{n:04d}_{record['function']}.json"
            try:
                sha = self.store.put_file(case_id, rel, content)
                break
            except FileExistsError:
                n += 1
        call = {"path": rel, "sha256": sha}
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
                    "call": call,
                },
            ),
        )
        self.ledger.append({
            "case_id": case_id, "function": record["function"], "mode": record["mode"],
            "model": record["model"], "valid": record["valid"], "usage": record["usage"],
            "cost_usd": record["cost_usd"],
        })  # fmt: skip
        return call

    def _record_without_case(self, record: dict[str, Any]) -> dict[str, str]:
        content = json_bytes(record)
        stamp = format_time(self.store.clock())
        name = f"{stamp[11:19].replace(':', '')}_{record['function']}_{secrets.token_hex(4)}"
        rel = f"ai/calls/{stamp[:10]}/{name}.json"  # random part: two turns at once never clash
        write_atomic(self.store.paths.root / rel, content, overwrite=False)
        call = {"path": rel, "sha256": sha256_bytes(content)}
        self.ledger.append({
            "case_id": None, "function": record["function"], "mode": record["mode"],
            "model": record["model"], "valid": record["valid"], "usage": record["usage"],
            "cost_usd": record["cost_usd"], "call": call,
        })  # fmt: skip
        return call


@dataclass(frozen=True)
class _RecordedUse:
    """A tool call from a recorded turn, shaped like the API's tool_use block."""

    id: str
    name: str
    input: dict[str, Any]


def _api_error_note(exc: Exception) -> str:
    """What went wrong with an API call, in a few words (never the request content)."""
    import anthropic

    if isinstance(exc, anthropic.APIConnectionError):
        return "could not reach the API"
    if isinstance(exc, anthropic.RateLimitError):
        return "rate limited by the API"
    if isinstance(exc, anthropic.APIStatusError):
        # the API's own explanation (it names the problem, not the request content)
        err = (exc.body or {}).get("error", {}) if isinstance(exc.body, dict) else {}
        return f"API error {exc.status_code}: {str(err.get('message', ''))[:200]}".rstrip(": ")
    return f"API error: {type(exc).__name__}"
