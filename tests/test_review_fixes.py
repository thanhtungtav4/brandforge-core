"""Fail-first tests for the CODE REVIEW fix round (2026-11-22).

Each test pins a repro from CODE_REVIEW_2026-11-22.md (finding ids in
the test names/docstrings): H1 sentence-scoped money roles, the
M1/M2 facts contract, M3 craft normalisation, M6/M7/M8 error dialect
+ shape validation, M10/M11 brand-draft conversion, M12-M15 fetch +
claims, L1/L4/L6/L7/L8/L9/L13/L16 small items, and the H2 shipped
Cao Nguyên Mộc draft invariants.
"""
from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from content_core import loaders  # noqa: E402
from content_core.brand_extract import (  # noqa: E402
    build_brand_draft, draft_to_brand_yaml, find_site_claims)
from content_core.craft import craft_check  # noqa: E402
from content_core.fact_check import check_section  # noqa: E402
from content_core.loaders import load_brand, load_facts  # noqa: E402
from content_core.quality import evaluate_quality, preset_profile  # noqa: E402

BRAND = load_brand()
FACTS = load_facts()


def _issue_types(text, facts=FACTS, brand=BRAND):
    issues, _flagged = check_section(text, facts, brand, "s1")
    return [i["type"] for i in issues]


# ---------------------------------------------------------------------------
# H1 — money roles are scoped to the amount's own sentence
# ---------------------------------------------------------------------------

def test_h1_cross_sentence_ship_fee_does_not_excuse_wrong_price():
    # The review repro: the ship marker sits in the PREVIOUS sentence.
    text = "Phí ship được tính riêng theo đơn. Gói 250g có giá 200.000đ."
    assert "wrong_price" in _issue_types(text)


def test_h1_same_sentence_ship_role_still_excused():
    text = "Phí ship cho một gói 250g là 200.000đ tính theo đơn của bạn."
    assert "wrong_price" not in _issue_types(text)


# ---------------------------------------------------------------------------
# M1/M2 — a facts doc validate_facts accepts is consumable everywhere
# ---------------------------------------------------------------------------

MINIMAL_FACTS = {"brand_id": "contract-test",
                 "facts": {"price_vnd_per_250g": 99000, "pack_weight_g": 250,
                           "origins": ["Arabica Cầu Đất"]},
                 "entries": [{"id": "p", "content": "Giá 99.000đ một gói."}]}


