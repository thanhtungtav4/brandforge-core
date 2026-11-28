"""GEO layer — Generative Engine Optimization checks (advisory).

WHY THIS EXISTS (Smith's request, 2026-11-27; spec:
specs/geo-feature.md): the Quality Profile's `seo` axis judges whether
an article can rank and display well in classic search. AI answer
engines (ChatGPT, Gemini, Perplexity, Google AI Overview) do something
else: they lift self-contained blocks out of a page and cite them.
This layer measures the surface signals of citability — answer-first
openings, quotable factual statements, statistics carrying a source,
an explicit definition of the topic, question-shaped headings,
extractable structure, an FAQ — so the writer can fix a draft BEFORE
it ships, instead of discovering it was never citable.

ADVISORY BOUNDARY (the images.py precedent): this layer NEVER enters
final_gate's verdict, never touches the seven quality axes, their
weights, floors or PASS_TOTAL, and never changes any existing
threshold. A stored GEO report surfaces in combined checks as one
additive `geo` summary field and nowhere else. Grade 0 in the judged
layer is a hard flag INSIDE the GEO report only.

SPLIT OF LABOUR (the house architecture): the deterministic checker
below counts surface signals — it cannot judge whether the answer is
actually good, only whether the shapes AI engines extract are
present. That judgement belongs to the host model, recorded through
`validate_geo_judgement` (mirroring record_vision_check for images):
rubric grades 0/1/2 for citability / answer_clarity / self_contained
plus issues from a fixed vocabulary. The judgement is stored BESIDE
the deterministic report and never blended into `geo_score` — the
deterministic score must stay reproducible from the text alone.

SCORING: each check scores 0–100 against its own documented target;
`geo_score` is the weighted mean over the checks that APPLY. A check
that cannot judge this draft (too little text, no topic to look for,
a format the check does not target) is EXCLUDED and the weights
renormalise over the rest — the same missing-axis pattern as
quality.py. Nothing is ever scored 0 by default for being absent.

PROVISIONAL WEIGHTS: GEO_WEIGHTS below are v1 defaults for a NEW
layer, marked provisional in every report (`weights_status`). They
are not Smith-approved design values like quality.py's presets.
Whether GEO later folds into the `seo` axis, becomes an eighth axis,
or stays an advisory layer is deferred to Smith until trial data
exists (see the spec, §5).
"""
from __future__ import annotations

import re

from .linter import normalize_for_match, split_sentences
from .quality import GRADE_SCORES

GEO_VERSION = 1

#: Provisional v1 weights (sum 100) — see the module docstring.
GEO_WEIGHTS: dict[str, int] = {
    "answer_first": 20,
    "quotable_blocks": 20,
    "fact_density": 15,
    "attribution": 10,
    "definition_present": 10,
    "question_headings": 10,
    "structure": 10,
    "faq_present": 5,
}

CHECK_LABELS: dict[str, str] = {
    "answer_first": "Trả lời thẳng ở mở đầu",
    "question_headings": "Tiêu đề dạng câu hỏi",
    "fact_density": "Mật độ số liệu",
    "attribution": "Số liệu có nguồn",
    "quotable_blocks": "Câu trích dẫn được",
    "definition_present": "Định nghĩa chủ đề",
    "structure": "Cấu trúc dễ trích xuất",
    "faq_present": "Mục hỏi đáp (FAQ)",
}

#: Status bands inside the GEO report (advisory, provisional like the
#: weights): >= PASS_AT reads as pass, >= WARN_AT as warn, else fail.
PASS_AT = 70
WARN_AT = 40

#: Below this many words the rate-based checks (density, quotable,
#: structure) judge noise, not prose — they exclude themselves.
MIN_WORDS_FOR_RATES = 50

#: Fact density target: numeric statements per 1,000 words at which
#: the check reaches full marks (linear below).
FACT_DENSITY_TARGET = 6.0
#: Share of numeric claims carrying a source cue at full marks.
ATTRIBUTION_TARGET = 0.5
#: Share of question-shaped headings at full marks.
QUESTION_HEADING_TARGET = 0.4
#: Quotable blocks at full marks.
QUOTABLE_TARGET = 4
#: A paragraph in this word range is one extractable idea.
PARAGRAPH_MIN_WORDS = 20
PARAGRAPH_MAX_WORDS = 120
#: A quotable statement is long enough to stand alone, short enough
#: to lift verbatim.
QUOTABLE_MIN_WORDS = 8
QUOTABLE_MAX_WORDS = 45

