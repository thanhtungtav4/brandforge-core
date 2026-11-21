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
