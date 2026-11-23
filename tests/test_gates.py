from content_core.graph import MAX_REWRITES, run_flow

GOOD_BRIEF = {"id": "t1", "format": "blog", "topic": "cà phê văn phòng",
              "keyword": "cà phê", "title": "Cà phê cho dân văn phòng"}
BAD_BRIEF = {**GOOD_BRIEF, "id": "t2", "writer_mode": "bad"}


def test_outline_gate_blocks_without_approval():
    state = run_flow(GOOD_BRIEF, outline_approved=False)
    assert state["status"] == "awaiting_outline_approval"
    assert not state.get("sections")


def test_good_brief_is_approved_first_pass():
    state = run_flow(GOOD_BRIEF, outline_approved=True)
    assert state["status"] == "approved"
    assert state["scores"]["brand"] >= 85
    assert state["rewrite_count"] == 0
    assert state["brand_id"] == "moc-brew"
    assert state["persona_id"] == "Linh"


def test_bad_draft_fails_first_pass_then_recovers_via_rewrite():
    state = run_flow(BAD_BRIEF, outline_approved=True)
    first = state["gate_history"][0]
    assert first["passed"] is False
    assert first["brand_violations"] > 0
    assert state["status"] == "approved"
    assert 1 <= state["rewrite_count"] <= MAX_REWRITES


def test_invalid_brief_stops():
    state = run_flow({"format": "blog"}, outline_approved=True)
    assert state["status"] == "invalid_brief"


def test_research_only_retrieves_triggered_facts():
    state = run_flow(GOOD_BRIEF, outline_approved=True)
    ids = {s["id"] for s in state["sources"]}
    assert "office-brewing" in ids          # brief mentions văn phòng
    assert "price" not in ids               # brief never mentions giá -> not retrieved


# --- N2 (review round 3): graph gate_history records the full MCP fields --------
# The LangGraph graph path's gate_history entries used to carry only the
# hard + craft-score fields (no craft_advisory, no style fields) while
# MCP final_gate entries carry the full set — a consumer-confusion risk,
# not a verdict risk. The graph node now records the same fields
# final_gate does; only the record shape changes, never a verdict.

# The MCP entry shape with no style profile and no quality layer active:
# exactly the keys final_gate writes in that case. content_sha joined
# in fix round F1 (the exporter's pass-currency fingerprint, recorded
# by BOTH gate paths).
_MCP_PLAIN_ENTRY_KEYS = {"pass", "brand_score", "brand_violations",
                         "fact_issues", "passed", "craft_score",
                         "craft_blocking", "craft_advisory",
                         "content_sha"}


def test_graph_gate_history_matches_mcp_entry_shape():
    state = run_flow(GOOD_BRIEF, outline_approved=True)
    assert state["status"] == "approved"
    entry = state["gate_history"][0]
    assert set(entry) == _MCP_PLAIN_ENTRY_KEYS
    # blog is a craft-blocking format: advisory is False, not absent.
    assert entry["craft_blocking"] is True
    assert entry["craft_advisory"] is False
    assert entry["craft_score"] >= 70


def test_graph_gate_history_records_style_when_brief_names_profile():
    brief = {**GOOD_BRIEF, "id": "t-styled",
             "style_profile_id": "mocbrew-blog-voice"}
    state = run_flow(brief, outline_approved=True)
    assert state["status"] == "approved"  # style is advisory: cannot block
    entry = state["gate_history"][0]
    assert entry["style_profile_id"] == "mocbrew-blog-voice"
    joined = "\n\n".join(s.get("text", "") for s in state["sections"])
    assert entry["style_score"] == _expected_style_score(joined)


def _expected_style_score(joined: str) -> float:
    from content_core.loaders import load_style_profile
    from content_core.style import style_match

    return style_match(joined, load_style_profile("mocbrew-blog-voice"))["score"]


def test_graph_gate_history_omits_style_for_stale_profile():
    # A brief naming a profile that no longer resolves must NOT newly
    # fail the draft (the graph path never had style handling): the
    # entry simply omits the style fields and the verdict is unchanged.
    brief = {**GOOD_BRIEF, "id": "t-stale",
             "style_profile_id": "khong-ton-tai"}
    state = run_flow(brief, outline_approved=True)
    assert state["status"] == "approved"
    entry = state["gate_history"][0]
    assert "style_score" not in entry
    assert "style_profile_id" not in entry


def test_graph_gate_history_omits_style_for_corrupt_profile(tmp_path, monkeypatch):
    # Same rule for a corrupt stored profile (the loader raises
    # ValueError): omit the style fields, keep the verdict.
    monkeypatch.setenv("CONTENT_CORE_STYLE_PROFILES_DIR", str(tmp_path))
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    brief = {**GOOD_BRIEF, "id": "t-corrupt", "style_profile_id": "broken"}
    state = run_flow(brief, outline_approved=True)
    assert state["status"] == "approved"
    entry = state["gate_history"][0]
    assert "style_score" not in entry
    assert "style_profile_id" not in entry


# --- Fix round F1: the gate content fingerprint --------------------------------
# content_sha is what lets the WordPress exporter prove a recorded
# pass still describes the current text. It must be deterministic,
# content-sensitive (title, heading, text, order) and blind to
# planning metadata (intent / target_words are not content).

from content_core.gates import content_sha  # noqa: E402

_SECS = [{"id": "s1", "heading": "Mở đầu", "intent": "intro",
          "target_words": 120, "text": "Nội dung một."},
         {"id": "s2", "heading": "Thân bài", "intent": "body",
          "target_words": 200, "text": "Nội dung hai."}]


def test_content_sha_deterministic_and_metadata_blind():
    a = content_sha(_SECS, "Tiêu đề")
    assert a == content_sha([dict(s) for s in _SECS], "Tiêu đề")
    tweaked = [dict(s) for s in _SECS]
    tweaked[0]["intent"] = "khác"
    tweaked[0]["target_words"] = 999
    assert content_sha(tweaked, "Tiêu đề") == a


def test_content_sha_changes_with_any_content_edit():
    a = content_sha(_SECS, "Tiêu đề")
    assert content_sha(_SECS, "Tiêu đề khác") != a
    edited = [dict(s) for s in _SECS]
    edited[1]["text"] = "Nội dung hai, đã sửa."
    assert content_sha(edited, "Tiêu đề") != a
    edited = [dict(s) for s in _SECS]
    edited[0]["heading"] = "Mở đầu khác"
    assert content_sha(edited, "Tiêu đề") != a
    assert content_sha(list(reversed(_SECS)), "Tiêu đề") != a


def test_graph_and_mcp_entries_carry_the_same_content_sha():
    # Both gate paths fingerprint the same content identically.
    state = run_flow(GOOD_BRIEF, outline_approved=True)
    entry = state["gate_history"][0]
    expect = content_sha(state["sections"], GOOD_BRIEF["title"])
    assert entry["content_sha"] == expect