#: Question markers for headings, matched on NORMALIZED text
#: (diacritics stripped), so "là gì" is looked up as "la gi".
QUESTION_MARKERS = ["la gi", "nhu the nao", "bao nhieu", "vi sao",
                    "co nen", "khi nao", "o dau", "the nao", "lam sao",
                    "la ai", "co khong", "duoc khong"]

#: Source cues for a numeric claim, matched on normalized text:
#: attribution phrases, explicit source labels, links, and named
#: institutions a Vietnamese article plausibly cites.
SOURCE_CUES = ["theo ", "nguon ", "http", "www.", "who", "fao",
               "ubnd", "hiep hoi", "tong cuc", "so nong nghiep",
               "so cong thuong", "so y te", "bo y te", "bo cong thuong",
               "bo nong nghiep", "ngan hang nha nuoc"]

#: Sentences opening with a reference or a connector do not stand
#: alone — an AI lifting them verbatim lifts a dangling pointer.
REFERENCE_OPENERS = ["no", "chung", "ho", "tuy nhien", "ngoai ra",
                     "vi vay", "do do", "sau do", "ben canh do",
                     "the la", "khi do", "luc nay"]

#: Formats the FAQ check does not target (short-form copy has no
#: room for an FAQ block; the check excludes itself, as craft's
#: sensory check excludes non-food formats by design elsewhere).
FAQ_EXEMPT_FORMATS = {"social", "email"}

#: Judged layer (host model): dimensions + fixed issue vocabulary.
JUDGE_DIMS = ["citability", "answer_clarity", "self_contained"]
JUDGE_ISSUES = ["buried_answer", "vague_claims", "missing_context",
                "jargon_unexplained", "not_self_contained",
                "no_citable_fact", "structure_hard_to_extract"]

_NUMBER_RE = re.compile(r"\d")
_LIST_LINE_RE = re.compile(r"^\s*(?:[-•*]|\d{1,2}[.)])\s+\S", re.MULTILINE)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _words(text: str) -> int:
    return len((text or "").split())


def _topic_of(brief: dict | None) -> str:
    """The main entity/topic a GEO check can look for: the brief's
    keyword first (short, matchable); else the leading words of the
    topic, then of the title. '' when the brief names nothing."""
    brief = brief or {}
    keyword = str(brief.get("keyword") or "").strip()
    if keyword:
        return keyword
    for field in ("topic", "title"):
        value = str(brief.get(field) or "").strip()
        if value:
            return " ".join(value.split()[:4])
    return ""


def _sentences_of(sections: list[dict]) -> list[tuple[str, str]]:
    """(section_id, sentence) pairs in draft order."""
    out: list[tuple[str, str]] = []
    for sec in sections:
        for sent in split_sentences(sec.get("text", "") or ""):
            out.append((sec.get("id", ""), sent))
    return out


def _is_question(text: str) -> bool:
    if "?" in text:
        return True
    norm = f" {normalize_for_match(text)} "
    return any(f" {marker} " in norm or norm.rstrip().endswith(marker)
               for marker in QUESTION_MARKERS)


def _paragraphs(sections: list[dict]) -> list[str]:
    paras: list[str] = []
    for sec in sections:
        for block in re.split(r"\n\s*\n", sec.get("text", "") or ""):
            if block.strip():
                paras.append(block.strip())
    return paras


def _definition_sentence(sentences: list[tuple[str, str]],
                         topic_norm: str) -> str | None:
    """The first sentence (in the first two sections) defining the
    topic: '<topic> là …' on normalized text."""
    if not topic_norm:
        return None
    pattern = re.compile(re.escape(topic_norm) + r" la ")
    seen_sections: list[str] = []
    for sec_id, sent in sentences:
        if sec_id not in seen_sections:
            seen_sections.append(sec_id)
        if len(seen_sections) > 2:
            break
        if pattern.search(normalize_for_match(sent)):
            return sent
    return None


