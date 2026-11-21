"""Brand Builder — extract a DRAFT Brand Voice profile from a website.

Smith's request: build a Brand FAST by analyzing a target site (the user's
own client site) or a competitor site. The output is a **draft** in the
exact structure of data/brand_mocbrew.yaml (plus clearly-marked draft-only
fields) so the user edits/approves it quickly instead of writing a Brand
Voice profile from scratch.

Division of labour, same as Style Extraction:
- CODE (this module) does everything deterministic: corpus stats, the
  style fingerprint of the site's copy, the visual brand kit (with the
  source_url each value came from), voice-rule SUGGESTIONS derived from
  measured numbers, vocabulary candidates, and the site's own claims
  flagged for evidence review.
- The MODEL reads the pages and fills the small qualitative schema
  (tone traits, audience guess, positioning, differentiators) — code only
  validates and stores it.
- The HUMAN approves. Nothing here silently becomes an approved brand:
  every draft carries review_status="draft" and review_notes listing what
  was inferred and what the user must decide. Banned terms in particular
  can NOT be inferred from a site (a site never says what it forbids
  itself), so drafts start with banned_terms=[] and a note saying so.

COMPETITOR RULE: a competitor's copy is analysis material only. Drafts
built with source_type="competitor" mark every example passage
reference_only=True, and draft_to_brand_yaml REFUSES to convert a draft
that still contains reference_only examples — competitor text can never
become an approved brand by accident; the user must replace/paraphrase
those passages with the brand's own words first.

Fetching is NOT here: fetch_site.py (CLI helper, stdlib) turns a URL into
pages = [{url, title, text, headings, html_meta}]; the MCP server stays
offline and every function below takes that pages shape.
"""
from __future__ import annotations

import math
import re
import unicodedata
import urllib.parse
from collections import Counter

import yaml

from .fact_check import CLAIM_PHRASES
from .linter import split_sentences
from .loaders import load_brand, prohibited_claims
from .style import STOPWORDS, fingerprint

# ---------------------------------------------------------------------------
# Constants (documented heuristics — tune here, nowhere else)
# ---------------------------------------------------------------------------

#: The qualitative schema the MODEL fills after reading the site's pages.
#: Unknown keys are IGNORED on build (same documented choice as style.py);
#: missing keys default to empty.
QUAL_FIELDS = ["tone_traits", "audience_guess", "positioning", "differentiators"]

#: Generic navigation/boilerplate syllables: frequent on every Vietnamese
#: site, so they carry no domain signal for the vocabulary heuristic.
_GENERIC_WEB_WORDS = {
    "trang", "chủ", "giới", "thiệu", "sản", "phẩm", "dịch", "vụ",
    "liên", "hệ", "tin", "tức", "mua", "ngay", "xem", "thêm", "tìm",
    "kiếm", "đăng", "nhập", "giỏ", "hàng", "thanh", "toán", "hotline",
    "email", "website", "copyright", "bản", "quyền",
    # pronouns are voice signal (measured by the fingerprint), not domain
    # vocabulary:
    "mình", "bạn", "tôi", "ta", "họ", "chúng",
}

#: Title/heading words that are page furniture, not brand/product names —
#: trimmed from the edges of capitalized-name candidates.
_GENERIC_NAME_WORDS = {
    "Trang", "Chủ", "Giới", "Thiệu", "Sản", "Phẩm", "Dịch", "Vụ",
    "Liên", "Hệ", "Tin", "Tức", "Về", "Blog", "Home",
}

_WORD_RE = re.compile(r"[a-zà-ỹ0-9]+")
_CAP_TOKEN_RE = re.compile(r"[A-ZÀ-Ỹ][\wà-ỹ]*")

#: Superlative/claim patterns scanned on the site itself: the fact_checker's
#: evidence-requiring phrases + the house prohibited-claim patterns (from
#: the shipped brand). A site's own claims are REVIEW material, never
#: approved brand claims.
_DEFAULT_REQUIRES_EVIDENCE = ["mọi số liệu", "mọi khẳng định về sức khỏe",
                              "mọi so sánh với đối thủ"]


def _slugify(name: str) -> str:
    nfkd = unicodedata.normalize("NFD", (name or "").lower().replace("đ", "d"))
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-") or "brand"


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _claim_patterns() -> list[str]:
    patterns = list(CLAIM_PHRASES)
    try:
        patterns += prohibited_claims(load_brand())
    except Exception:
        pass
    out: list[str] = []
    for p in patterns:
        if p not in out:
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------

