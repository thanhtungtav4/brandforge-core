"""Deterministic fact checker.

Facts live ONLY in data/facts_lorebook.json. Any number/claim in a draft that
does not match that file is an issue. Claims marked [NEEDS SOURCE] are recorded
as flagged_claims and do NOT block the gate; unmarked unsupported claims do.

Coverage (P1b):
- Money in every common Vietnamese format (185.000đ, 185.000 đ, 185.000đ/gói,
  185.000 VND / vnđ, 185000đ, 185k, 185 nghìn, 185 ngàn), normalized to a VND
  integer and compared with the fact price.
- Weight and delivery ranges vs the facts file.
- Roast weekday: a "rang" sentence naming a different weekday than the fact.
- Origin pairing: Arabica↔Cầu Đất, Robusta↔Đắk Lắk; another coffee region
  presented as the brand's bean source is an unknown_origin.
- Unsupported statistic-shaped numbers (number + unit like khách hàng /
  cửa hàng / năm kinh nghiệm) that appear in no fact.
- Prohibited / evidence-requiring claim phrases and bare percentages.
"""
from __future__ import annotations

import re

from .linter import split_sentences
from .loaders import prohibited_claims

WEIGHT = re.compile(r"(\d+)\s?g\b")
DELIVERY = re.compile(r"(\d+-\d+)\s?ngày")
PERCENT = re.compile(r"\d+\s?%")
NEEDS_SOURCE = "[NEEDS SOURCE]"

# Claim patterns that require evidence (mirrors brand YAML requires_evidence)
CLAIM_PHRASES = ["tốt nhất", "ngon nhất", "rẻ nhất", "số 1", "đẳng cấp thế giới",
                 "chữa mất ngủ", "trị bệnh", "luôn thơm hơn", "luôn ngon hơn", "tốt hơn hẳn"]

# ---------------------------------------------------------------------------
# Money normalization (P1b)
# ---------------------------------------------------------------------------

def _grouped_to_int(s: str) -> int:
    return int(s.replace(".", "").replace(",", ""))


def _decimal_to_float(s: str) -> float:
    return float(s.replace(",", "."))


# Each pattern: (regex, value extractor). Applied in order; overlapping spans
# are de-duplicated so one amount is never reported twice.
_MONEY_PATTERNS: list[tuple[re.Pattern, object]] = [
    # 185.000đ / 185.000 đ / 185.000đ/gói / 185.000 VND / 185.000 vnđ / 185.000 đồng
    (re.compile(r"(\d{1,3}(?:\.\d{3})+)\s*(?:vnđ|vnd|đồng|đ)(?:\s*/\s*gói)?", re.IGNORECASE),
     lambda m: _grouped_to_int(m.group(1))),
    # 185000đ / 185000 vnđ (plain digits, no thousand separators)
    (re.compile(r"(\d{4,})\s*(?:vnđ|vnd|đồng|đ)\b", re.IGNORECASE),
     lambda m: int(m.group(1))),
    # 185k / 185.5k
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*k\b", re.IGNORECASE),
     lambda m: int(_decimal_to_float(m.group(1)) * 1000)),
    # 185 nghìn / 185 ngàn / 185,5 nghìn
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:nghìn|ngàn)\b", re.IGNORECASE),
     lambda m: int(_decimal_to_float(m.group(1)) * 1000)),
]


def find_money_amounts(text: str) -> list[tuple[int, int, int, str]]:
    """Return [(start, end, value_vnd, raw)] for every money amount in text,
    de-duplicated by span overlap (first/longest match wins)."""
    found: list[tuple[int, int, int, str]] = []
    for pattern, extract in _MONEY_PATTERNS:
        for m in pattern.finditer(text):
            found.append((m.start(), m.end(), int(extract(m)), m.group(0)))  # type: ignore[operator]
    found.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    deduped: list[tuple[int, int, int, str]] = []
    for item in found:
        if any(item[0] < kept[1] and kept[0] < item[1] for kept in deduped):
            continue
        deduped.append(item)
    return deduped


def _fact_price(facts: dict) -> int:
    f = facts["facts"]
    if f.get("price_vnd_per_250g"):
        return int(f["price_vnd_per_250g"])
    return int(re.sub(r"\D", "", str(f.get("price_display", ""))) or 0)


# ---------------------------------------------------------------------------
# Roast weekday / origin pairing / unsupported numbers (P1b)
# ---------------------------------------------------------------------------

_WEEKDAY_CANON = {"hai": "thứ hai", "2": "thứ hai", "ba": "thứ ba", "3": "thứ ba",
                  "tư": "thứ tư", "4": "thứ tư", "năm": "thứ năm", "5": "thứ năm",
                  "sáu": "thứ sáu", "6": "thứ sáu", "bảy": "thứ bảy", "7": "thứ bảy",
                  "chủ nhật": "chủ nhật"}
_WEEKDAY_RE = re.compile(r"thứ\s+(hai|ba|tư|năm|sáu|bảy|[2-7])\b|chủ\s+nhật", re.IGNORECASE)

# Fact origins: Arabica -> Cầu Đất, Robusta -> Đắk Lắk.
_ORIGIN_PAIRS = {"arabica": "cầu đất", "robusta": "đắk lắk"}

# Other coffee regions: fine to MENTION (comparisons), an issue only when a
# sentence presents one as the source of the brand's own beans.
_OTHER_REGIONS = ["bảo lộc", "di linh", "gia lai", "kon tum",
                  "sơn la", "điện biên", "buôn ma thuột"]
_SOURCING_MARKERS = ["của mình", "mộc brew", "hạt của", "nguồn hạt",
                     "trồng tại", "thu hoạch tại", "lấy từ", "nguồn gốc"]