def _is_quotable(sentence: str) -> bool:
    words = _words(sentence)
    if not (QUOTABLE_MIN_WORDS <= words <= QUOTABLE_MAX_WORDS):
        return False
    norm = normalize_for_match(sentence)
    if not (_NUMBER_RE.search(sentence) or " la " in f" {norm} "):
        return False
    return not any(norm == opener or norm.startswith(opener + " ")
                   for opener in REFERENCE_OPENERS)


# ---------------------------------------------------------------------------
# The eight checks — each returns (score | None, evidence, suggestion)
# ---------------------------------------------------------------------------

def _check_answer_first(sections, sentences, topic_norm):
    if not topic_norm or not sentences:
        return None, {}, ""
    opening = [s for s in sentences if s[0] == sentences[0][0]][:2]
    joined = " ".join(s for _, s in opening)
    norm = normalize_for_match(joined)
    entity_early = topic_norm in norm
    direct = bool(_NUMBER_RE.search(joined)) or bool(
        re.search(re.escape(topic_norm) + r" la ", norm))
    if entity_early and direct:
        score = 100.0
    elif entity_early:
        score = 55.0
    elif direct:
        score = 40.0
    else:
        score = 0.0
    evidence = {"entity_early": entity_early, "direct_answer": direct,
                "opening": opening[0][1][:160] if opening else ""}
    suggestion = ("" if score >= PASS_AT else
                  "Đưa câu trả lời chính lên 1–2 câu đầu của bài, nêu rõ "
                  "chủ đề và kèm một sự thật cụ thể (số liệu hoặc định "
                  "nghĩa) — máy trả lời AI trích đoạn mở đầu trước tiên.")
    return score, evidence, suggestion


def _check_question_headings(sections, sentences, topic_norm):
    headed = [s for s in sections if str(s.get("heading") or "").strip()]
    if len(headed) < 2:
        return None, {}, ""
    questions = sum(1 for s in headed if _is_question(s["heading"]))
    share = questions / len(headed)
    score = min(100.0, share / QUESTION_HEADING_TARGET * 100)
    evidence = {"question_headings": questions,
                "headed_sections": len(headed), "share": round(share, 3)}
    suggestion = ("" if score >= PASS_AT else
                  "Đổi một số tiêu đề mục thành câu hỏi đúng cách người "
                  "dùng hỏi (…là gì? bao nhiêu? như thế nào?) — tiêu đề "
                  "câu hỏi khớp truy vấn và giúp AI map mục vào câu hỏi.")
    return score, evidence, suggestion


def _check_fact_density(sections, sentences, topic_norm, word_count):
    if word_count < MIN_WORDS_FOR_RATES:
        return None, {}, ""
    numeric = sum(1 for _, s in sentences if _NUMBER_RE.search(s))
    density = numeric / word_count * 1000 if word_count else 0.0
    score = min(100.0, density / FACT_DENSITY_TARGET * 100)
    evidence = {"numeric_sentences": numeric, "word_count": word_count,
                "per_1000_words": round(density, 2)}
    suggestion = ("" if score >= PASS_AT else
                  "Thêm số liệu cụ thể (giá, khối lượng, thời gian, tỉ "
                  "lệ) — đích khoảng 6 câu có số trên 1.000 từ; câu "
                  "không số hiếm khi được AI trích dẫn.")
    return score, evidence, suggestion


def _check_attribution(sections, sentences, topic_norm):
    numeric = [s for _, s in sentences if _NUMBER_RE.search(s)]
    if not numeric:
        return None, {}, ""
    attributed = 0
    for sent in numeric:
        norm = normalize_for_match(sent)
        if any(cue in norm for cue in SOURCE_CUES):
            attributed += 1
    share = attributed / len(numeric)
    score = min(100.0, share / ATTRIBUTION_TARGET * 100)
    evidence = {"numeric_sentences": len(numeric),
                "attributed_sentences": attributed,
                "share": round(share, 3)}
    suggestion = ("" if score >= PASS_AT else
                  "Gắn nguồn cho các số liệu quan trọng ('theo …', tên "
                  "tổ chức, hoặc đường link) — AI ưu tiên trích số liệu "
                  "có nguồn hơn số liệu trôi nổi.")
    return score, evidence, suggestion


