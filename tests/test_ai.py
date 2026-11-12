"""AI foundation (plan step 12): gateway modes, the tool loop, retries, records, cost
ledger, budget cap, replay, and gates. A scripted fake client stands in for the API:
tests never call it."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from adjud.ai.config import AiConfig, Prices
from adjud.ai.functions import DOCUMENT_TYPE
from adjud.ai.gates import citation_found, gate
from adjud.ai.gateway import Gateway, input_hash
from adjud.ai.tools import case_toolbox, page_text
from adjud.documents import DocumentProcessor, upload
from adjud.intake import IntakePipeline
from adjud.integrity import verify_all

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "data" / "flow1-testkit"
CONFIG = ROOT / "data" / "config"
TEXT = "DISCHARGE SUMMARY\nPatient: Philip Roberts\nAdmitted with chest pain on 14.03.2026."
PAYLOAD = {"file": "letter.pdf", "pages": 1, "allowed_types": ["discharge_summary", "other"]}
ANSWER = {
    "document_type": "discharge_summary",
    "confidence": 0.95,
    "evidence": {"file": "letter.pdf", "page": 1, "quote": "DISCHARGE SUMMARY"},
}


def pdf(text: str) -> bytes:
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), text, fontsize=11)
        return doc.tobytes()


def text(t: str):
    return SimpleNamespace(type="text", text=t)


def tool_use(name: str, args: dict, id_: str = "tu_1"):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=args)


def reply(*blocks, stop="end_turn", tokens=(1000, 200)):
    usage = SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1])
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=usage)


class FakeClient:
    """Returns scripted responses in order and keeps every request."""

    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []
        self.messages = self

    def create(self, **kw):
        self.requests.append(json.loads(json.dumps(kw, default=lambda o: o.__dict__)))
        return self.responses.pop(0)


@pytest.fixture
def case(tmp_path, clock):
    data = tmp_path / "data"
    pipe = IntakePipeline(data, CONFIG, KIT / "edc_mock", clock=clock)
    (data / "flow1-testkit").symlink_to(KIT)
    pipe.import_file(KIT / "inbox_scenarios" / "01_initial.csv")
    cid = pipe.case_for("DEMO01|DEMO01-101-0001|AE-001")
    upload(pipe.store, cid, [("letter.pdf", pdf(TEXT))], "site101.coord")
    DocumentProcessor(pipe.store, CONFIG).process_case(cid)
    return pipe.store, cid


def cfg(**kw) -> AiConfig:
    base = {
        "mode": "live",
        "functions": {"document_type": True},
        "price_per_mtok": Prices(input=0.10, output=0.50),
    }
    return AiConfig(**{**base, **kw})


def gateway(store, tmp_path, client=None, env=None, **kw) -> Gateway:
    return Gateway(store, cfg(**kw), tmp_path / "replays", client=client, env=env or {})


def run(gw, store, cid, payload=PAYLOAD):
    return gw.run(cid, DOCUMENT_TYPE, payload, case_toolbox(store, cid))


def test_off_makes_no_call_and_records_nothing(case, tmp_path):
    store, cid = case
    r = run(gateway(store, tmp_path, mode="off"), store, cid)
    assert (r.status, r.mode) == ("unavailable", "off")
    assert not (store.case_dir(cid) / "ai").exists()


def test_live_without_a_key_continues_without_ai(case, tmp_path):
    store, cid = case
    r = run(gateway(store, tmp_path), store, cid)
    assert r.status == "unavailable" and "no API key" in r.note


def test_live_tool_loop_answer_record_ledger_and_gate(case, tmp_path):
    store, cid = case
    client = FakeClient(
        reply(tool_use("read_page_text", {"file": "letter.pdf", "page": 1}), stop="tool_use"),
        reply(text(json.dumps(ANSWER))),
    )
    r = run(gateway(store, tmp_path, client=client), store, cid)
    assert r.ok and r.output["document_type"] == "discharge_summary" and r.label == "AI"
    # the tool read the redacted page: the patient's name is gone, the title is there
    tool_result = client.requests[1]["messages"][2]["content"][0]
    assert "DISCHARGE SUMMARY" in tool_result["content"] and "Philip" not in tool_result["content"]
    first = client.requests[0]
    assert first["model"] == "claude-haiku-5-5"
    assert first["output_config"]["format"]["type"] == "json_schema"
    assert all(t["strict"] for t in first["tools"])
    record = store.get_json(cid, r.call["path"])
    assert record["valid"] and record["tool_calls"][0]["tool"] == "read_page_text"
    assert record["usage"]["input_tokens"] == 2000 and record["cost_usd"] == pytest.approx(0.0004)
    assert [e["type"] for e in store.events(cid)].count("ai.called") == 1
    assert all(v.ok for v in verify_all(store.paths))  # call file and ledger are chained

    def pages(f, p):
        return page_text(store, cid, f, p)

    g = gate(r, min_confidence=0.8, citations=[r.output["evidence"]], page_text=pages)
    assert g.passed, g.reasons
    fake = {**r.output["evidence"], "quote": "ECG REPORT"}
    assert not gate(r, min_confidence=0.8, citations=[fake], page_text=pages).passed
    assert "below" in gate(r, min_confidence=0.99).reasons[0]
    assert gate(r, min_confidence=0.8, checks=[("file unreadable", False)]).reasons == [
        "file unreadable"
    ]


def test_schema_invalid_answers_are_re_asked_then_given_up(case, tmp_path):
    store, cid = case
    bad = reply(text('{"document_type": "discharge_summary"}'))
    client = FakeClient(bad, reply(text(json.dumps(ANSWER))))
    assert run(gateway(store, tmp_path, client=client), store, cid).ok
    assert "did not match" in client.requests[1]["messages"][-1]["content"]

    client = FakeClient(bad, bad, bad)
    r = run(gateway(store, tmp_path, client=client), store, cid)
    assert r.status == "invalid" and len(client.requests) == 3
    assert store.get_json(cid, r.call["path"])["valid"] is False  # failures are kept


def test_tool_errors_go_back_to_the_agent(case, tmp_path):
    store, cid = case
    client = FakeClient(
        reply(tool_use("read_page_text", {"file": "original.pdf", "page": 1}), stop="tool_use"),
        reply(text(json.dumps(ANSWER))),
    )
    run(gateway(store, tmp_path, client=client), store, cid)
    result = client.requests[1]["messages"][2]["content"][0]
    assert result["is_error"] and "no page 1" in result["content"]


def test_refusal_is_recorded_as_an_error(case, tmp_path):
    store, cid = case
    client = FakeClient(reply(text(""), stop="refusal"))
    r = run(gateway(store, tmp_path, client=client), store, cid)
    assert r.status == "error" and "declined" in r.note


def test_api_errors_keep_the_apis_explanation(case, tmp_path):
    import anthropic
    import httpx

    class Failing:
        class messages:  # noqa: N801
            @staticmethod
            def create(**_):
                req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
                body = {"error": {"type": "invalid_request_error", "message": "needs a workspace"}}
                raise anthropic.BadRequestError("bad", response=httpx.Response(400, request=req),
                                                body=body)  # fmt: skip

    store, cid = case
    r = run(gateway(store, tmp_path, client=Failing(), mode="live",
                    env={"ANTHROPIC_API_KEY": "k"}), store, cid)  # fmt: skip
    assert not r.ok and r.note == "API error 400: needs a workspace"


def test_workspace_id_is_sent_with_every_request(case, tmp_path):
    store, _ = case
    env = {"ANTHROPIC_API_KEY": "k", "ANTHROPIC_WORKSPACE_ID": "wrkspc_test"}
    client = gateway(store, tmp_path, mode="live", env=env)._client()
    assert client.default_headers["anthropic-workspace-id"] == "wrkspc_test"
    plain = gateway(store, tmp_path, mode="live", env={"ANTHROPIC_API_KEY": "k"})._client()
    assert "anthropic-workspace-id" not in plain.default_headers


def test_key_check_makes_one_tiny_call_and_records_its_cost(case, tmp_path):
    store, _ = case
    client = FakeClient(reply(text("OK"), tokens=(12, 3)))
    gw = gateway(store, tmp_path, client=client, mode="live", env={"ANTHROPIC_API_KEY": "k"})
    ok, detail = gw.check()
    assert ok and "answered" in detail
    assert client.requests[0]["max_tokens"] == 64
    (entry,) = list(gw.ledger.records())
    assert entry["function"] == "key_check" and entry["case_id"] is None
    assert gw.spent_usd() == entry["cost_usd"] > 0
    assert not gateway(store, tmp_path, mode="live").check()[0]  # no key


def test_budget_cap_stops_live_calls(case, tmp_path):
    store, cid = case
    client = FakeClient(reply(text(json.dumps(ANSWER)), tokens=(10_000_000, 0)))
    gw = gateway(store, tmp_path, client=client, budget_usd=0.5)
    assert run(gw, store, cid).cost_usd == pytest.approx(1.0)
    r = run(gw, store, cid)
    assert r.status == "unavailable" and "budget" in r.note and not client.responses


def test_replay_returns_the_recorded_output_labelled_simulated(case, tmp_path):
    store, cid = case
    gw = gateway(store, tmp_path, mode="replay")
    assert "no recorded output" in run(gw, store, cid).note
    h = input_hash(DOCUMENT_TYPE, gw.cfg.model, PAYLOAD)
    path = tmp_path / "replays" / "document_type" / f"{h}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"simulated": True, "output": ANSWER}))
    r = run(gw, store, cid)
    assert r.ok and r.simulated and r.label == "Simulated AI" and r.cost_usd == 0
    assert store.get_json(cid, r.call["path"])["mode"] == "replay"


def test_output_schema_is_self_contained_and_closed():
    schema = DOCUMENT_TYPE.output_schema()
    dumped = json.dumps(schema)
    assert "$ref" not in dumped and "$defs" not in dumped and "title" not in dumped
    assert schema["additionalProperties"] is False
    assert schema["properties"]["evidence"]["additionalProperties"] is False


def test_citations_tolerate_ocr_spacing_and_case():
    assert citation_found("Discharge  summary", "DISCHARGE SUMMARY\nPatient ...")
    assert not citation_found("ECG", "ECG report")  # too short to prove anything
    assert not citation_found("Discharge summary", None)


def test_study_config_builds_a_gateway_with_ai_off(case):
    store, _ = case
    gw = Gateway.for_study(store, CONFIG)
    assert gw.cfg.mode == "off" and gw.replay_dir == CONFIG / "DEMO01" / "ai_replays"


# --- AI-1: the agent adds identifiers on scanned pages (plan step 20) ----------------------


def test_ai1_adds_an_identifier_the_rules_missed_on_a_scan(tmp_path, clock, monkeypatch):
    from adjud.documents import documents_of

    data = tmp_path / "data"
    pipe = IntakePipeline(data, CONFIG, KIT / "edc_mock", clock=clock)
    (data / "flow1-testkit").symlink_to(KIT)
    pipe.import_file(KIT / "inbox_scenarios" / "01_initial.csv")
    cid = pipe.case_for("DEMO01|DEMO01-101-0001|AE-001")
    with pymupdf.open() as doc:
        doc.new_page().insert_text(
            (72, 120), "Clinic note. Tray zebra quokka is full.", fontsize=14
        )
        png = doc[0].get_pixmap(dpi=200).tobytes("png")  # a scan: pixels only
    upload(pipe.store, cid, [("scan.png", png)], "site101.coord")
    answer = {"identifiers": [{"text": "zebra quokka", "category": "PATIENT_NAME"}]}
    client = FakeClient(reply(text(json.dumps(answer))))
    gw = Gateway(pipe.store, cfg(functions={"phi_detection": True}), tmp_path / "r",
                 client=client)  # fmt: skip
    monkeypatch.setattr("adjud.ai.gateway.Gateway.for_study", classmethod(lambda *a, **k: gw))
    DocumentProcessor(pipe.store, CONFIG).process_case(cid)

    done = documents_of(pipe.store, cid).processed["scan.png"]
    assert done["status"] == "verified"
    report = pipe.store.get_json(cid, done["report"]["path"])
    assert any(
        f["detector"] == "ai" and f["category"] == "PATIENT_NAME" for f in report["findings"]
    )
    out = pipe.store.get_file(cid, done["output"]["path"])
    assert "zebra" not in page_text_of(out).lower() and "clinic note" in page_text_of(out).lower()
    sent = client.requests[0]["messages"][0]["content"]
    assert sent[0]["type"] == "image" and json.loads(sent[1]["text"])["file"] == "scan.png"
    assert "tools" not in client.requests[0]  # AI-1 gets no tools


def test_ai1_stays_off_unless_switched_on(case, tmp_path):
    from adjud.documents import DocumentProcessor as DP

    store, _ = case
    assert DP(store, CONFIG)._ai_phi("DEMO01-0001") is None  # study.yaml: phi_detection false


def page_text_of(pdf_bytes: bytes) -> str:
    from PIL import Image

    from adjud.deid import ocr

    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        pix = doc[0].get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)
    return ocr.ocr_unit(Image.frombytes("L", (pix.width, pix.height), pix.samples), 200).text
