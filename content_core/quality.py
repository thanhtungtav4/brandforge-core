"""Quality Profile — the weighted quality layer of the final gate.

Smith's agreed design (21 Nov 2026, from the artifact's quality tab): a
draft is scored on SEVEN axes. At setup the user distributes a 100-point
budget across the axes (per-format presets ship below); at review time the
per-axis scores are shown target-vs-actual (dumbbell-ready). Two guardrails
are structural, not optional:

1. HARD criteria stay OUTSIDE the weighting — illegal claims, wrong facts
   and banned terms live in gates.py's hard layer and cannot be traded
   away by a high weighted total.
2. Every axis has a FLOOR. quality_pass requires the weighted total to
   clear PASS_TOTAL **and** no axis below its floor — a collapsed axis
   cannot be rescued by the others (anti-compensation).

Axis scoring — deterministic where a checker exists, judged where not:

- facts     MEASURED: fact_check over the full draft. 100 − 30 per
            unflagged issue − 10 per flagged [NEEDS SOURCE] item, floor 0.
- voice     MEASURED: the brand linter's score; when a Style Profile is
            supplied, the mean of brand score and style_match score.
- clarity   MEASURED: craft_check over the full text. (v1 mapping: craft
            currently blends clarity + rhythm + concreteness into one
            score; splitting it into a pure clarity metric is future work.)
- seo       MEASURED: seo_check (seo_pass) returns FIELDS, not a score —
            the axis maps its four boolean outcomes at 25 points each:
            keyword_in_title, headings_ok, meta_ok, and a non-empty
            title/topic. Honest derivation, documented here and in README.
- coherence JUDGED: supplied by the caller (the writing model) as a
- persuasion  rubric grade 0/1/2 per axis, mirroring the 0/1/2 rubrics
- depth       in the artifact's quality tab, converted
              {0: 20, 1: 60, 2: 95}. A missing judged axis is EXCLUDED
              and the weights renormalise over the present axes; the
              report lists measured / judged / missing axes explicitly.
              A judged score is NEVER invented here.

This module is pure computation + validation. Persistence of custom
profiles lives in loaders.py (save/load/list_quality_profiles), exactly
like style profiles.
"""
from __future__ import annotations

import hashlib
import json

from .craft import craft_check
from .fact_check import check_draft
from .linter import lint_draft
from .loaders import load_quality_profile
from .seo import seo_pass
from .style import style_match

# ---------------------------------------------------------------------------
# The seven axes (fixed order — the artifact's dumbbell/budget-bar order)
# ---------------------------------------------------------------------------

AXES: list[dict] = [
    {"key": "facts", "label": "Sự thật & bằng chứng"},
    {"key": "voice", "label": "Giọng brand & văn phong"},
    {"key": "clarity", "label": "Dễ hiểu"},
    {"key": "coherence", "label": "Mạch lạc & cấu trúc"},
    {"key": "seo", "label": "SEO / được tìm thấy"},
    {"key": "persuasion", "label": "Thuyết phục & CTA"},
    {"key": "depth", "label": "Độ gốc & chiều sâu"},
]
AXIS_KEYS: list[str] = [a["key"] for a in AXES]
AXIS_LABELS: dict[str, str] = {a["key"]: a["label"] for a in AXES}

MEASURED_AXES = ["facts", "voice", "clarity", "seo"]
JUDGED_AXES = ["coherence", "persuasion", "depth"]

#: Rubric grade (0/1/2, as in the artifact rubrics) → axis score.
GRADE_SCORES = {0: 20, 1: 60, 2: 95}

#: Per-format presets: weights summing to exactly 100 (the same numbers as
#: the artifact demo's budget bar).
PRESETS: dict[str, dict[str, int]] = {
    "blog": {"facts": 18, "voice": 14, "clarity": 14, "coherence": 14,
             "seo": 22, "persuasion": 8, "depth": 10},
    "product": {"facts": 24, "voice": 12, "clarity": 12, "coherence": 10,
                "seo": 10, "persuasion": 22, "depth": 10},
    "social": {"facts": 14, "voice": 26, "clarity": 14, "coherence": 10,
               "seo": 8, "persuasion": 20, "depth": 8},
    "email": {"facts": 16, "voice": 12, "clarity": 22, "coherence": 14,
              "seo": 6, "persuasion": 22, "depth": 8},
    "comparison": {"facts": 30, "voice": 10, "clarity": 12, "coherence": 14,
                   "seo": 12, "persuasion": 10, "depth": 12},
}

#: Axis floors — the same for every format in v1 (from the artifact demo).
FLOORS: dict[str, int] = {"facts": 70, "voice": 60, "clarity": 60,
                          "coherence": 60, "seo": 55, "persuasion": 55,
                          "depth": 60}

#: Weighted total a draft must reach (on top of clearing every floor).
PASS_TOTAL = 70

WEIGHT_MIN, WEIGHT_MAX = 2, 45