def _check_quotable(sections, sentences, topic_norm, word_count):
    if word_count < MIN_WORDS_FOR_RATES:
        return None, {}, ""
    quotable = [s for _, s in sentences if _is_quotable(s)]
    score = min(100.0, len(quotable) / QUOTABLE_TARGET * 100)
    evidence = {"quotable_blocks": len(quotable),
                "examples": [s[:140] for s in quotable[:3]]}
    suggestion = ("" if score >= PASS_AT else
                  "Viết thêm câu sự thật tự chứa: chủ thể + một con số "
                  "hoặc một định nghĩa trong cùng một câu 8–45 từ, "
                  "không mở đầu bằng từ nối hay đại từ tham chiếu.")
    return score, evidence, suggestion


def _check_definition(sections, sentences, topic_norm):
    if not topic_norm:
        return None, {}, ""
    found = _definition_sentence(sentences, topic_norm)
    evidence = {"definition": found[:160] if found else None}
    suggestion = ("" if found else
                  "Thêm một câu định nghĩa chủ đề ngay ở phần đầu "
                  "('X là …') — đây là câu AI hay trích nhất khi người "
                  "dùng hỏi 'X là gì'.")
    return (100.0 if found else 0.0), evidence, suggestion


def _check_structure(sections, sentences, topic_norm, word_count):
    if word_count < MIN_WORDS_FOR_RATES:
        return None, {}, ""
    joined = "\n".join(s.get("text", "") or "" for s in sections)
    has_list = bool(_LIST_LINE_RE.search(joined))
    paras = _paragraphs(sections)
    in_range = sum(1 for p in paras
                   if PARAGRAPH_MIN_WORDS <= _words(p) <= PARAGRAPH_MAX_WORDS)
    para_share = in_range / len(paras) if paras else 0.0
    headed = [s for s in sections if str(s.get("heading") or "").strip()]
    headed_share = len(headed) / len(sections) if sections else 0.0
    subs = {"list_score": 100.0 if has_list else 0.0,
            "paragraph_score": round(para_share * 100, 2),
            "headed_score": round(headed_share * 100, 2)}
    score = round(sum(subs.values()) / len(subs), 2)
    evidence = {"has_list": has_list, "paragraphs": len(paras),
                "paragraphs_in_range": in_range, **subs}
    suggestion = ("" if score >= PASS_AT else
                  "Tổ chức lại cho dễ trích xuất: mỗi đoạn một ý "
                  "(20–120 từ), có danh sách liệt kê khi kể nhiều mục, "
                  "và mọi section đều có tiêu đề.")
    return score, evidence, suggestion


#: Raw-text sentence segments WITH their terminator kept — the
#: shared split_sentences strips punctuation, but Q&A detection
#: needs to know which segments were questions.
_SEGMENT_RE = re.compile(r"[^?!….]+(\?|[!….])?")


def _qa_pairs(sections) -> int:
    """Question segments immediately followed by an answer segment,
    counted on the raw text (see _SEGMENT_RE)."""
    pairs = 0
    for sec in sections:
        segments = [(m.group(0).strip(), (m.group(1) or "") == "?")
                    for m in _SEGMENT_RE.finditer(sec.get("text", "") or "")]
        segments = [s for s in segments if s[0]]
        for i, (_, is_question) in enumerate(segments):
            if is_question and i + 1 < len(segments) \
                    and not segments[i + 1][1]:
                pairs += 1
    return pairs


def _check_faq(sections, sentences, topic_norm, fmt):
    if fmt in FAQ_EXEMPT_FORMATS:
        return None, {}, ""
    for sec in sections:
        norm = normalize_for_match(sec.get("heading") or "")
        if "faq" in norm or "hoi dap" in norm or "cau hoi" in norm:
            return 100.0, {"faq_section": sec.get("id", ""),
                           "qa_pairs": 0}, ""
    pairs = _qa_pairs(sections)
    score = 100.0 if pairs >= 2 else (50.0 if pairs == 1 else 0.0)
    evidence = {"faq_section": None, "qa_pairs": pairs}
    suggestion = ("" if score >= PASS_AT else
                  "Thêm mục Hỏi đáp ở cuối bài với 2–3 câu hỏi người "
                  "mua hay hỏi nhất, mỗi câu trả lời trong 1–3 câu — "
                  "đây là khối AI trích nguyên văn thường xuyên nhất.")
    return score, evidence, suggestion


# ---------------------------------------------------------------------------
# Per-section fixes — the "tối ưu" output
# ---------------------------------------------------------------------------