def site_corpus(pages: list[dict]) -> dict:
    """Join the pages into one corpus with per-page stats."""
    pages = [p for p in pages or [] if p and p.get("text")]
    per_page = []
    for p in pages:
        text = p.get("text", "")
        per_page.append({"url": p.get("url", ""), "title": p.get("title", ""),
                         "word_count": len(text.split()),
                         "sentence_count": len(split_sentences(text))})
    all_text = "\n\n".join(p.get("text", "") for p in pages)
    return {"all_text": all_text,
            "total_words": len(all_text.split()),
            "page_count": len(pages),
            "per_page": per_page}


# ---------------------------------------------------------------------------
# Visual brand kit — every value carries its source_url evidence
# ---------------------------------------------------------------------------

def _title_brand_segment(pages: list[dict]) -> str | None:
    """The title segment repeated across pages ("Trang Chủ | Sớm Coffee"
    and "Giới Thiệu | Sớm Coffee" share "Sớm Coffee") — the brand name when
    og:site_name is absent."""
    counts: Counter = Counter()
    source: dict[str, str] = {}
    for p in pages:
        segments = re.split(r"\s*[|—–·:]\s*|\s+-\s+", p.get("title", ""))
        if len(segments) < 2:
            continue  # no separator -> the whole title is a page title, not a brand segment
        for seg in segments:
            seg = seg.strip()
            if seg:
                counts[seg] += 1
                source.setdefault(seg, p.get("url", ""))
    for seg, n in counts.most_common():
        if n >= 2 or len(pages) == 1:
            return seg
    return None


def extract_brand_kit(pages: list[dict]) -> dict:
    """Merge the pages' html_meta into one visual brand kit.

    Name precedence: og:site_name > repeated title segment > domain label.
    Colors/fonts are merged frequency tables (top 6 / top 3). Scalar fields
    are {value, source_url} — the first page that provides the value is the
    evidence."""
    pages = [p for p in pages or [] if p]
    metas = [(p.get("url", ""), p.get("html_meta") or {}) for p in pages]

    def first(key: str):
        for url, meta in metas:
            if meta.get(key):
                return {"value": meta[key], "source_url": url}
        return {"value": None, "source_url": ""}

    name = first("og_site_name")
    if not name["value"]:
        segment = _title_brand_segment(pages)
        if segment:
            src = next((p.get("url", "") for p in pages if segment in p.get("title", "")), "")
            name = {"value": segment, "source_url": src}
    if not name["value"] and pages:
        host = urllib.parse.urlparse(pages[0].get("url", "")).netloc or "site"
        label = host.removeprefix("www.").split(".")[0]
        name = {"value": label.capitalize() or "Brand", "source_url": pages[0].get("url", "")}

    color_counts: Counter = Counter()
    font_counts: Counter = Counter()
    for _url, meta in metas:
        for c in meta.get("colors", []):
            color_counts[c["hex"]] += int(c.get("count", 1))
        for f in meta.get("fonts", []):
            font_counts[f["family"]] += int(f.get("count", 1))

    return {
        "brand_name": name,
        "logo_url": first("logo_url"),
        "theme_color": first("theme_color"),
        "og_image": first("og_image"),
        "colors": [{"hex": h, "count": n} for h, n in
                   sorted(color_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:6]],
        "fonts": [{"family": f, "count": n} for f, n in
                  sorted(font_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]],
    }


# ---------------------------------------------------------------------------
# Voice-rule suggestions — derived from the measured fingerprint
# ---------------------------------------------------------------------------