# ---------------------------------------------------------------------------
# Profiles: build / validate / resolve
# ---------------------------------------------------------------------------

def validate_profile(data: dict) -> dict:
    """Validate + normalize a quality profile dict.

    Rules: format must be a known preset format; weights keys == AXES,
    each within [2, 45], summing to exactly 100; floors (merged over the
    defaults) each within [0, 90]; pass_total within [0, 100].
    Raises ValueError with a specific message on any violation."""
    if not isinstance(data, dict):
        raise ValueError("quality profile must be a dict")
    fmt = data.get("format")
    if fmt not in PRESETS:
        raise ValueError(f"unknown format for quality profile: {fmt!r} "
                         f"(known: {sorted(PRESETS)})")
    weights_in = data.get("weights")
    if not isinstance(weights_in, dict) or set(weights_in) != set(AXIS_KEYS):
        raise ValueError(f"weights keys must be exactly the 7 axes {AXIS_KEYS}")
    weights: dict[str, float] = {}
    for key in AXIS_KEYS:
        w = weights_in[key]
        if not isinstance(w, (int, float)) or isinstance(w, bool):
            raise ValueError(f"weight for {key} must be a number, got {w!r}")
        if not (WEIGHT_MIN <= w <= WEIGHT_MAX):
            raise ValueError(f"weight for {key} must be within "
                             f"[{WEIGHT_MIN}, {WEIGHT_MAX}], got {w}")
        weights[key] = w
    total = sum(weights.values())
    if abs(total - 100) > 1e-9:
        raise ValueError(f"weights must sum to exactly 100, got {total}")

    floors = dict(FLOORS)
    for key, value in (data.get("floors") or {}).items():
        if key not in AXIS_KEYS:
            raise ValueError(f"unknown floor axis: {key!r}")
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not (0 <= value <= 90)):
            raise ValueError(f"floor for {key} must be within [0, 90], got {value!r}")
        floors[key] = value

    pass_total = data.get("pass_total", PASS_TOTAL)
    if (not isinstance(pass_total, (int, float)) or isinstance(pass_total, bool)
            or not (0 <= pass_total <= 100)):
        raise ValueError(f"pass_total must be within [0, 100], got {pass_total!r}")

    return {"id": str(data.get("id") or f"preset-{fmt}"),
            "format": fmt,
            "weights": weights,
            "floors": floors,
            "pass_total": pass_total,
            "source": data.get("source", "custom")}


def preset_profile(fmt: str) -> dict:
    """The shipped preset profile for a format (weights from PRESETS,
    default floors, default pass_total)."""
    if fmt not in PRESETS:
        raise ValueError(f"no quality preset for format: {fmt!r}")
    return validate_profile({"id": f"preset-{fmt}", "format": fmt,
                             "weights": dict(PRESETS[fmt]),
                             "floors": dict(FLOORS), "pass_total": PASS_TOTAL,
                             "source": "preset"})


def make_profile(fmt: str, weights: dict | None = None,
                 floors: dict | None = None, pass_total: float | None = None,
                 profile_id: str | None = None) -> dict:
    """Build a custom profile for a format (preset weights when none are
    given). The generated id is content-derived, like style profiles."""
    data: dict = {"format": fmt,
                  "weights": weights if weights is not None
                  else dict(PRESETS.get(fmt, {})),
                  "source": "custom"}
    if floors is not None:
        data["floors"] = floors
    if pass_total is not None:
        data["pass_total"] = pass_total
    profile = validate_profile(data)
    if profile_id:
        profile["id"] = profile_id
    else:
        digest = hashlib.sha1(json.dumps(
            {"f": fmt, "w": profile["weights"], "fl": profile["floors"],
             "pt": profile["pass_total"]},
            sort_keys=True).encode("utf-8")).hexdigest()[:6]
        profile["id"] = f"custom-{fmt}-{digest}"
    return profile


def get_profile(format_or_id: str) -> dict:
    """Resolve a profile: a format name (or 'preset-<format>') → the
    shipped preset; anything else → a stored custom profile by id.
    Raises ValueError when neither exists."""
    key = (format_or_id or "").strip()
    if key in PRESETS:
        return preset_profile(key)
    if key.startswith("preset-") and key[len("preset-"):] in PRESETS:
        return preset_profile(key[len("preset-"):])
    stored = load_quality_profile(key) if key else None
    if stored is not None:
        return validate_profile(stored)
    raise ValueError(f"unknown quality profile: {format_or_id!r} "
                     f"(use a format name {sorted(PRESETS)} or a saved custom id)")


# ---------------------------------------------------------------------------
# Axis scoring
# ---------------------------------------------------------------------------

def _as_sections(draft) -> list[dict]:
    """evaluate_quality accepts a draft as a list of section dicts
    ({id, heading?, text}) or as one plain text string."""
    if isinstance(draft, str):
        return [{"id": "text", "heading": "", "text": draft}]
    return list(draft)