def _fixes(sections, sentences, checks: dict[str, dict]) -> list[dict]:
    fixes: list[dict] = []

    def add(section_id: str, check: str, suggestion: str) -> None:
        if suggestion and len(fixes) < 10:
            fixes.append({"section_id": section_id, "check": check,
                          "suggestion": suggestion})

    if not sections:
        return fixes
    first_id = sections[0].get("id", "")
    last_id = sections[-1].get("id", "")

    af = checks["answer_first"]
    if af["status"] in ("fail", "warn"):
        add(first_id, "answer_first", af["suggestion"])
    definition = checks["definition_present"]
    if definition["status"] == "fail":
        add(first_id, "definition_present", definition["suggestion"])
    faq = checks["faq_present"]
    if faq["status"] == "fail":
        add(last_id, "faq_present", faq["suggestion"])

    by_section: dict[str, list[str]] = {}
    for sec_id, sent in sentences:
        by_section.setdefault(sec_id, []).append(sent)
    numeric_fixes = 0
    for sec in sections:
        sec_id = sec.get("id", "")
        sents = by_section.get(sec_id, [])
        if _words(sec.get("text", "") or "") >= 40 and sents and not any(
                _NUMBER_RE.search(s) for s in sents):
            if numeric_fixes < 3:
                add(sec_id, "fact_density",
                    "Section này chưa có số liệu nào — thêm một sự "
                    "thật cụ thể, đo được (kèm nguồn) để AI có thứ "
                    "trích dẫn.")
                numeric_fixes += 1
        for para in _paragraphs([sec]):
            if _words(para) > PARAGRAPH_MAX_WORDS:
                add(sec_id, "structure",
                    "Có đoạn dài hơn 120 từ — tách thành các đoạn "
                    "ngắn, mỗi đoạn một ý, để AI trích được từng ý.")
                break

    headings = checks["question_headings"]
    if headings["status"] in ("fail", "warn"):
        for sec in sections:
            heading = str(sec.get("heading") or "").strip()
            if heading and not _is_question(heading):
                add(sec.get("id", ""), "question_headings",
                    "Cân nhắc viết lại tiêu đề này thành câu hỏi nếu "
                    "tự nhiên ('… là gì?', '… bao nhiêu?').")
                break
    attribution = checks["attribution"]
    if attribution["status"] in ("fail", "warn"):
        worst_id, worst_count = "", 0
        for sec in sections:
            sec_id = sec.get("id", "")
            bare = sum(1 for s in by_section.get(sec_id, [])
                       if _NUMBER_RE.search(s) and not any(
                           cue in normalize_for_match(s)
                           for cue in SOURCE_CUES))
            if bare > worst_count:
                worst_id, worst_count = sec_id, bare
        if worst_id:
            add(worst_id, "attribution", attribution["suggestion"])
    return fixes


# ---------------------------------------------------------------------------
# The checker
# ---------------------------------------------------------------------------

