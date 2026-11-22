"""Article images: planning, SEO metadata checks, library ranking,
manifest building — all deterministic, no image generation inside.

WHY NO GENERATION HERE: the house architecture (see .pmcontext.md) is
"the host app's model is the writer, the core is the referee". Text
works that way — the model writes prose, the core lints/fact-checks
it — and images follow the same split (Smith's decision, 2026-11-22):
the host/writer generates or supplies the actual image files; this
module plans the slots, suggests filenames/alts, checks the metadata
against SEO rules, ranks the brand's own photo library for
library-first fulfilment, and packages the WordPress-export manifest.

SOURCE MODEL (hybrid): every plan slot prefers the CLIENT'S REAL
PHOTOS from the brand's image library (loaders image-library store);
AI generation fills the slots no library image fits. A library image
is fulfilled with source taken from its library entry; a directly
generated image is fulfilled with source "ai".

STYLE LOCK: a brand may carry an optional `visual` section (brand
YAML / brand draft): {style_summary, palette[], medium, mood,
composition, avoid[]}. Every planned prompt is scene + the brand's
rendered style block, byte-identical across slots — that shared block
is what makes a generated set look like ONE set. A directly generated
image records the full prompt actually used (`prompt_used`); the
checker verifies the brand's current block is in it
(`style_not_locked`). Library images need no prompt: they were
curated into the library already.

VISION CHECK (judged layer): the core cannot see images, the host
model can — the same split as the Quality Profile's judged axes. A
fulfilled image may carry a `vision_check` record {content_grade,
style_grade, notes, checked_at} with rubric grades 0/1/2 supplied by
the writer AFTER looking at the image (2 = matches intent and brand
style, 1 = usable with reservations, 0 = wrong content / off-style).
The core enforces the RECORD, never the seeing: in complete mode an
unchecked image is flagged; a grade 0 is a hard issue whose remedy is
regenerating/replacing that one image.

ADVISORY BOUNDARY: this layer never enters final_gate's verdict in
this round (like style_match). `ready` on the manifest is the image
layer's own completeness signal for the WordPress exporter.
"""
from __future__ import annotations

import os
import re

from .linter import normalize_for_match
from .loaders import slugify

# --- design constants -------------------------------------------------------
# One featured image per article + at most 5 in-content images: beyond
# ~6 images an article's page weight and layout cost outgrow the SEO /
# readability benefit, and the WordPress draft stays reviewable.
MAX_IN_CONTENT = 5
# Alt text: >125 chars is flagged (screen readers truncate around
# there and long alts read as stuffing); >200 is a hard issue — at
# that length it is a paragraph, not a description of the image.
ALT_SOFT_LIMIT = 125
ALT_HARD_LIMIT = 200
# Advisory local-file size budgets (page speed): the featured image
# renders largest, so it gets the bigger budget.
FEATURED_SIZE_BUDGET_KB = 200
IN_CONTENT_SIZE_BUDGET_KB = 150
# Library matching: at least 35% of a slot's query tokens must appear
# in a library entry's tags+description. Query sets are small
# (heading + intent + keyword ≈ 5-10 tokens), so 0.35 means roughly a
# third of the slot's concepts are covered — strict enough that a
# generic entry sharing only the brief keyword does not auto-fit
# every slot, loose enough for short Vietnamese headings.
LIBRARY_MATCH_THRESHOLD = 0.35

ASPECT_FEATURED = "16:9"      # cover image, social/OG friendly
ASPECT_IN_CONTENT = "4:3"     # sits inside the article column
TARGET_FORMAT = "webp"
SOURCE_PREFERENCE = "client_photo_or_ai"
_IMAGE_EXTS = {".webp", ".jpg", ".jpeg", ".png"}
_FILENAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\.(webp|jpg|jpeg|png)$")
_STYLE_MARKER = "Brand visual style:"
# Function words (post-normalisation) carry no topical signal for
# library ranking; without dropping them, "cà phê" + glue words match
# almost everything in a coffee brand's library.
_RANK_STOPWORDS = {
    "va", "cua", "cho", "trong", "khi", "la", "cac", "nhung", "duoc",
    "mot", "voi", "de", "tren", "duoi", "nay", "do", "tai", "vao",
    "the", "a", "an", "of", "for", "in", "on", "to",
}


# --- brand visual style -----------------------------------------------------