def _facts_score(sections: list[dict], facts: dict, brand: dict) -> tuple[float, dict]:
    issues, flagged, _ = check_draft(sections, facts, brand)
    score = max(0.0, 100.0 - 30.0 * len(issues) - 10.0 * len(flagged))
    return score, {"unflagged_issues": len(issues), "flagged_claims": len(flagged)}


def _voice_score(sections: list[dict], fmt: str, brand: dict,
                 style_profile: dict | None) -> tuple[float, dict]:
    _violations, brand_score, _failing = lint_draft(sections, fmt, brand)
    detail: dict = {"brand_score": brand_score}
    if style_profile is not None:
        joined = "\n\n".join(s.get("text", "") for s in sections)
        match = style_match(joined, style_profile)
        detail["style_score"] = match["score"]
        detail["style_profile_id"] = style_profile.get("id")
        return round((brand_score + match["score"]) / 2, 2), detail
    return float(brand_score), detail


def _seo_score(sections: list[dict], brief: dict | None) -> tuple[float, dict]:
    """seo_pass returns fields, not a score: map its four boolean outcomes
    at 25 points each (keyword_in_title, headings_ok, meta_ok, non-empty
    title/topic). Documented derivation — see module docstring."""
    result = seo_pass(brief or {}, sections)
    parts = {"keyword_in_title": bool(result["keyword_in_title"]),
             "headings_ok": bool(result["headings_ok"]),
             "meta_ok": bool(result["meta_ok"]),
             "title_present": bool(result["title"])}
    return 25.0 * sum(parts.values()), {**parts, "seo": result}


def evaluate_quality(draft, fmt: str, profile: dict, judged: dict | None,
                     brand: dict, facts: dict,
                     style_profile: dict | None = None,
                     brief: dict | None = None) -> dict:
    """Score a draft on the profile's seven axes.

    `draft` is a list of section dicts or one text string. `profile` is a
    validated profile dict (see get_profile / make_profile). `judged` maps
    judged axis → rubric grade 0/1/2, supplied by the writing model; a
    missing judged axis is excluded and the weights renormalise over the
    present axes. Pure function of its inputs.

    Returns the dumbbell-ready report: per-axis {score, weight, floor,
    below_floor, source} + weighted_total + quality_pass."""
    profile = validate_profile(profile)
    sections = _as_sections(draft)
    weights, floors = profile["weights"], profile["floors"]

    measured: dict[str, tuple[float, dict]] = {
        "facts": _facts_score(sections, facts, brand),
        "voice": _voice_score(sections, fmt, brand, style_profile),
        "clarity": (float(craft_check(
            "\n\n".join(s.get("text", "") for s in sections), fmt)["score"]), {}),
        "seo": _seo_score(sections, brief),
    }

    judged = judged or {}
    for axis, grade in judged.items():
        if axis not in JUDGED_AXES:
            raise ValueError(f"{axis!r} is not a judged axis (judged: {JUDGED_AXES})")
        # bool is an int subclass (True == 1): without the explicit guard a
        # JSON true would silently grade as 1 (audit round 2).
        if isinstance(grade, bool) or grade not in GRADE_SCORES:
            raise ValueError(f"judged grade for {axis} must be 0, 1 or 2, got {grade!r}")

    axes: list[dict] = []
    present_weight = 0.0
    weighted_sum = 0.0
    floors_failed: list[str] = []
    measured_keys, judged_keys, missing_keys = [], [], []
    for key in AXIS_KEYS:
        entry: dict = {"axis": key, "label": AXIS_LABELS[key],
                       "weight": weights[key], "floor": floors[key]}
        if key in measured:
            score, detail = measured[key]
            entry.update({"score": round(float(score), 2), "source": "measured",
                          "detail": detail})
            measured_keys.append(key)
        elif key in judged:
            entry.update({"score": float(GRADE_SCORES[judged[key]]),
                          "source": "judged", "grade": judged[key], "detail": {}})
            judged_keys.append(key)
        else:
            entry.update({"score": None, "source": "missing", "detail": {}})
            missing_keys.append(key)
        if entry["score"] is None:
            entry["below_floor"] = False
        else:
            entry["below_floor"] = entry["score"] < floors[key]
            if entry["below_floor"]:
                floors_failed.append(key)
            present_weight += weights[key]
            weighted_sum += entry["score"] * weights[key]
        axes.append(entry)

    weighted_total = round(weighted_sum / present_weight, 2) if present_weight else 0.0
    quality_pass = weighted_total >= profile["pass_total"] and not floors_failed
    return {"profile_id": profile["id"], "format": fmt,
            "axes": axes,
            "weighted_total": weighted_total,
            "pass_total": profile["pass_total"],
            "floors_failed": floors_failed,
            "quality_pass": quality_pass,
            "measured_axes": measured_keys,
            "judged_axes": judged_keys,
            "missing_axes": missing_keys}