def suggest_voice_rules(corpus_text: str) -> dict:
    """Deterministic voice suggestions from style.fingerprint().

    - sentence median range: [floor(median*0.85), ceil(median*1.15)],
      clamped to [3, 30] and at least 2 wide — a STARTING range around how
      the site actually writes, for the user to tighten.
    - pov: first_person_rate >= 1.5/100 words → first-person observed;
      otherwise the copy speaks in third person / brand name.
    - reader_address: "bạn" when reader_rate >= 0.8/100 words, else None.
    - exclamation: a high rate is reported as a review NOTE, never copied
      into formatting_rules — hype punctuation must be a deliberate choice.
    """
    fp = fingerprint(corpus_text or "")
    median = fp["sentence_len"]["median"]
    lo = max(3, math.floor(median * 0.85)) if median else 11
    hi = min(30, math.ceil(median * 1.15)) if median else 14
    if hi <= lo:
        hi = lo + 2

    fp_rate = fp["first_person_rate"]
    if fp_rate >= 1.5:
        pov = "first-person"
        pov_note = (f"site copy speaks in first person ({fp_rate}/100 words "
                    f"of mình/tôi) — confirm WHICH pronoun the brand uses "
                    f"before approving")
    else:
        pov = "third-person"
        pov_note = (f"first person is rare in the site copy "
                    f"({fp_rate}/100 words) — the brand speaks about itself "
                    f"in third person / by name")

    notes: list[str] = []
    excl = fp["exclamation_rate"]
    if excl >= 10:
        notes.append(
            f"site copy uses exclamation marks frequently ({excl} per 100 "
            f"sentences) — decide deliberately whether to keep this; it is "
            f"NOT copied into formatting_rules automatically")
    if fp["word_count"] < 800:
        notes.append(
            f"corpus is small ({fp['word_count']} words) — these suggestions "
            f"are a rough starting point, not a measurement to trust blindly")
    notes.append(pov_note)

    return {
        "sentence_rules": {"median_words_default": [lo, hi],
                           "max_sentence_words": 25},
        "pov": pov,
        "pov_note": pov_note,
        "reader_address": "bạn" if fp["reader_rate"] >= 0.8 else None,
        "formatting_notes": notes,
        "fingerprint_summary": {
            "sentence_median": median, "sentence_stdev": fp["sentence_len"]["stdev"],
            "first_person_rate": fp_rate, "reader_rate": fp["reader_rate"],
            "exclamation_rate": excl, "question_rate": fp["question_rate"],
            "number_density": fp["number_density"],
            "word_count": fp["word_count"]},
    }


# ---------------------------------------------------------------------------
# Vocabulary candidates
# ---------------------------------------------------------------------------

def _capitalized_names(pages: list[dict]) -> list[str]:
    """Capitalized multiword names from page titles + headings, exact
    casing preserved ("Đỉnh Cao Gold Blend"). Generic furniture words are
    trimmed from the edges; sequences made only of them are dropped."""
    found: list[str] = []
    for p in pages or []:
        texts = [p.get("title", "")] + list(p.get("headings", []) or [])
        for text in texts:
            tokens = text.replace("|", " ").split()
            run: list[str] = []

            def flush(run=run):
                trimmed = list(run)
                while trimmed and trimmed[0] in _GENERIC_NAME_WORDS:
                    trimmed.pop(0)
                while trimmed and trimmed[-1] in _GENERIC_NAME_WORDS:
                    trimmed.pop()
                if len(trimmed) >= 2:
                    name = " ".join(trimmed)
                    if name not in found:
                        found.append(name)

            for tok in tokens:
                clean = tok.strip(",.;:()")
                if clean and _CAP_TOKEN_RE.match(clean) and clean[0].isupper():
                    run.append(clean)
                else:
                    flush(run)
                    run = []
            flush(run)
    return found


def extract_vocabulary(pages: list[dict]) -> dict:
    """Vocabulary candidates from the corpus.

    Heuristic (documented, deliberately simple): domain-looking terms are
    the frequent bigrams of the corpus whose syllables are all content
    words (not stopwords, not generic web/boilerplate words, no digits) —
    in Vietnamese most domain terms are two-syllable compounds ("cà phê",
    "rang xay", "hậu vị"). `use` takes the top bigrams; the remaining
    frequent content words become glossary candidates for the user to
    define. `exact` is the brand name plus capitalized product-like names
    from titles/headings, casing preserved."""
    corpus = site_corpus(pages)
    text = corpus["all_text"]
    tokens = _tokens(text)

    def content(tok: str) -> bool:
        return (tok not in STOPWORDS and tok not in _GENERIC_WEB_WORDS
                and not tok.isdigit() and len(tok) >= 2)

    bigrams: Counter = Counter()
    for a, b in zip(tokens, tokens[1:]):
        if content(a) and content(b):
            bigrams[f"{a} {b}"] += 1
    use = [{"term": t, "count": n, "instead_of": ""}
           for t, n in bigrams.most_common(8) if n >= 2]

    used = {u["term"] for u in use}
    singles: Counter = Counter(t for t in tokens if content(t))
    glossary = [{"term": t, "count": n} for t, n in singles.most_common(20)
                if t not in " ".join(used).split()][:10]

    kit = extract_brand_kit(pages)
    exact: list[str] = []
    if kit["brand_name"]["value"]:
        exact.append(kit["brand_name"]["value"])
    for name in _capitalized_names(pages):
        if name not in exact:
            exact.append(name)
    return {"use": use, "exact": exact[:8], "glossary_candidates": glossary}