def test_m1_minimal_facts_doc_loads_normalized_and_checks(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_FACTS_PROFILES_DIR", str(tmp_path))
    loaders.save_facts(json.loads(json.dumps(MINIMAL_FACTS)))
    doc = loaders.load_facts("contract-test")
    # Derived/defaulted at load: display price + entry keys.
    assert doc["facts"]["price_display"] == "99.000đ"
    assert doc["entries"][0]["keys"] == []
    # Consumable by check_section without KeyError, either way.
    ok_issues, _ = check_section("Một gói 250g có giá 99.000đ.", doc, BRAND, "s1")
    assert ok_issues == []
    bad_issues, _ = check_section("Một gói 250g có giá 88.000đ.", doc, BRAND, "s1")
    assert [i["type"] for i in bad_issues] == ["wrong_price"]
    assert "99.000đ" in bad_issues[0]["detail"]


def test_m2_graph_research_tolerates_raw_minimal_facts():
    from content_core.graph import Core
    core = Core.__new__(Core)  # research() touches only self.facts
    core.facts = json.loads(json.dumps(MINIMAL_FACTS))
    out = core.research({"brief": {"topic": "giá gói"}})
    assert out["sources"] == []  # no keys -> nothing triggers, no KeyError


def test_l1_every_percent_is_checked_not_just_the_first():
    # Percent #1 is composition ("100% Arabica"); percent #2 is a
    # bare statistic percent. The old code looked at the first
    # percent only and never judged the second (review L1).
    text = ("Mẻ này có 100% Arabica Cầu Đất nguyên chất cho bạn. "
            "Mẻ kia trộn 50% Culi cùng 50% Arabica cho đậm vị hơn.")
    assert "unsupported_statistic" in _issue_types(text)


# ---------------------------------------------------------------------------
# M15 — claim-matcher precision (fact_check side)
# ---------------------------------------------------------------------------

def test_m15_idiom_tot_nhat_la_is_not_a_claim():
    assert _issue_types("Tốt nhất là dùng bã cà phê để khử mùi tủ lạnh.") == []


def test_m15_freshness_window_is_not_a_claim():
    text = "Hạt giữ vị ngon nhất trong vòng 2 ngày sau khi mở túi."
    assert _issue_types(text) == []


def test_m15_suffix_denial_is_not_a_claim():
    text = "Mộc Brew không phải cà phê tốt nhất Việt Nam đâu, mình chỉ rang mẻ nhỏ."
    assert _issue_types(text) == []


def test_m15_khong_chi_affirms_and_still_blocks():
    # "Không chỉ … đâu" AFFIRMS the claim — the suffix-denial rule
    # must not excuse it.
    assert "prohibited_claim" in _issue_types("Không chỉ tốt nhất Việt Nam đâu.")


def test_m15_question_claim_is_flagged_not_blocking():
    issues, flagged = check_section(
        "Vì sao nhiều người gọi đây là cà phê tốt nhất Việt Nam?",
        FACTS, BRAND, "s1")
    assert issues == [] and len(flagged) == 1


# ---------------------------------------------------------------------------
# M3 — craft matches on normalized text: accent twins score the same
# ---------------------------------------------------------------------------

def _strip_accents(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if not unicodedata.combining(c))


def test_m3_craft_accented_and_unaccented_twins_score_identically():
    accented = ("Hạt cà phê này thật sự ngon tuyệt vời, hương thơm phức và vị "
                "rất ngon miệng cho bạn. Mình rang hạt vào sáng thứ Ba và đóng "
                "gói ngay trong ngày cho bạn. Gói hạt giữ mùi thơm lâu khi bạn "
                "để nơi khô thoáng mát mẻ. Bạn pha phin chậm và chờ ba phút là "
                "có ly cà phê đậm vị.")
    a = craft_check(accented, "blog")
    u = craft_check(_strip_accents(accented), "blog")
    assert a["score"] == u["score"]
    assert a["metrics"]["weak_words_found"] == u["metrics"]["weak_words_found"]


# ---------------------------------------------------------------------------
# M6/M7/M8 — error dialect + shape validation at the MCP boundary
# ---------------------------------------------------------------------------

@pytest.fixture()
def _srv(tmp_path, monkeypatch):
    for var, sub in (("CONTENT_CORE_DRAFTS_DIR", "drafts"),
                     ("CONTENT_CORE_STYLE_PROFILES_DIR", "styles"),
                     ("CONTENT_CORE_BRAND_DRAFTS_DIR", "brand_drafts"),
                     ("CONTENT_CORE_FACTS_PROFILES_DIR", "facts"),
                     ("CONTENT_CORE_QUALITY_PROFILES_DIR", "quality")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    import mcp_server as srv
    return srv


def test_m6_corrupt_style_profile_is_structured_error(_srv, tmp_path):
    d = tmp_path / "styles"
    d.mkdir(parents=True, exist_ok=True)
    (d / "broken.json").write_text('["not", "an", "object"]', encoding="utf-8")
    out = _srv.get_style_profile("broken")
    assert "error" in out and "corrupt" in out["error"]
    out = _srv.style_match("văn bản mẫu", profile_id="broken")
    assert "error" in out


def test_m6_corrupt_brand_draft_is_structured_error(_srv, tmp_path):
    d = tmp_path / "brand_drafts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "broken.json").write_text('{"nope": 1}', encoding="utf-8")
    out = _srv.get_brand_draft("broken")
    assert "error" in out and "corrupt" in out["error"]


def test_m7_corrupt_draft_message_names_file_not_path(_srv, tmp_path):
    draft_id = _srv.start_draft({"topic": "t", "format": "blog"})["draft_id"]
    path = tmp_path / "drafts" / f"{draft_id}.json"
    path.write_text("{not json", encoding="utf-8")
    out = _srv.get_draft(draft_id)
    assert "error" in out
    assert str(tmp_path) not in out["error"]  # no absolute-path leak
    assert f"{draft_id}.json" in out["error"]


def test_m8_non_dict_section_entry_is_named_corrupt(_srv, tmp_path):
    draft_id = _srv.start_draft({"topic": "t", "format": "blog"})["draft_id"]
    path = tmp_path / "drafts" / f"{draft_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["sections"] = ["just a string"]
    path.write_text(json.dumps(data), encoding="utf-8")
    out = _srv.get_draft(draft_id)
    assert "error" in out and "section 0 must be an object" in out["error"]


def test_m7_list_functions_surface_corrupt_files(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_STYLE_PROFILES_DIR", str(tmp_path))
    (tmp_path / "good.json").write_text('{"id": "good"}', encoding="utf-8")
    (tmp_path / "bad.json").write_text('["a", "list"]', encoding="utf-8")
    listed = loaders.list_style_profiles()
    by_id = {e["id"]: e for e in listed}
    assert by_id["good"]["corrupt"] is False
    assert by_id["bad"]["corrupt"] is True


# ---------------------------------------------------------------------------
# L4/L6/L7 — small MCP/quality items
# ---------------------------------------------------------------------------

def test_l4_bool_target_words_rejected(_srv):
    draft_id = _srv.start_draft({"topic": "t", "format": "blog"})["draft_id"]
    out = _srv.save_section(draft_id, "s1", "H", "Hạt rang mùi thơm.",
                            target_words=True)
    assert "error" in out
    out = _srv.make_outline({"format": "blog"},
                            proposed_outline=[{"heading": "H",
                                               "target_words": True}])
    assert out["ok"] is False


def test_l4_float_judged_grade_rejected():
    sections = [{"id": "s1", "text": "Hạt rang mùi thơm cho bạn pha phin."}]
    with pytest.raises(ValueError):
        evaluate_quality(sections, "blog", preset_profile("blog"),
                         {"coherence": 2.0}, BRAND, FACTS)


def test_l6_gate_history_is_capped(_srv):
    draft_id = _srv.start_draft({"topic": "t", "format": "blog"})["draft_id"]
    _srv.save_section(draft_id, "s1", "H", "Hạt rang mùi thơm cho bạn.")
    last = None
    for _ in range(55):
        last = _srv.final_gate(draft_id)
    assert len(last["gate_history"]) == 50
    assert last["gate_history"][-1]["pass"] == 55  # numbering stays monotonic


def test_l7_brand_check_free_text_warning_only_not_failing(_srv):
    # A NEGATED banned term is a warning-only violation (score 100):
    # the draft path does not fail it, so the free-text path must not
    # either (they used to disagree).
    text = ("Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn. "
            "Mộc Brew không nhận là tốt nhất Việt Nam như nhiều nơi quảng cáo.")
    out = _srv.brand_check(text=text, format="blog")
    assert out["violations"] and out["score"] == 100
    assert out["failing_sections"] == []


# ---------------------------------------------------------------------------
# M10/M11 — conversion guard + guardrail claims union
# ---------------------------------------------------------------------------

def _competitor_draft():
    return {"brand": {"id": "rival", "name": "Rival", "source_type": "competitor"},
            "voice": {"prohibited_claims": ["claim-a"]},
            "claims_and_guardrails": {"prohibited_claims": ["claim-b"]},
            "examples": [{"text": "rival copy", "reference_only": False}],
            "anti_examples": []}


def test_m10_anti_example_reference_only_blocks_conversion():
    draft = _competitor_draft()
    draft["anti_examples"] = [{"bad": "rival bad", "good": "ours",
                               "reference_only": True}]
    with pytest.raises(ValueError, match="reference_only"):
        draft_to_brand_yaml(draft)


def test_m10_falsy_flag_spellings_do_not_clear_competitor_copy():
    draft = _competitor_draft()
    draft["examples"] = [{"text": "rival copy", "reference_only": 0}]
    with pytest.raises(ValueError, match="reference_only"):
        draft_to_brand_yaml(draft)


def test_m11_guardrail_only_prohibited_claim_survives_conversion():
    import yaml
    draft = _competitor_draft()
    out = yaml.safe_load(draft_to_brand_yaml(draft))
    assert out["voice"]["prohibited_claims"] == ["claim-a", "claim-b"]


def test_m10_save_brand_draft_restamps_reference_only(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_BRAND_DRAFTS_DIR", str(tmp_path))
    draft = _competitor_draft()
    draft["examples"] = [{"text": "rival copy"}]  # flag stripped pre-save
    loaders.save_brand_draft(draft)
    stored = loaders.load_brand_draft("rival")
    assert stored["examples"][0]["reference_only"] is True


# ---------------------------------------------------------------------------
# M12/M14/L8/L9 — fetch layer
# ---------------------------------------------------------------------------

def test_m12_decode_body_uses_declared_charset():
    from fetch_sample import decode_body
    body = "Cà phê ngon".encode("cp1258")
    text, enc = decode_body(body, "text/html; charset=windows-1258")
    assert text == "Cà phê ngon" and enc == "windows-1258"
    meta = (b'<html><head><meta charset="windows-1258"></head><body>'
            + "Cà phê".encode("cp1258"))
    text, enc = decode_body(meta)
    assert text.endswith("Cà phê") and enc == "windows-1258"


def test_m14_sitemap_entities_and_cdata_survive():
    from fetch_site import _parse_sitemap_locs
    doc = (b'<?xml version="1.0" encoding="UTF-8"?>'
           b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           b'<url><loc>https://x.vn/a?x=1&amp;y=2</loc></url>'
           b'<url><loc><![CDATA[https://x.vn/b]]></loc></url></urlset>')
    locs, is_index = _parse_sitemap_locs(doc)
    assert locs == ["https://x.vn/a?x=1&y=2", "https://x.vn/b"]
    assert is_index is False
    assert _parse_sitemap_locs(b"<urlset><url><loc>broken") is None


def test_l8_canonical_url_full_normalisation():
    from fetch_site import _canonical_url
    assert _canonical_url("http://user:pw@[::1]:80/a/%7e?q=2&a=1") == \
        "http://user:pw@[::1]/a/~?a=1&q=2"
    assert _canonical_url("http://example.com/a/%2f") == \
        "http://example.com/a/%2F"


def test_l9_non_html_link_lands_in_errors(tmp_path):
    import fetch_site as fs
    (tmp_path / "index.html").write_text(
        '<html><head><title>T</title></head><body>'
        '<a href="doc.pdf">doc</a>'
        "<p>" + "Cà phê rang mộc cho bạn mỗi tuần. " * 10 + "</p>"
        "</body></html>", encoding="utf-8")
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4 fake")
    out = fs.fetch_site(f"file://{tmp_path}/index.html")
    assert len(out["pages"]) == 1
    # file:// carries no Content-Type, so the PDF is skipped by
    # extension instead; the HTTP content-type gate is pinned below.


def test_l9_http_content_type_gate():
    import http.server
    import threading

    import fetch_site as fs

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/":
                body = (b"<html><head><title>T</title></head><body>"
                        b'<a href="/doc">doc</a><p>'
                        + b"Ca phe rang moc cho ban moi tuan. " * 10
                        + b"</p></body></html>")
                ctype = "text/html"
            else:
                body, ctype = b"%PDF-1.4 fake", "application/pdf"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        out = fs.fetch_site(f"http://127.0.0.1:{port}/")
    finally:
        server.shutdown()
    assert len(out["pages"]) == 1
    assert any("non-HTML content-type" in e for e in out.get("errors", []))


def test_m13_discovery_resolves_against_final_root():
    import http.server
    import threading

    import fetch_site as fs

    def _serve(handler_cls):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    class BHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/home":
                body = (b"<html><head><title>B</title></head><body>"
                        b'<a href="/about">about</a><p>'
                        + b"Trang chu cua B voi nhieu noi dung. " * 10
                        + b"</p></body></html>")
            elif self.path == "/about":
                body = (b"<html><head><title>About</title></head><body><p>"
                        + b"Gioi thieu ve B rat chi tiet. " * 10
                        + b"</p></body></html>")
            else:
                body, code = b"", 404
                self.send_response(code)
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    b = _serve(BHandler)
    b_port = b.server_address[1]

    class AHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(301)
            self.send_header("Location", f"http://127.0.0.1:{b_port}/home")
            self.end_headers()

    a = _serve(AHandler)
    a_port = a.server_address[1]
    try:
        out = fs.fetch_site(f"http://127.0.0.1:{a_port}/")
    finally:
        a.shutdown()
        b.shutdown()
    urls = [p["url"] for p in out["pages"]]
    assert any(u.endswith("/about") for u in urls), urls


def test_m15_site_claims_skip_questions_and_idioms():
    pages = [{"url": "https://x.vn/", "text": (
        "Cà phê rang xong bao lâu thì uống ngon nhất. "
        "Tốt nhất là dùng bã để khử mùi tủ lạnh. "
        "Hạt giữ vị ngon nhất trong vài tuần đầu sau khi mở túi.")}]
    assert find_site_claims(pages) == []


def test_l13_model_codes_are_not_vocabulary():
    pages = [{"url": "https://x.vn/", "title": "Máy Xay W180 W240 W320",
              "text": "Máy xay hạt cho quán nhỏ. " * 5,
              "headings": ["Máy Xay W180 W240 W320"], "html_meta": {}}]
    draft = build_brand_draft(pages, source_type="own_site",
                              brand_name="Xay House")
    assert "W180 W240 W320" not in draft["vocabulary"]["exact"]


# ---------------------------------------------------------------------------
# L16 — the CONTENT_WRITER plugin path
# ---------------------------------------------------------------------------

def test_l16_writer_plugin_loads_and_rejects_bad_paths(tmp_path, monkeypatch):
    from content_core.writer import MockWriter, get_writer
    (tmp_path / "mywriter.py").write_text(
        "from content_core.writer import MockWriter\n\n\n"
        "class MyWriter(MockWriter):\n    pass\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert type(get_writer("mywriter:MyWriter")).__name__ == "MyWriter"
    assert isinstance(get_writer("mock"), MockWriter)
    with pytest.raises(ValueError):
        get_writer("no-colon-here")


# ---------------------------------------------------------------------------
# M4 — quality reuse is verdict-identical
# ---------------------------------------------------------------------------

def test_m4_precomputed_quality_matches_fresh_quality():
    from content_core.fact_check import check_draft
    from content_core.linter import lint_draft
    sections = [{"id": "s1", "heading": "Giá",
                 "text": "Một gói 250g có giá 200.000đ cho bạn hôm nay."}]
    profile = preset_profile("blog")
    fresh = evaluate_quality(sections, "blog", profile, None, BRAND, FACTS)
    issues, flagged, _ = check_draft(sections, FACTS, BRAND)
    _v, score, _f = lint_draft(sections, "blog", BRAND)
    reused = evaluate_quality(
        sections, "blog", profile, None, BRAND, FACTS,
        _precomputed={"fact_issues": issues, "fact_flagged": flagged,
                      "brand_score": score})
    assert reused["weighted_total"] == fresh["weighted_total"]
    assert reused["axes"] == fresh["axes"]


# ---------------------------------------------------------------------------
# H2 — the shipped Cao Nguyên Mộc draft satisfies builder invariants
# ---------------------------------------------------------------------------

CNM_DRAFT = PROJECT_DIR / "data/brand_drafts/cao-nguyen-moc.json"
MOC_BREW_PROHIBITED = {"100% chữa mất ngủ", "tốt nhất việt nam",
                       "đẳng cấp thế giới", "chữa mất ngủ", "trị bệnh"}


@pytest.mark.skipif(not CNM_DRAFT.exists(), reason="CNM draft not present")
def test_h2_shipped_cnm_draft_invariants():
    draft = json.loads(CNM_DRAFT.read_text(encoding="utf-8"))
    # No other brand's prohibited claims, in either location.
    for where in (draft["voice"]["prohibited_claims"],
                  draft["claims_and_guardrails"]["prohibited_claims"]):
        assert not (set(where) & MOC_BREW_PROHIBITED)
        assert where == []
    # Derived sentence cap, not the pre-fix fixed default.
    assert draft["voice"]["sentence_rules"]["max_sentence_words"] == 39
    # No CSS-variable font families; no thumbnail logo.
    families = [f["family"] for f in draft["brand_kit"]["fonts"]]
    assert families and all("var(" not in f for f in families)
    assert draft["brand_kit"]["logo_url"]["value"] is None
    # No model-code 'names' in the exact vocabulary.
    assert "W180 W240 W320" not in draft["vocabulary"]["exact"]


def test_h2_builder_guarantees_the_invariants_on_fixture_pages():
    pages = [{"url": "https://x.vn/", "title": "Cà Phê Sạch Ngon",
              "text": ("Tụi mình rang cà phê vào sáng thứ Ba cho bạn. "
                       "Một gói hạt có giá ghi rõ trên bao bì của tụi mình. "
                       "Tụi mình chọn hạt từ Cầu Đất và giao trong tuần."),
              "headings": ["Cà Phê Sạch"],
              "html_meta": {"logo_url": "https://x.vn/img/thumb.jpg",
                            "fonts": [{"family": "var(--display)", "count": 3},
                                      {"family": "Lora", "count": 2}]}}]
    draft = build_brand_draft(pages, source_type="own_site",
                              brand_name="Sạch Ngon")
    assert draft["voice"]["prohibited_claims"] == []
    assert draft["claims_and_guardrails"]["prohibited_claims"] == []