def check_geo(sections, brief: dict | None = None) -> dict:
    """Run the deterministic GEO checks over a draft's sections.

    `sections` is a list of {id, heading?, text} dicts (a plain string
    is treated as one section, like quality._as_sections). `brief`
    supplies the topic/keyword (what the article is ABOUT — the entity
    the checks look for) and the format (FAQ applicability). Pure
    function of its inputs; the MCP tool stamps `checked_at` when it
    stores the report."""
    if isinstance(sections, str):
        sections = [{"id": "text", "heading": "", "text": sections}]
    sections = list(sections or [])
    brief = brief or {}
    fmt = str(brief.get("format") or "blog")
    topic_norm = normalize_for_match(_topic_of(brief))
    sentences = _sentences_of(sections)
    word_count = sum(_words(s.get("text", "") or "") for s in sections)

    if word_count == 0:
        # Nothing to judge at all: every check excludes itself, so
        # the report carries geo_score None rather than a fake 0.
        raw = {key: (None, {}, "") for key in GEO_WEIGHTS}
    else:
        raw = {
        "answer_first": _check_answer_first(sections, sentences, topic_norm),
        "question_headings": _check_question_headings(sections, sentences,
                                                      topic_norm),
        "fact_density": _check_fact_density(sections, sentences, topic_norm,
                                            word_count),
        "attribution": _check_attribution(sections, sentences, topic_norm),
        "quotable_blocks": _check_quotable(sections, sentences, topic_norm,
                                           word_count),
        "definition_present": _check_definition(sections, sentences,
                                                topic_norm),
        "structure": _check_structure(sections, sentences, topic_norm,
                                      word_count),
        "faq_present": _check_faq(sections, sentences, topic_norm, fmt),
        }

    checks: list[dict] = []
    by_key: dict[str, dict] = {}
    present_weight = 0.0
    weighted_sum = 0.0
    applicable: list[str] = []
    excluded: list[str] = []
    for key, weight in GEO_WEIGHTS.items():
        score, evidence, suggestion = raw[key]
        if score is None:
            entry = {"key": key, "label": CHECK_LABELS[key],
                     "status": "not_applicable", "score": None,
                     "weight": weight, "evidence": evidence,
                     "suggestion": ""}
            excluded.append(key)
        else:
            score = round(float(score), 2)
            status = ("pass" if score >= PASS_AT
                      else "warn" if score >= WARN_AT else "fail")
            entry = {"key": key, "label": CHECK_LABELS[key],
                     "status": status, "score": score, "weight": weight,
                     "evidence": evidence, "suggestion": suggestion}
            applicable.append(key)
            present_weight += weight
            weighted_sum += score * weight
        checks.append(entry)
        by_key[key] = entry

    geo_score = (round(weighted_sum / present_weight, 2)
                 if present_weight else None)
    return {"version": GEO_VERSION, "weights_status": "provisional_v1",
            "geo_score": geo_score, "word_count": word_count,
            "checks": checks, "applicable_checks": applicable,
            "excluded_checks": excluded,
            "fixes": _fixes(sections, sentences, by_key)}


# ---------------------------------------------------------------------------
# Judged layer
# ---------------------------------------------------------------------------

def validate_geo_judgement(grades, issues) -> dict:
    """Validate + normalise a host-model GEO judgement (the judged
    layer — the core cannot judge citability, the writing model can,
    exactly the record_vision_check split). Raises ValueError with a
    specific message on any violation: grades must be exactly the
    three dimensions, each an int 0/1/2 (bools and floats rejected,
    as everywhere in this codebase); issues must come from
    JUDGE_ISSUES. Grade 0 dimensions are reported as `hard_flags` —
    hard INSIDE the GEO report only, never gate-blocking."""
    if not isinstance(grades, dict) or set(grades) != set(JUDGE_DIMS):
        raise ValueError(f"grades must name exactly the GEO dimensions "
                         f"{JUDGE_DIMS}")
    for dim in JUDGE_DIMS:
        grade = grades[dim]
        if isinstance(grade, bool) or not isinstance(grade, int) \
                or grade not in GRADE_SCORES:
            raise ValueError(f"grade for {dim} must be 0, 1 or 2, "
                             f"got {grade!r}")
    issues = list(issues or [])
    for issue in issues:
        if issue not in JUDGE_ISSUES:
            raise ValueError(f"unknown GEO judgement issue: {issue!r} "
                             f"(known: {JUDGE_ISSUES})")
    scores = {dim: GRADE_SCORES[grades[dim]] for dim in JUDGE_DIMS}
    return {"grades": {dim: grades[dim] for dim in JUDGE_DIMS},
            "issues": issues,
            "scores": scores,
            "judgement_score": round(sum(scores.values()) / len(scores), 2),
            "hard_flags": [dim for dim in JUDGE_DIMS if grades[dim] == 0]}


def geo_summary(geo_state) -> dict | None:
    """The additive summary other reports may carry for a draft whose
    GEO layer has run: {geo_score, judgement_score, hard_flags,
    checked_at} — or None when the draft carries no GEO report, so
    callers add the field only when there is something to add."""
    if not isinstance(geo_state, dict):
        return None
    report = geo_state.get("report")
    if not isinstance(report, dict):
        return None
    judgement = geo_state.get("judgement")
    return {"geo_score": report.get("geo_score"),
            "judgement_score": (judgement.get("judgement_score")
                                if isinstance(judgement, dict) else None),
            "hard_flags": (judgement.get("hard_flags", [])
                           if isinstance(judgement, dict) else []),
            "checked_at": report.get("checked_at")}