def visual_of(brand: dict | None) -> dict:
    """The brand's optional `visual` section, {} when absent. The
    loader tolerates a missing section (older brands, builder drafts
    without one) — no style block, no style lock, everything else
    works."""
    if not isinstance(brand, dict):
        return {}
    visual = brand.get("visual")
    return visual if isinstance(visual, dict) else {}


def style_block(visual: dict | None) -> str:
    """Render a brand `visual` dict as THE style block appended to
    every generation prompt. Deterministic field order; absent fields
    are skipped. '' when the brand carries no usable visual section."""
    visual = visual or {}
    parts: list[str] = []
    if visual.get("medium"):
        parts.append(f"medium: {visual['medium']}")
    palette = visual.get("palette") or []
    if palette:
        parts.append("palette: " + ", ".join(str(c) for c in palette))
    if visual.get("mood"):
        parts.append(f"mood: {visual['mood']}")
    if visual.get("composition"):
        parts.append(f"composition: {visual['composition']}")
    if visual.get("style_summary"):
        parts.append(str(visual["style_summary"]))
    avoid = visual.get("avoid") or []
    if avoid:
        parts.append("avoid: " + ", ".join(str(a) for a in avoid))
    if not parts:
        return ""
    return f"{_STYLE_MARKER} " + "; ".join(parts) + "."


def extract_style_block(prompt_used: str | None) -> str:
    """The style block inside a recorded prompt: the template appends
    it LAST, so it runs from the marker to the end of the prompt.
    '' when the prompt carries no block."""
    text = prompt_used or ""
    idx = text.find(_STYLE_MARKER)
    return text[idx:].strip() if idx >= 0 else ""


# --- planning -----------------------------------------------------------------

def plan_images(sections: list[dict], brief: dict,
                brand: dict | None = None) -> list[dict]:
    """Plan the image slots for a draft: one featured + one in-content
    slot per section, capped at MAX_IN_CONTENT in-content slots
    (sections beyond the cap get no slot — documented, not silent:
    the cap is a module constant the README names).

    Each item: {id, role, section_id, section_heading, section_intent,
    purpose, prompt, style_block, source_preference, aspect, format,
    size_budget_kb}. The prompt is a deterministic template (scene +
    the brand's style block) — no LLM is involved in planning."""
    brand = brand or {}
    brand_name = (brand.get("brand") or {}).get("name", "")
    block = style_block(visual_of(brand))
    title = (brief.get("title") or brief.get("topic") or "").strip()
    topic = (brief.get("topic") or title).strip()
    keyword = (brief.get("keyword") or topic).strip()

    def _prompt(scene: str) -> str:
        return f"{scene}\n{block}" if block else scene

    plan: list[dict] = [{
        "id": "img-featured", "role": "featured", "section_id": None,
        "section_heading": None, "section_intent": None,
        "purpose": "cover image representing the article's main topic",
        "prompt": _prompt(
            f"Article cover image: '{title or topic}'. "
            f"Central subject: {keyword}."
            + (f" Brand: {brand_name}." if brand_name else "")),
        "style_block": block, "source_preference": SOURCE_PREFERENCE,
        "aspect": ASPECT_FEATURED, "format": TARGET_FORMAT,
        "size_budget_kb": FEATURED_SIZE_BUDGET_KB,
    }]
    for sec in sections[:MAX_IN_CONTENT]:
        heading = sec.get("heading", "")
        intent = sec.get("intent", "")
        scene = (f"Illustration for the section '{heading}' of an article "
                 f"about {topic}."
                 + (f" Section intent: {intent}." if intent else "")
                 + (f" Brand: {brand_name}." if brand_name else ""))
        plan.append({
            "id": f"img-{sec.get('id', '')}", "role": "in_content",
            "section_id": sec.get("id", ""), "section_heading": heading,
            "section_intent": intent,
            "purpose": f"supporting image for section '{heading}'",
            "prompt": _prompt(scene),
            "style_block": block, "source_preference": SOURCE_PREFERENCE,
            "aspect": ASPECT_IN_CONTENT, "format": TARGET_FORMAT,
            "size_budget_kb": IN_CONTENT_SIZE_BUDGET_KB,
        })
    return plan


# --- suggestions ---------------------------------------------------------------