# ---------------------------------------------------------------------------
# The site's own claims — review material, never approved claims
# ---------------------------------------------------------------------------

def find_site_claims(pages: list[dict]) -> list[dict]:
    """Sentences on the site containing superlative/claim phrases (the
    fact_check CLAIM_PHRASES + the house prohibited-claim patterns). Each
    hit: {claim (the sentence), phrase (what matched), source_url,
    risk: "needs_evidence"}. These go to the draft's review list — a site
    asserting something does not make it an approved brand claim."""
    patterns = _claim_patterns()
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for p in pages or []:
        for sentence in split_sentences(p.get("text", "")):
            low = sentence.lower()
            hit = next((ph for ph in patterns if ph in low), None)
            if hit:
                claim = sentence.strip()[:200]
                key = (claim, p.get("url", ""))
                if key not in seen:
                    seen.add(key)
                    out.append({"claim": claim, "phrase": hit,
                                "source_url": p.get("url", ""),
                                "risk": "needs_evidence"})
    return out


# ---------------------------------------------------------------------------
# Draft building — code validates the qualitative schema like style.py
# ---------------------------------------------------------------------------

def _validate_qualitative(qualitative: dict | None) -> dict:
    q = qualitative or {}
    out: dict = {}
    for field in QUAL_FIELDS:
        value = q.get(field)
        if field == "tone_traits":
            traits = []
            for item in value or []:
                if isinstance(item, dict):
                    traits.append({"dimension": str(item.get("dimension", "")),
                                   "position": str(item.get("position", "")),
                                   "do": str(item.get("do", "")),
                                   "dont": str(item.get("dont", ""))})
                elif item:
                    traits.append({"dimension": str(item), "position": "",
                                   "do": "", "dont": ""})
            out[field] = traits
        elif field in ("audience_guess", "positioning"):
            out[field] = str(value or "")
        else:  # differentiators
            out[field] = [str(v) for v in (value or [])]
    return out


def _example_passages(pages: list[dict], limit: int = 3) -> list[dict]:
    """Candidate example passages (<=120 words) from the site's own pages:
    paragraphs (or sentence groups) of 30-120 words, first good ones first,
    each with its source_url."""
    out: list[dict] = []
    for p in pages or []:
        text = p.get("text", "")
        paragraphs = [x.strip() for x in re.split(r"\n\s*\n", text) if x.strip()]
        if len(paragraphs) < 2:
            sentences = split_sentences(text)
            paragraphs = [" ".join(sentences[i:i + 4])
                          for i in range(0, len(sentences), 4)]
        for para in paragraphs:
            words = para.split()
            if 30 <= len(words) <= 120:
                out.append({"text": para, "source_url": p.get("url", "")})
                break  # one passage per page keeps the set varied
        if len(out) >= limit:
            break
    return out