# Statistic-shaped numbers: a bare digit-number glued to a countable unit is
# a factual claim; if no fact supports it, it needs a source.
_STAT_UNITS = ["khách hàng", "người dùng", "năm kinh nghiệm", "cửa hàng",
               "chi nhánh", "khách", "người", "năm", "tấn", "kg"]
_STAT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(" + "|".join(re.escape(u) for u in _STAT_UNITS) + r")\b",
    re.IGNORECASE)
# Numbers that ARE facts (pack weight in g / as 1kg, the pack price in k).
_FACT_NUMBER_TOKENS = {"185", "250", "1000"}


def check_section(text: str, facts: dict, brand: dict, section_id: str = "") -> tuple[list[dict], list[dict]]:
    issues: list[dict] = []
    flagged: list[dict] = []
    f = facts["facts"]
    price = _fact_price(facts)

    # --- money: every amount must equal the fact price ---
    for _start, _end, value, raw in find_money_amounts(text):
        if value != price:
            issues.append({"section_id": section_id, "type": "wrong_price",
                           "detail": f"{raw} = {value}đ != fact {f['price_display']}"})

    # --- weight / delivery ---
    for m in WEIGHT.finditer(text):
        if int(m.group(1)) not in (f["pack_weight_g"], 1000):
            issues.append({"section_id": section_id, "type": "unknown_weight", "detail": m.group(0)})
    for m in DELIVERY.finditer(text):
        if m.group(1) != f["delivery_days"].split()[0]:
            issues.append({"section_id": section_id, "type": "wrong_delivery", "detail": m.group(0)})

    fact_roast_day = str(f.get("roast_weekday", "")).lower()

    for sentence in split_sentences(text):
        low = sentence.lower()
        marked = NEEDS_SOURCE in sentence

        # --- roast weekday: only sentences about roasting ---
        if "rang" in low and fact_roast_day:
            for m in _WEEKDAY_RE.finditer(low):
                token = (m.group(1) or "chủ nhật").lower()
                if _WEEKDAY_CANON.get(token) != fact_roast_day:
                    issues.append({"section_id": section_id, "type": "wrong_roast_day",
                                   "detail": f"{m.group(0)} != fact {f['roast_weekday']}"})
                    break

        # --- origin pairing, checked per CLAUSE: one sentence may legitimately
        #     list both correct pairings ("Arabica Cầu Đất, Robusta Đắk Lắk").
        #     A clause is wrong only when it pairs a bean with the OTHER region
        #     and does not name its true home in the same clause. ---
        for clause in re.split(r"[,;]+", low):
            for bean, home in _ORIGIN_PAIRS.items():
                other = next(v for k, v in _ORIGIN_PAIRS.items() if k != bean)
                if bean in clause and other in clause and home not in clause:
                    issues.append({"section_id": section_id, "type": "wrong_origin",
                                   "detail": f"{bean} paired with {other}, fact is {bean} {home}"})

        # --- another region claimed as the brand's bean source ---
        region_hit = next((r for r in _OTHER_REGIONS if r in low), None)
        if region_hit and any(marker in low for marker in _SOURCING_MARKERS):
            issues.append({"section_id": section_id, "type": "unknown_origin",
                           "detail": f"{region_hit} presented as bean source; facts: {', '.join(f['origins'])}"})

        # --- claims: prohibited phrases block, evidence-requiring phrases and
        #     statistic-shaped numbers block unless marked [NEEDS SOURCE] ---
        claim_found = False
        for phrase in prohibited_claims(brand):
            if phrase in low:
                if marked:
                    flagged.append({"section_id": section_id, "type": "flagged_claim", "detail": phrase})
                else:
                    issues.append({"section_id": section_id, "type": "prohibited_claim", "detail": phrase})
                claim_found = True
                break
        if not claim_found:
            for phrase in CLAIM_PHRASES:
                if phrase in low:
                    if marked:
                        flagged.append({"section_id": section_id, "type": "flagged_claim", "detail": phrase})
                    else:
                        issues.append({"section_id": section_id, "type": "unsupported_claim", "detail": phrase})
                    claim_found = True
                    break
        if not claim_found and PERCENT.search(sentence) and not marked:
            issues.append({"section_id": section_id, "type": "unsupported_statistic",
                           "detail": sentence[:60]})
            claim_found = True

        # --- unsupported statistic-shaped numbers (digit + countable unit) ---
        for m in _STAT_RE.finditer(sentence):
            token = m.group(1)
            if token in _FACT_NUMBER_TOKENS:
                continue
            if m.start() > 0 and sentence[m.start() - 1] == "-":
                continue  # inside a range like 2-3 (delivery fact territory)
            if sentence[m.end():m.end() + 1] == "-":
                continue  # start of a range
            if marked:
                flagged.append({"section_id": section_id, "type": "flagged_claim",
                                "detail": m.group(0)})
            else:
                issues.append({"section_id": section_id, "type": "unsupported_number",
                               "detail": m.group(0)})
    return issues, flagged


def check_draft(sections: list[dict], facts: dict, brand: dict) -> tuple[list[dict], list[dict], list[str]]:
    issues: list[dict] = []
    flagged: list[dict] = []
    failing: list[str] = []
    for sec in sections:
        i, fl = check_section(sec.get("text", ""), facts, brand, sec.get("id", ""))
        issues.extend(i)
        flagged.extend(fl)
        if i:
            failing.append(sec.get("id", ""))
    return issues, flagged, failing


def find_refused_claims(brief_text: str, brand: dict) -> list[str]:
    """Claims appearing in the BRIEF that the core must refuse to write."""
    low = brief_text.lower()
    return [p for p in prohibited_claims(brand) if p in low]