def suggest_filename(item: dict, brief: dict) -> str:
    """SEO filename: slugified brief keyword first, then the section
    slug for in-content images, .webp. Lowercase ASCII, hyphenated —
    the exact shape check_images enforces."""
    keyword_slug = slugify(brief.get("keyword") or brief.get("topic") or "",
                           fallback="")
    parts = [keyword_slug] if keyword_slug else []
    if item.get("role") != "featured" and item.get("section_heading"):
        parts.append(slugify(item["section_heading"], fallback=""))
    stem = "-".join(p for p in parts if p)
    if not stem:
        stem = "featured-image" if item.get("role") == "featured" \
            else "article-image"
    return f"{stem}.webp"


def suggest_alt(item: dict, brief: dict, description: str = "") -> str:
    """Template-built STARTING-POINT alt text, used when the writer
    supplies none (or when a library image is auto-fulfilled). The
    writer is expected to replace it with alt written from the actual
    image; stored records mark suggested alts (`alt_suggested`).

    With a library `description`, the suggestion is its first
    sentence — the description says what the photo actually shows,
    which is what alt is for. Capped at ALT_SOFT_LIMIT chars."""
    if description and description.strip():
        text = re.split(r"(?<=[.!?])\s+", description.strip())[0].strip()
        return _cap_alt(text)
    topic = (brief.get("topic") or brief.get("title") or "").strip()
    if item.get("role") == "featured":
        title = (brief.get("title") or topic).strip()
        return _cap_alt(f"Hình ảnh bìa cho bài viết {title}")
    heading = item.get("section_heading") or ""
    return _cap_alt(f"Minh họa cho phần {heading} trong bài viết về {topic}")