def build_brand_draft(pages: list[dict], qualitative: dict | None = None,
                      source_type: str = "own_site",
                      brand_name: str | None = None) -> dict:
    """Build the DRAFT brand profile from fetched pages.

    The returned dict follows data/brand_mocbrew.yaml's structure (brand /
    voice / examples / anti_examples / claims_and_guardrails) plus the
    draft extensions: vocabulary, brand_kit, qualitative, review_notes —
    and, for competitors, a top-level competitor_warning. It is a draft:
    brand.review_status == "draft" until a human approves it."""
    if source_type not in ("own_site", "competitor"):
        raise ValueError(f"source_type must be 'own_site' or 'competitor', got {source_type!r}")
    pages = [p for p in pages or [] if p]
    corpus = site_corpus(pages)
    kit = extract_brand_kit(pages)
    name = brand_name or kit["brand_name"]["value"] or "Brand"
    suggestions = suggest_voice_rules(corpus["all_text"])
    vocab = extract_vocabulary(pages)
    claims = find_site_claims(pages)
    qual = _validate_qualitative(qualitative)

    try:
        default_prohibited = prohibited_claims(load_brand())
        default_requires = list(load_brand().get("claims_and_guardrails", {})
                                .get("requires_evidence", _DEFAULT_REQUIRES_EVIDENCE))
    except Exception:
        default_prohibited, default_requires = [], _DEFAULT_REQUIRES_EVIDENCE

    is_competitor = source_type == "competitor"
    examples = []
    for ex in _example_passages(pages):
        entry = {"format": "site_copy", "text": ex["text"],
                 "source_url": ex["source_url"],
                 "why_it_works": "đoạn mẫu lấy từ chính site — user duyệt trước khi dùng"}
        if is_competitor:
            entry["reference_only"] = True
        examples.append(entry)

    review_notes = [
        "Đây là bản DRAFT trích xuất tự động — chưa phải brand chính thức. "
        "User sửa và duyệt trước khi dùng.",
        "banned_terms đang TRỐNG: từ cấm không suy ra được từ site (site "
        "không bao giờ tự nói mình cấm gì) — user phải tự thêm.",
        "voice.traits do model đề xuất (qualitative) — cần user duyệt từng "
        "trait, không mặc định đúng.",
        f"pov '{suggestions['pov']}' và sentence_rules được SUY từ số đo "
        f"văn phong của site ({suggestions['pov_note']}) — xác nhận lại "
        f"trước khi chốt.",
    ]
    review_notes += suggestions["formatting_notes"]
    if claims:
        review_notes.append(
            f"site có {len(claims)} claim cần kiểm chứng bằng chứng — xem "
            f"claims_and_guardrails.site_claims_to_review; KHÔNG claim nào "
            f"được duyệt sẵn.")
    if corpus["total_words"] < 800:
        review_notes.append(
            f"corpus chỉ có {corpus['total_words']} từ — nên fetch thêm "
            f"trang (blog/sản phẩm) để hồ sơ tin cậy hơn.")
    competitor_warning = None
    if is_competitor:
        competitor_warning = (
            "COMPETITOR SITE: hồ sơ này chỉ để PHÂN TÍCH đối thủ (traits, "
            "định vị, khoảng trống). Mọi example đều là reference_only — "
            "phải thay/diễn đạt lại bằng lời của brand mình trước khi "
            "duyệt; draft_to_brand_yaml sẽ TỪ CHỐI chuyển đổi khi còn "
            "example reference_only.")
        review_notes.append(competitor_warning)

    draft = {
        "brand": {
            "id": _slugify(name),
            "name": name,
            "simulated": False,
            "version": 0,
            "review_status": "draft",
            "source_type": source_type,
            "source_urls": [p.get("url", "") for p in pages],
        },
        "voice": {
            "traits": qual["tone_traits"],
            "pov": suggestions["pov"],
            "reader_address": suggestions["reader_address"],
            "sentence_rules": {
                "median_words_default": suggestions["sentence_rules"]["median_words_default"],
                "median_words_by_format": {},
                "max_sentence_words": suggestions["sentence_rules"]["max_sentence_words"],
            },
            "formatting_rules": [],
            "banned_terms": [],
            "prohibited_claims": default_prohibited,
        },
        "vocabulary": {
            "use": vocab["use"],
            "exact": vocab["exact"],
            "banned": [],
            "glossary": vocab["glossary_candidates"],
        },
        "examples": examples,
        "anti_examples": [],
        "claims_and_guardrails": {
            "requires_evidence": default_requires,
            "prohibited_claims": default_prohibited,
            "unsupported_claim_marker": "[NEEDS SOURCE]",
            "site_claims_to_review": claims,
        },
        "brand_kit": kit,
        "qualitative": qual,
        "review_notes": review_notes,
    }
    if competitor_warning:
        draft["competitor_warning"] = competitor_warning
    return draft


# ---------------------------------------------------------------------------
# Conversion — draft -> approved brand YAML (with the competitor guard)
# ---------------------------------------------------------------------------

_DRAFT_ONLY_BRAND_KEYS = ("review_status", "source_type", "source_urls")


