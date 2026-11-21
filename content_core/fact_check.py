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

Audit round 2 additions:
- Money also in comma-grouped form (185,000đ) and with the ₫ sign (U+20AB);
  delivery ranges with en/em dashes or spaces ("5–7 ngày", "5 - 7 ngày");
  weights written "gam"/"gram" ("300 gam").
- Claim phrases, roast weekdays and origin pairing are matched on the
  linter's NORMALIZED text, so diacritics-free copy ("chua mat ngu",
  "Arabica Dak Lak") cannot evade them, "số 1" no longer matches inside
  "số 12", and a NEGATED claim ("không phải là tốt nhất", "không rang vào
  thứ Tư") is not treated as an assertion.
"""
from __future__ import annotations

import re

from .linter import _phrase_regex, normalize_for_match, phrase_in, split_sentences
from .loaders import prohibited_claims

WEIGHT = re.compile(r"(\d+)\s?(?:gam|gram|g)\b")
DELIVERY = re.compile(r"(\d+)\s*[-–—]\s*(\d+)\s*ngày")
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
    # 185.000đ / 185.000 đ / 185.000đ/gói / 185.000 VND / 185.000 vnđ / 185.000 đồng / 185.000₫
    (re.compile(r"(\d{1,3}(?:\.\d{3})+)\s*(?:vnđ|vnd|đồng|đ|₫)(?:\s*/\s*gói)?", re.IGNORECASE),
     lambda m: _grouped_to_int(m.group(1))),
    # 185,000đ — comma as the thousand separator (audit round 2: this form
    # used to be completely invisible to the checker)
    (re.compile(r"(\d{1,3}(?:,\d{3})+)\s*(?:vnđ|vnd|đồng|đ|₫)(?:\s*/\s*gói)?", re.IGNORECASE),
     lambda m: _grouped_to_int(m.group(1))),
    # 185000đ / 185000 vnđ (plain digits, no thousand separators)
    (re.compile(r"(\d{4,})\s*(?:vnđ|vnd|đồng|đ|₫)(?!\w)", re.IGNORECASE),
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

# Weekdays are matched on NORMALIZED text ("thu ba"), so diacritics-free
# copy is checked too. Canon maps the matched token to the normalized
# weekday name; the fact value is normalized the same way before comparing.
_WEEKDAY_CANON_NORM = {"hai": "thu hai", "2": "thu hai", "ba": "thu ba", "3": "thu ba",
                       "tu": "thu tu", "4": "thu tu", "nam": "thu nam", "5": "thu nam",
                       "sau": "thu sau", "6": "thu sau", "bay": "thu bay", "7": "thu bay",
                       "chu nhat": "chu nhat"}
_WEEKDAY_RE_NORM = re.compile(r"thu\s+(hai|ba|tu|nam|sau|bay|[2-7])\b|chu\s+nhat")
_ROAST_NEGATED_RE = re.compile(r"khong\s+(?:phai\s+)?(?:la\s+)?rang")

# Fact origins: Arabica -> Cầu Đất, Robusta -> Đắk Lắk (normalized forms).
_ORIGIN_PAIRS_NORM = {"arabica": "cau dat", "robusta": "dak lak"}

# Other coffee regions: fine to MENTION (comparisons), an issue only when a
# sentence presents one as the source of the brand's own beans.
_OTHER_REGIONS_NORM = [normalize_for_match(r) for r in
                       ["bảo lộc", "di linh", "gia lai", "kon tum",
                        "sơn la", "điện biên", "buôn ma thuột"]]
_SOURCING_MARKERS_NORM = [normalize_for_match(m) for m in
                          ["của mình", "mộc brew", "hạt của", "nguồn hạt",
                           "trồng tại", "thu hoạch tại", "lấy từ", "nguồn gốc"]]

# A claim immediately preceded by one of these negations (optionally
# followed by "la") is a DENIAL, not an assertion: "không phải là tốt
# nhất" must not be flagged as claiming "tốt nhất". Narrow on purpose —
# "không chỉ tốt nhất" (which affirms) is not in the list and still flags.
_NEGATED_PREFIX_RE = re.compile(
    r"(khong phai|chang phai|dau phai|khong nhan|khong dam nhan)( la)?$")


def find_claim(sentence: str, phrases: list[str]) -> str | None:
    """First phrase (list order) with a NON-NEGATED normalized match in the
    sentence, or None. Matching is diacritics/punctuation-insensitive with
    digit-boundary guards (see linter.normalize_for_match)."""
    norm = normalize_for_match(sentence)
    for phrase in phrases:
        rx = _phrase_regex(phrase)
        if rx is None:
            continue
        for m in rx.finditer(norm):
            prefix = norm[:m.start()].rstrip()
            if _NEGATED_PREFIX_RE.search(prefix):
                continue  # denied, not asserted — try the next occurrence
            return phrase
    return None

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
    fact_range = f["delivery_days"].split()[0]  # e.g. "2-3"
    for m in DELIVERY.finditer(text):
        if f"{m.group(1)}-{m.group(2)}" != fact_range:
            issues.append({"section_id": section_id, "type": "wrong_delivery", "detail": m.group(0)})

    fact_roast_day_norm = normalize_for_match(str(f.get("roast_weekday", "")))

    for sentence in split_sentences(text):
        marked = NEEDS_SOURCE in sentence
        norm = normalize_for_match(sentence)

        # --- roast weekday: on the normalized sentence, only when it is
        #     about roasting, and a weekday whose mention is covered by a
        #     roasting negation ("không rang vào thứ Tư") is not a claim. ---
        if fact_roast_day_norm and "rang" in norm:
            for m in _WEEKDAY_RE_NORM.finditer(norm):
                if _ROAST_NEGATED_RE.search(norm[:m.start()]):
                    continue
                token = (m.group(1) or "chu nhat")
                if _WEEKDAY_CANON_NORM.get(token) != fact_roast_day_norm:
                    issues.append({"section_id": section_id, "type": "wrong_roast_day",
                                   "detail": f"{m.group(0)} != fact {f['roast_weekday']}"})
                    break

        # --- origin pairing on the normalized sentence: a pairing is wrong
        #     only when a bean is named with the OTHER region and its true
        #     home is not named in the same sentence — so a sentence listing
        #     both correct pairings ("Arabica Cầu Đất, Robusta Đắk Lắk")
        #     stays clean. (Normalization has already collapsed the commas
        #     the old clause splitter used; the home-present guard carries
        #     the same semantics.) ---
        for bean, home in _ORIGIN_PAIRS_NORM.items():
            other = next(v for k, v in _ORIGIN_PAIRS_NORM.items() if k != bean)
            if bean in norm and other in norm and home not in norm:
                issues.append({"section_id": section_id, "type": "wrong_origin",
                               "detail": f"{bean} paired with {other}, fact is {bean} {home}"})

        # --- another region claimed as the brand's bean source ---
        region_hit = next((r for r in _OTHER_REGIONS_NORM if r in norm), None)
        if region_hit and any(marker in norm for marker in _SOURCING_MARKERS_NORM):
            issues.append({"section_id": section_id, "type": "unknown_origin",
                           "detail": f"{region_hit} presented as bean source; facts: {', '.join(f['origins'])}"})

        # --- claims: prohibited phrases block, evidence-requiring phrases and
        #     statistic-shaped numbers block unless marked [NEEDS SOURCE].
        #     Normalized, negation-aware matching (find_claim): a denied
        #     claim is not an assertion. ---
        claim_found = False
        hit = find_claim(sentence, prohibited_claims(brand))
        if hit is not None:
            if marked:
                flagged.append({"section_id": section_id, "type": "flagged_claim", "detail": hit})
            else:
                issues.append({"section_id": section_id, "type": "prohibited_claim", "detail": hit})
            claim_found = True
        if not claim_found:
            hit = find_claim(sentence, CLAIM_PHRASES)
            if hit is not None:
                if marked:
                    flagged.append({"section_id": section_id, "type": "flagged_claim", "detail": hit})
                else:
                    issues.append({"section_id": section_id, "type": "unsupported_claim", "detail": hit})
                claim_found = True
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
    """Claims appearing in the BRIEF that the core must refuse to write.
    Normalized matching (diacritics/punctuation-insensitive), so a brief
    written without diacritics ("100% chua mat ngu") is still refused.
    Negation is NOT honoured here on purpose: a brief that mentions a
    prohibited claim at all is flagged for the writer to keep out."""
    return [p for p in prohibited_claims(brand) if phrase_in(brief_text, p)]