def _cap_alt(text: str) -> str:
    if len(text) <= ALT_SOFT_LIMIT:
        return text
    return text[: ALT_SOFT_LIMIT - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


# --- library ranking -------------------------------------------------------------

def _token_set(text: str) -> set[str]:
    return {t for t in normalize_for_match(text).split()
            if len(t) > 1 and t not in _RANK_STOPWORDS}


def slot_query_tokens(item: dict, brief: dict) -> set[str]:
    """The topical tokens a plan slot is matched against: featured →
    title + topic + keyword; in-content → section heading + intent +
    keyword."""
    if item.get("role") == "featured":
        text = " ".join([brief.get("title") or "", brief.get("topic") or "",
                         brief.get("keyword") or ""])
    else:
        text = " ".join([item.get("section_heading") or "",
                         item.get("section_intent") or "",
                         brief.get("keyword") or ""])
    return _token_set(text)


def library_score(entry: dict, query_tokens: set[str]) -> tuple[float, int]:
    """(score, hits): the fraction of the slot's query tokens covered
    by the entry's tags + description tokens."""
    if not query_tokens:
        return 0.0, 0
    entry_tokens = _token_set(
        " ".join(str(t) for t in entry.get("tags") or [])
        + " " + str(entry.get("description") or ""))
    hits = len(query_tokens & entry_tokens)
    return hits / len(query_tokens), hits


def best_library_match(item: dict, brief: dict, entries: list[dict],
                       used_ids: set[str]) -> tuple[dict, float] | None:
    """The best unused library entry for a slot, or None when nothing
    clears LIBRARY_MATCH_THRESHOLD. Deterministic order: higher
    score, more hits, CLIENT entries before AI ones (real photos are
    the point of the library), then entry id."""
    query = slot_query_tokens(item, brief)
    ranked = []
    for entry in entries:
        if entry.get("id") in used_ids:
            continue
        score, hits = library_score(entry, query)
        if hits >= 1 and score >= LIBRARY_MATCH_THRESHOLD:
            ranked.append((entry, score, hits))
    if not ranked:
        return None
    ranked.sort(key=lambda r: (-r[1], -r[2],
                               0 if r[0].get("source") == "client" else 1,
                               str(r[0].get("id", ""))))
    entry, score, _hits = ranked[0]
    return entry, score


# --- checking ---------------------------------------------------------------------

def _is_local_file(ref: str) -> bool:
    return bool(ref) and not ref.startswith(("http://", "https://")) \
        and os.path.exists(ref)


def check_images(plan: list[dict], images: dict, brief: dict,
                 brand: dict | None = None,
                 complete: bool = False) -> tuple[list[dict], list[dict]]:
    """Check fulfilled images against the plan and the SEO rules.

    Returns (issues, flagged) in the house checker shape — issues are
    hard (the manifest is not ready while any exist), flagged are
    warnings. `complete=True` additionally treats unfulfilled plan
    slots as missing_image issues and ungraded images as
    vision_unchecked warnings.

    Hard issues: missing_image (complete), missing_alt, duplicate_alt
    (diacritics-insensitive), stuffed_alt (the brief keyword repeated,
    or an alt made of nothing but the keyword), bad_filename,
    featured_filename_missing_keyword, featured_reused (the featured
    ref used again in-content), too_many_images (fulfilments outside
    the plan), alt_too_long (>200), style_not_locked /
    style_block_mismatch (AI style lock), vision_content_mismatch /
    vision_style_mismatch (a recorded grade of 0).
    Flagged: alt_long (125-200), missing_caption (featured only),
    missing_description, file_over_budget / file_format (local files
    only), vision_unchecked (complete), vision_reservations (grade 1).
    """
    issues: list[dict] = []
    flagged: list[dict] = []
    plan_by_id = {p["id"]: p for p in plan}
    brand_block = style_block(visual_of(brand))
    keyword_norm = normalize_for_match(
        brief.get("keyword") or brief.get("topic") or "")
    keyword_tokens = set(keyword_norm.split())

    def _issue(image_id: str, type_: str, detail: str) -> None:
        issues.append({"image_id": image_id, "type": type_, "detail": detail})

    def _flag(image_id: str, type_: str, detail: str) -> None:
        flagged.append({"image_id": image_id, "type": type_, "detail": detail})

    fulfilled = {iid: img for iid, img in (images or {}).items()
                 if isinstance(img, dict)
                 and str(img.get("ref") or "").strip()}

    for iid in sorted(set(fulfilled) - set(plan_by_id)):
        _issue(iid, "too_many_images",
               "fulfilled image is not in the plan (more images than planned)")

    if complete:
        for item in plan:
            if item["id"] not in fulfilled:
                _issue(item["id"], "missing_image",
                       f"no image fulfilled for slot {item['id']} "
                       f"({item.get('purpose', '')})")

    featured_ref = None
    for item in plan:
        entry = fulfilled.get(item["id"])
        if item["role"] == "featured" and entry:
            featured_ref = entry.get("ref")

    seen_alts: dict[str, str] = {}
    ai_blocks: dict[str, str] = {}
    ordered_ids = [p["id"] for p in plan if p["id"] in fulfilled] + \
        sorted(set(fulfilled) - set(plan_by_id))
    for iid in ordered_ids:
        entry = fulfilled[iid]
        item = plan_by_id.get(iid)
        ref = str(entry.get("ref") or "")
        alt = str(entry.get("alt") or "").strip()

        # --- alt text ---
        if not alt:
            _issue(iid, "missing_alt", "image has no alt text")
        else:
            nalt = normalize_for_match(alt)
            if nalt in seen_alts:
                _issue(iid, "duplicate_alt",
                       f"alt duplicates {seen_alts[nalt]}'s alt text")
            else:
                seen_alts[nalt] = iid
            if keyword_norm and (
                    nalt.count(keyword_norm) > 1
                    or (keyword_tokens
                        and set(nalt.split()) <= keyword_tokens)):
                _issue(iid, "stuffed_alt",
                       "alt repeats the brief keyword or is only keywords")
            if len(alt) > ALT_HARD_LIMIT:
                _issue(iid, "alt_too_long",
                       f"alt is {len(alt)} chars (> {ALT_HARD_LIMIT})")
            elif len(alt) > ALT_SOFT_LIMIT:
                _flag(iid, "alt_long",
                      f"alt is {len(alt)} chars (> {ALT_SOFT_LIMIT})")

        # --- filename ---
        filename = str(entry.get("filename") or "")
        if not _FILENAME_RE.match(filename):
            _issue(iid, "bad_filename",
                   f"filename {filename!r} is not a lowercase hyphenated "
                   f"slug with a web image extension")
        elif item is not None and item["role"] == "featured":
            keyword_slug = slugify(brief.get("keyword")
                                   or brief.get("topic") or "", fallback="")
            if keyword_slug and keyword_slug not in filename.rsplit(".", 1)[0]:
                _issue(iid, "featured_filename_missing_keyword",
                       "featured filename does not contain the brief keyword")

        # --- featured reuse ---
        if item is not None and item["role"] != "featured" \
                and featured_ref and ref == featured_ref:
            _issue(iid, "featured_reused",
                   "the featured image is reused as an in-content image")

        # --- style lock (direct AI generations only; library images
        #     were curated already and client photos have no prompt) ---
        if entry.get("source") == "ai" and not entry.get("from_library"):
            prompt_used = str(entry.get("prompt_used") or "")
            block = extract_style_block(prompt_used)
            if block:
                ai_blocks[iid] = normalize_for_match(block)
            if not prompt_used.strip():
                _issue(iid, "style_not_locked",
                       "AI image has no recorded prompt_used — the style "
                       "lock cannot be verified")
            elif brand_block and normalize_for_match(block) \
                    != normalize_for_match(brand_block):
                _issue(iid, "style_not_locked",
                       "recorded prompt does not carry the brand's "
                       "current visual style block")

        # --- vision check (judged layer; every fulfilled image,
        #     library and client photos included) ---
        vc = entry.get("vision_check")
        grades_ok = isinstance(vc, dict) \
            and isinstance(vc.get("content_grade"), int) \
            and isinstance(vc.get("style_grade"), int)
        if not grades_ok:
            if complete:
                _flag(iid, "vision_unchecked",
                      "no vision_check recorded — the writer has not "
                      "graded this image yet")
        else:
            notes = str(vc.get("notes") or "")
            if vc["content_grade"] == 0:
                _issue(iid, "vision_content_mismatch",
                       "vision check: content grade 0 — wrong subject; "
                       "regenerate or replace this image"
                       + (f" ({notes})" if notes else ""))
            if vc["style_grade"] == 0:
                _issue(iid, "vision_style_mismatch",
                       "vision check: style grade 0 — off the brand "
                       "look; regenerate or replace this image"
                       + (f" ({notes})" if notes else ""))
            if vc["content_grade"] == 1 or vc["style_grade"] == 1:
                _flag(iid, "vision_reservations",
                      "vision check: usable with reservations"
                      + (f" — {notes}" if notes else ""))

        # --- flagged metadata gaps ---
        if item is not None and item["role"] == "featured" \
                and not str(entry.get("caption") or "").strip():
            _flag(iid, "missing_caption", "featured image has no caption")
        if not str(entry.get("description") or "").strip():
            _flag(iid, "missing_description",
                  "image has no media-library description")

        # --- local file checks (existing local paths only; URLs and
        #     not-yet-existing refs are the exporter's problem) ---
        if _is_local_file(ref):
            ext = os.path.splitext(ref)[1].lower()
            if ext not in _IMAGE_EXTS:
                _flag(iid, "file_format",
                      f"local file extension {ext or '(none)'} is not "
                      f"webp/jpg/png")
            budget_kb = item["size_budget_kb"] if item is not None \
                else IN_CONTENT_SIZE_BUDGET_KB
            size_kb = os.path.getsize(ref) / 1024
            if size_kb > budget_kb:
                _flag(iid, "file_over_budget",
                      f"local file is {size_kb:.0f}KB (> {budget_kb}KB "
                      f"budget for this slot)")

    # --- AI style blocks must agree with EACH OTHER too: two direct
    #     generations locked to different blocks are not one set ---
    distinct = set(ai_blocks.values())
    if len(distinct) > 1:
        first = next(iter(ai_blocks.values()))
        for iid, block in ai_blocks.items():
            if block != first:
                _issue(iid, "style_block_mismatch",
                       "this AI image's style block differs from the "
                       "other AI images' — the set is not style-locked "
                       "to one look")

    return issues, flagged


# --- manifest -----------------------------------------------------------------

def build_manifest(plan: list[dict], images: dict) -> list[dict]:
    """The WordPress-export-ready manifest: fulfilled images only, in
    plan order, in the WP media field names (alt_text / caption /
    description) the future exporter uploads."""
    out = []
    for item in plan:
        entry = (images or {}).get(item["id"])
        if not isinstance(entry, dict) \
                or not str(entry.get("ref") or "").strip():
            continue
        out.append({
            "role": item["role"],
            "section_id": item["section_id"],
            "ref": entry.get("ref", ""),
            "filename": entry.get("filename", ""),
            "alt_text": entry.get("alt", ""),
            "caption": entry.get("caption", ""),
            "description": entry.get("description", ""),
        })
    return out