def draft_to_brand_yaml(draft: dict) -> str:
    """Convert an EDITED draft into brand YAML in brand_mocbrew.yaml's
    structure, minus draft-only fields (brand.review_status/source_type/
    source_urls, review_notes, claims_and_guardrails.site_claims_to_review,
    qualitative working notes).

    REFUSES (ValueError) when any example is still reference_only:
    competitor copy must be replaced/paraphrased by the user first — it can
    never become an approved brand by accident."""
    ref_only = [e for e in draft.get("examples", []) if e.get("reference_only")]
    if ref_only:
        raise ValueError(
            f"draft still contains {len(ref_only)} reference_only example(s) "
            f"from a competitor site — replace or paraphrase them with the "
            f"brand's own words (and remove the reference_only flag) before "
            f"converting to an approved brand")

    brand_in = draft.get("brand", {})
    brand = {"id": brand_in.get("id", "brand"), "name": brand_in.get("name", ""),
             "simulated": False, "version": 1}
    voice_in = draft.get("voice", {})
    rules_in = voice_in.get("sentence_rules", {})
    voice = {
        "traits": voice_in.get("traits", []),
        "pov": voice_in.get("pov", ""),
        "reader_address": voice_in.get("reader_address"),
        "sentence_rules": {
            "median_words_default": rules_in.get("median_words_default", [11, 14]),
            "median_words_by_format": rules_in.get("median_words_by_format", {}),
            "max_sentence_words": rules_in.get("max_sentence_words", 25),
        },
        "formatting_rules": voice_in.get("formatting_rules", []),
        "banned_terms": voice_in.get("banned_terms", []),
        "prohibited_claims": voice_in.get("prohibited_claims", []),
    }
    examples = [{"format": e.get("format", "site_copy"), "text": e.get("text", ""),
                 "why_it_works": e.get("why_it_works", "")}
                for e in draft.get("examples", [])]
    guard_in = draft.get("claims_and_guardrails", {})
    clean = {
        "brand": brand,
        "voice": voice,
        "vocabulary": draft.get("vocabulary",
                                {"use": [], "exact": [], "banned": [], "glossary": []}),
        "examples": examples,
        "anti_examples": draft.get("anti_examples", []),
        "claims_and_guardrails": {
            "requires_evidence": guard_in.get("requires_evidence",
                                               _DEFAULT_REQUIRES_EVIDENCE),
            "unsupported_claim_marker": guard_in.get("unsupported_claim_marker",
                                                     "[NEEDS SOURCE]"),
        },
        "brand_kit": draft.get("brand_kit", {}),
    }
    return yaml.safe_dump(clean, allow_unicode=True, sort_keys=False,
                          default_flow_style=False)


# ---------------------------------------------------------------------------
# Compare mode — my site vs a competitor, from the numbers
# ---------------------------------------------------------------------------

def _fp_metrics(fp: dict) -> dict:
    return {"sentence_median": fp["sentence_len"]["median"],
            "sentence_stdev": fp["sentence_len"]["stdev"],
            "short_sentence_share": fp["short_sentence_share"],
            "first_person_rate": fp["first_person_rate"],
            "reader_rate": fp["reader_rate"],
            "number_density": fp["number_density"],
            "exclamation_rate": fp["exclamation_rate"],
            "question_rate": fp["question_rate"]}


def _gaps(m: dict, claims_a: int, claims_b: int,
          label_a: str, label_b: str, vocab: dict) -> list[str]:
    """Observations computed from actual deltas (thresholds documented):
    first-person ratio >= 2x AND abs diff >= 0.5/100w; claims abs diff >= 3;
    sentence median abs diff >= 3 words; burstiness ratio >= 1.5x;
    exclamation abs diff >= 5/100 sentences; non-empty vocabulary split."""
    gaps: list[str] = []

    def side(name: str) -> str:
        return label_a if name == "a" else label_b

    fa, fb = m["first_person_rate"]["a"], m["first_person_rate"]["b"]
    hi, lo = max(fa, fb), min(fa, fb)
    if lo > 0 and hi / lo >= 2 and hi - lo >= 0.5:
        gaps.append(f"{side('a' if fa > fb else 'b')} uses first-person "
                    f"{hi / lo:.1f}x more than "
                    f"{side('b' if fa > fb else 'a')} "
                    f"({hi} vs {lo} per 100 words) — a warmer, more personal voice")
    elif hi - lo >= 0.5:
        gaps.append(f"first-person usage differs: {label_a} {fa} vs "
                    f"{label_b} {fb} per 100 words")

    if abs(claims_a - claims_b) >= 3:
        more, fewer = (("a", "b") if claims_a > claims_b else ("b", "a"))
        gaps.append(f"{side(more)} has {max(claims_a, claims_b)} superlative/"
                    f"evidence-requiring claims, {side(fewer)} has "
                    f"{min(claims_a, claims_b)} — {side(fewer)} is more restrained")

    med_a, med_b = m["sentence_median"]["a"], m["sentence_median"]["b"]
    if abs(med_a - med_b) >= 3:
        gaps.append(f"sentence length differs: {label_a} median {med_a} words vs "
                    f"{label_b} median {med_b} words — "
                    f"{label_a if med_a < med_b else label_b} reads punchier")

    sa, sb = m["sentence_stdev"]["a"], m["sentence_stdev"]["b"]
    if min(sa, sb) > 0 and max(sa, sb) / min(sa, sb) >= 1.5:
        gaps.append(f"rhythm burstiness differs: {label_a} stdev {sa} vs "
                    f"{label_b} stdev {sb} — "
                    f"{label_a if sa > sb else label_b} varies sentence length more")

    ea, eb = m["exclamation_rate"]["a"], m["exclamation_rate"]["b"]
    if abs(ea - eb) >= 5:
        gaps.append(f"exclamation usage differs sharply: {label_a} {ea} vs "
                    f"{label_b} {eb} per 100 sentences — "
                    f"{label_a if ea > eb else label_b} shouts, "
                    f"{label_b if ea > eb else label_a} stays calm")

    if vocab["a_only"] and vocab["b_only"]:
        gaps.append(f"vocabulary differs: {label_a} leans on "
                    f"{[w['word'] for w in vocab['a_only'][:5]]}, "
                    f"{label_b} leans on {[w['word'] for w in vocab['b_only'][:5]]}")
    return gaps


def compare_brand_voice(pages_a: list[dict], pages_b: list[dict]) -> dict:
    """Contrast two sites' voices (typically: mine vs a competitor).

    Side-by-side fingerprints for the 6 style_match metrics (+ sentence
    median/stdev and punctuation rates), vocabulary symmetric difference,
    claim counts, brand kits, and `gaps` — observations generated in code
    from the measured deltas, never hardcoded prose."""
    corpus_a, corpus_b = site_corpus(pages_a), site_corpus(pages_b)
    fp_a, fp_b = fingerprint(corpus_a["all_text"]), fingerprint(corpus_b["all_text"])
    ma, mb = _fp_metrics(fp_a), _fp_metrics(fp_b)
    metrics = {k: {"a": ma[k], "b": mb[k], "delta": round(mb[k] - ma[k], 2)}
               for k in ma}

    words_a = {w["word"] for w in fp_a["top_words"]}
    words_b = {w["word"] for w in fp_b["top_words"]}
    vocab = {
        "a_only": [{"word": w["word"], "count": w["count"]}
                   for w in fp_a["top_words"] if w["word"] not in words_b][:10],
        "b_only": [{"word": w["word"], "count": w["count"]}
                   for w in fp_b["top_words"] if w["word"] not in words_a][:10],
    }

    kit_a, kit_b = extract_brand_kit(pages_a), extract_brand_kit(pages_b)
    label_a = kit_a["brand_name"]["value"] or "Site A"
    label_b = kit_b["brand_name"]["value"] or "Site B"
    claims_a = find_site_claims(pages_a)
    claims_b = find_site_claims(pages_b)

    return {
        "labels": {"a": label_a, "b": label_b},
        "corpus": {"a": {"pages": corpus_a["page_count"], "words": corpus_a["total_words"]},
                   "b": {"pages": corpus_b["page_count"], "words": corpus_b["total_words"]}},
        "fingerprint": {"a": fp_a, "b": fp_b},
        "metrics": metrics,
        "sentence": {"median": {"a": ma["sentence_median"], "b": mb["sentence_median"]},
                     "stdev": {"a": ma["sentence_stdev"], "b": mb["sentence_stdev"]}},
        "vocabulary": vocab,
        "claims": {"a": len(claims_a), "b": len(claims_b),
                   "a_items": claims_a, "b_items": claims_b},
        "brand_kit": {"a": kit_a, "b": kit_b},
        "gaps": _gaps(metrics, len(claims_a), len(claims_b), label_a, label_b, vocab),
    }
