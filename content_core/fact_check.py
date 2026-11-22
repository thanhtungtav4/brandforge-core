"""Deterministic fact checker.

Facts live in the ACTIVE brand's fact base (per-brand profiles in
data/facts_profiles/, with data/facts_lorebook.json as the shipped
moc-brew base — see loaders.load_facts). Any number/claim in a draft that
does not match that fact base is an issue. Claims marked [NEEDS SOURCE] are recorded
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

Core-completion additions:
- Origin pairing is negation-aware too: "Robusta không lấy từ Cầu Đất"
  and "không phải Robusta Cầu Đất" are denials, not assertions (while
  "không chỉ lấy từ Cầu Đất" still asserts and still flags).
- Single-value delivery ("giao trong 3 ngày") is checked against the
  fact range — inside passes, outside flags — and same-day claims
  ("giao ngay trong ngày") contradict a multi-day range.
- Statistic-shaped claims with the number in Vietnamese WORDS are
  detected for scale shapes ("năm trăm khách hàng", "hàng nghìn người
  dùng"). Money fully spelled out in words is NOT parsed (documented
  limitation — digit formats only).

Evaluation-fix additions (R1):
- Money ROLES: an amount framed as a shipping fee, an order total, a
  discount, an old (struck-through / "giá cũ") price, or a
  unit-consistent bulk amount ("740.000đ cho 1kg") is not a price claim
  and does not raise wrong_price; a bare mismatching price still does.
  ₫-prefix amounts ("₫200.000") are parsed like suffix ones.
- Delivery: ranges are context-gated per sentence (a use-by window is
  not a delivery promise), negated delivery verbs are denials, and the
  natural single-value phrasings are covered ("trong vòng N ngày",
  "sau N ngày làm việc", "N đến M ngày", "chậm nhất N ngày",
  "trong N giờ", "ship trong ngày", "hỏa tốc").
- Statistics: the fact-number exemption {185, 250, 1000} and the
  hyphen-range guard are gone — "250 khách hàng" and "5-7 khách hàng
  mới" are checked; weights (kg/tấn) left the unit list; bare
  người/khách needs a claim-ish context; composition percents
  ("100% hạt Arabica") are not statistics, and a percent claim marked
  [NEEDS SOURCE] is reported in flagged_claims instead of vanishing.
- Origin: per-clause nearest-region pairing catches full swaps
  ("Arabica Đắk Lắk, Robusta Cầu Đất") and region-before-bean phrasing.
- Weights: decimal and "gr" forms parse ("250,5g", "300gr", "0,3kg").

Core-completion additions (per-brand fact bases + R1 leftovers):
- The fact base is PER-BRAND: origin pairs derive from the active
  facts doc's `origins`; a facts doc may declare `known_prices_vnd`
  (verified prices beyond the primary pack) and `pack_weights_g`
  (its real pack sizes); a facts doc with no delivery fact skips the
  delivery checks instead of judging against an empty range.
- Roast day is clause-scoped with LOCAL negation (D5): a weekday in a
  clause without "rang" is another activity's day, one "không rang"
  no longer suppresses a later affirmed weekday, and post-posed
  denials ("thứ Tư không phải ngày rang") are honoured.
- Claim denial grammar widened (D6: bare "không", "không khẳng
  định", "chưa từng nhận", ...); competitor-attributed and
  reported-question claims are FLAGGED, not blocking.
- Claim paraphrases (D11): one inserted word ("chữa được mất ngủ"),
  word numerals ("số một"), and the synonyms "đứng đầu việt nam",
  "thần dược", "hết mất ngủ" are caught. Full semantic paraphrase
  and claims split across a sentence boundary remain out of reach
  for a phrase checker — documented non-goals.
- Word-number statistics (D10): chained scales ("hàng trăm nghìn"),
  "hàng chục nghìn", quantifier-led scales ("cả nghìn") and bare
  scale words ("Nghìn khách hàng") are caught; sub-scale numbers
  ("sáu mươi khách hàng") remain a documented bound.
- Money (D8): bare grouped amounts in an explicit price context
  ("giá 200.000") and amounts split by a line break ("200.\n000đ")
  are parsed. Spelled-out money and foreign currency remain
  documented non-goals.
- Origin: pronoun-linked cross-sentence pairings are checked, the
  other-region list covers international coffee origins with
  "nhập từ" as a sourcing marker, and unknown_origin is clause-scoped
  with a competitor-clause exclusion.
"""
from __future__ import annotations

import re

from .linter import (NEGATED_PREFIX_RE, _phrase_regex, collapse_letter_spacing,
                     fuzzy_phrase_spans, normalize_for_match, split_sentences,
                     strip_markup)
from .loaders import format_vnd, prohibited_claims

# Weight mentions in every common form: "250g", "300 gam", "300gr",
# "0,3kg", "250,5g" (evaluation R1: the old integer-only, g-only pattern
# missed 'gr' entirely and misread "250,5g" as "5g").
WEIGHT = re.compile(r"(\d+(?:[.,]\d+)?)\s?(kg|ký|gam|gram|gr|g)\b", re.IGNORECASE)


def _weight_grams(m: re.Match) -> float:
    value = float(m.group(1).replace(",", "."))
    return value * 1000 if m.group(2).lower() in ("kg", "ký") else value


# A whole "cân"/"ký" written with a word number ("một cân" = 1kg) — only
# used for the money bulk-consistency role below, never as a weight claim.
_WORD_KILO_RE = re.compile(r"\b(?:một|1)\s*(?:cân|ký)\b", re.IGNORECASE)


def _weights_in_grams(text: str) -> list[float]:
    grams = [_weight_grams(m) for m in WEIGHT.finditer(text)]
    if _WORD_KILO_RE.search(text):
        grams.append(1000.0)
    return grams


DELIVERY = re.compile(r"(\d+)\s*[-–—]\s*(\d+)\s*ngày")
# Range with a word separator ("5 đến 7 ngày") — checked per sentence and
# only with delivery context (see _has_delivery_context): the bare
# hyphen range above fires on ANY "N-M ngày", which false-flagged use-by
# windows ("dùng trong 5-7 ngày") — so the hyphen form is context-gated
# per sentence now too (evaluation R1).
DELIVERY_RANGE_WORDS = re.compile(r"(\d+)\s*(?:đến|tới)\s*(\d+)\s*ngày", re.IGNORECASE)
# Single-value phrasings (evaluation R1): "trong vòng N ngày",
# "sau N ngày (làm việc)", "chậm nhất N ngày", "trong N giờ" — each is a
# promise of delivery by day N (hours converted at 24h/day) and is
# checked against the fact range like SINGLE_DELIVERY, but ONLY in a
# sentence carrying delivery context, so shelf-life/duration prose
# ("dùng sau 5 ngày") is not mistaken for a delivery promise.
DELIVERY_WITHIN = re.compile(r"trong\s+vòng\s+(\d+)\s*ngày", re.IGNORECASE)
DELIVERY_AFTER = re.compile(r"\bsau\s+(\d+)\s*ngày", re.IGNORECASE)
DELIVERY_LATEST = re.compile(r"(?:chậm nhất|trễ nhất|muộn nhất)\s+(\d+)\s*ngày",
                             re.IGNORECASE)
DELIVERY_HOURS = re.compile(r"trong\s+(\d+)\s*(?:giờ|tiếng)", re.IGNORECASE)

_DELIVERY_CONTEXT_MARKERS = ("giao", "ship", "van chuyen", "nhan hang",
                             "nhan duoc hang", "thoi gian giao", "don hang")


def _has_delivery_context(sentence: str) -> bool:
    norm = normalize_for_match(sentence)
    return any(marker in norm for marker in _DELIVERY_CONTEXT_MARKERS)
# Single-value delivery ("giao trong 3 ngày"): a delivery verb must appear
# shortly before "trong N ngày", so unrelated durations ("dùng trong 5
# ngày") are not delivery claims. The value is checked against the fact
# RANGE: inside passes, outside is a wrong_delivery.
SINGLE_DELIVERY = re.compile(
    r"(?:giao|nhận hàng|ship|vận chuyển)[^.!?…\n]{0,40}?\btrong\s+(\d+)\s*ngày",
    re.IGNORECASE)
PERCENT = re.compile(r"\d+\s?%")
NEEDS_SOURCE = "[NEEDS SOURCE]"

# Claim patterns that require evidence (mirrors brand YAML requires_evidence).
# Core-completion additions (evaluation R1-D11): the superlative synonym
# "đứng đầu việt nam", the miracle-cure word "thần dược", and the cure
# paraphrase "hết mất ngủ" ("uống vào là hết mất ngủ") — all claim shapes
# the phrase list previously could not see at all.
CLAIM_PHRASES = ["tốt nhất", "ngon nhất", "rẻ nhất", "số 1", "đẳng cấp thế giới",
                 "chữa mất ngủ", "trị bệnh", "luôn thơm hơn", "luôn ngon hơn", "tốt hơn hẳn",
                 "đứng đầu việt nam", "thần dược", "hết mất ngủ"]

# ---------------------------------------------------------------------------
# Money normalization (P1b)
# ---------------------------------------------------------------------------

def _grouped_to_int(s: str) -> int:
    return int(re.sub(r"[\s.,]", "", s))


def _decimal_to_float(s: str) -> float:
    return float(s.replace(",", "."))


# Each pattern: (regex, value extractor). Applied in order; overlapping spans
# are de-duplicated so one amount is never reported twice. Grouped patterns
# tolerate whitespace (incl. a line break) after the thousand separator —
# "200.\n000đ" used to defeat every pattern (evaluation R1 §9).
_MONEY_PATTERNS: list[tuple[re.Pattern, object]] = [
    # 185.000đ / 185.000 đ / 185.000đ/gói / 185.000 VND / 185.000 vnđ / 185.000 đồng / 185.000₫
    (re.compile(r"(\d{1,3}(?:\.\s*\d{3})+)\s*(?:vnđ|vnd|đồng|đ|₫)(?:\s*/\s*gói)?", re.IGNORECASE),
     lambda m: _grouped_to_int(m.group(1))),
    # 185,000đ — comma as the thousand separator (audit round 2: this form
    # used to be completely invisible to the checker)
    (re.compile(r"(\d{1,3}(?:,\s*\d{3})+)\s*(?:vnđ|vnd|đồng|đ|₫)(?:\s*/\s*gói)?", re.IGNORECASE),
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
    # ₫200.000 / ₫185.000 — the ₫ sign BEFORE the amount (evaluation R1:
    # prefix forms were invisible; suffix ₫ is covered above)
    (re.compile(r"₫\s*(\d{1,3}(?:\.\s*\d{3})+)"),
     lambda m: _grouped_to_int(m.group(1))),
    (re.compile(r"₫\s*(\d{4,})"),
     lambda m: int(m.group(1))),
    # Bare amount in an EXPLICIT price context (evaluation R1-D8): "một
    # gói giá 200.000." names a price with no currency marker at all.
    # Bounded on purpose: the number must be thousand-grouped (or 4+
    # digits) and immediately follow "giá" (optional ":" / "là"); the
    # lookbehind keeps "đánh giá"/"định giá" (to evaluate / to appraise)
    # from counting as price contexts, and the lookahead keeps
    # statistic units and weights from being read as money.
    (re.compile(r"(?<!đánh )(?<!định )(?<!danh )(?:giá|gia)\s*(?::|là)?\s*"
                r"(\d{1,3}(?:\.\s*\d{3})+|\d{4,})"
                r"(?!\s*(?:khách|người|cửa hàng|chi nhánh|năm)\b|g\b)",
                re.IGNORECASE),
     lambda m: int(re.sub(r"\D", "", m.group(1)))),
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


def _price_display(facts: dict) -> str:
    """The fact price's display form for issue details. NEVER indexes
    the field directly (review M1): validate_facts accepts a doc with
    only `price_vnd_per_250g`, and while load_facts derives the
    display at load time, a facts dict built in-process (a test, a
    caller with its own doc) must not KeyError the checker — derive
    here too."""
    f = facts.get("facts") or {}
    if f.get("price_display"):
        return str(f["price_display"])
    price = _fact_price(facts)
    return format_vnd(price) if price else "the fact price"


# ---------------------------------------------------------------------------
# Money ROLES (evaluation R1): not every amount in a draft is the product
# price. An amount explicitly framed as one of the roles below is NOT a
# wrong_price when it differs from the fact price — it was never claiming
# to BE the price:
#   shipping_fee — preceded (same sentence window) by phí ship / phí vận
#                  chuyển / phí giao hàng / tiền ship;
#   total        — preceded by "tổng" (tổng cộng, hai gói có tổng…);
#   discount     — preceded by giảm / tiết kiệm;
#   old_price    — preceded by giá cũ / giá gốc, or struck through (~~…~~);
#   bulk         — the same sentence names a weight W and the amount is
#                  exactly the unit price scaled to W (amount × pack_weight
#                  == price × W): "740.000đ cho 1kg" at 185.000đ/250g is
#                  consistent, not wrong.
# Anything else that differs from the fact price is still wrong_price —
# a bare "giá 200.000đ" for the pack keeps flagging. Role markers are
# matched on the NORMALIZED text before the amount, within ~90 chars
# AND within the amount's own sentence (review H1: the sentence bound
# is what the "same sentence window" wording always promised — a
# marker in a PREVIOUS sentence does not reframe this amount); a
# marker AFTER the amount does not reframe it either.
# ---------------------------------------------------------------------------

_MONEY_ROLE_RES = [
    ("shipping_fee", re.compile(r"\b(phi ship|phi van chuyen|phi giao hang|tien ship)\b")),
    ("total", re.compile(r"\btong\b")),
    ("discount", re.compile(r"\b(giam|tiet kiem)\b")),
    ("old_price", re.compile(r"\b(gia cu|gia goc)\b")),
]


def _is_sentence_boundary(text: str, i: int) -> bool:
    """True when text[i] ends a sentence for money-role scoping
    (review H1). A boundary is a newline, or sentence punctuation
    (. ! ? … ;) followed by whitespace and an uppercase letter (or
    the end of the text). The '.' inside '185.000đ' is followed by a
    digit — never a boundary; the '.' in 'theo đơn. Gói…' is followed
    by a capital — always one. Edited Vietnamese copy capitalises
    sentence starts, so the capital test is the reliable signal."""
    ch = text[i]
    if ch == "\n":
        return True
    if ch not in ".!?…;":
        return False
    j = i + 1
    while j < len(text) and text[j] in " \t\r":
        j += 1
    if j >= len(text):
        return True
    if j == i + 1:
        return False  # punctuation glued to the next char: not a break
    return text[j].isupper() or text[j] in "«\"“"


def _sentence_span(text: str, start: int, end: int) -> tuple[int, int]:
    """(lo, hi) character span of the sentence containing
    text[start:end], per _is_sentence_boundary."""
    lo = 0
    for i in range(start - 1, -1, -1):
        if _is_sentence_boundary(text, i):
            lo = i + 1
            break
    hi = len(text)
    for i in range(end, len(text)):
        if _is_sentence_boundary(text, i):
            hi = i + 1
            break
    return lo, hi


def _money_role(text: str, start: int, end: int, value: int,
                price: int, pack_weight_g: float) -> str | None:
    """The role framing the amount at text[start:end], or None when the
    amount is presented as the product price itself.

    Role markers are searched ONLY inside the amount's own sentence
    (review H1): the search used to run over the previous ~90 raw
    characters with no boundary check, so 'Phí ship được tính riêng
    theo đơn. Gói 250g có giá 200.000đ' excused the wrong price — the
    shipping sentence 'framed' an amount in the NEXT sentence. The
    ~90-char distance bound still applies within the sentence."""
    if text[:start].count("~~") % 2 == 1:
        return "old_price"  # inside a ~~struck-through~~ span
    sent_lo, sent_hi = _sentence_span(text, start, end)
    before = normalize_for_match(text[max(sent_lo, start - 90):start])
    for role, rx in _MONEY_ROLE_RES:
        if rx.search(before):
            return role
    window = text[max(sent_lo, start - 90):min(sent_hi, end + 40)]
    for grams in _weights_in_grams(window):
        if grams and value * pack_weight_g == price * grams:
            return "bulk"
    return None


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
# These are the DEFAULT pairs — the ones the shipped Mộc Brew facts
# derive. The pairs a check actually uses come from the ACTIVE facts
# doc (see _origin_pairs): per-brand fact bases (core-completion round)
# mean a second brand's beans pair with ITS regions, not Mộc Brew's.
_DEFAULT_ORIGIN_PAIRS_NORM = {"arabica": "cau dat", "robusta": "dak lak"}



def _origin_pairs(facts: dict) -> dict[str, str]:
    """Bean -> home region (normalized), derived from the facts doc's
    `origins` list ("Arabica Cầu Đất" -> {"arabica": "cau dat"}). The
    shipped Mộc Brew facts derive exactly the default pairs, so their
    behaviour is unchanged; a brand whose facts say "Robusta Đức Cơ"
    is judged against Đức Cơ. Falls back to the default pairs when the
    origins field is missing or names no recognisable bean."""
    pairs: dict[str, str] = {}
    for origin in (facts.get("facts") or {}).get("origins") or []:
        tokens = normalize_for_match(str(origin)).split()
        if len(tokens) >= 2 and tokens[0] in ("arabica", "robusta"):
            pairs[tokens[0]] = " ".join(tokens[1:])
    return pairs or dict(_DEFAULT_ORIGIN_PAIRS_NORM)


def _wrong_regions(pairs: dict[str, str], home: str) -> list[str]:
    """Regions that are WRONG for a bean whose home is `home`: every
    other paired region, plus the default pair regions (a Robusta Đức
    Cơ brand pairing its beans with Đắk Lắk is still a wrong pairing,
    even though its own facts name only one region)."""
    regions = set(pairs.values()) | set(_DEFAULT_ORIGIN_PAIRS_NORM.values())
    regions.discard(home)
    return sorted(regions)


# Other coffee regions: fine to MENTION (comparisons), an issue only when a
# clause presents one as the source of the brand's own beans. The
# international origins were added in the core-completion round
# (evaluation R1 §5: "nhập từ Ethiopia" was invisible because the list
# was domestic-only).
_OTHER_REGIONS_NORM = [normalize_for_match(r) for r in
                       ["bảo lộc", "di linh", "gia lai", "kon tum",
                        "sơn la", "điện biên", "buôn ma thuột",
                        "ethiopia", "colombia", "brazil", "kenya",
                        "sumatra", "costa rica", "guatemala", "peru",
                        "yemen", "java"]]
_SOURCING_MARKERS_NORM = [normalize_for_match(m) for m in
                          ["của mình", "mộc brew", "hạt của", "nguồn hạt",
                           "trồng tại", "thu hoạch tại", "lấy từ", "nguồn gốc",
                           "nhập từ", "nguồn gốc từ"]]

# A claim immediately preceded by one of these negations (optionally
# followed by "la") is a DENIAL, not an assertion: "không phải là tốt
# nhất" must not be flagged as claiming "tốt nhất". Narrow on purpose —
# "không chỉ tốt nhất" (which affirms) is not in the list and still flags.
# The regex itself lives in linter.py (NEGATED_PREFIX_RE) and is shared
# with the linter's banned-term context rule — one definition, two users.
_NEGATED_PREFIX_RE = NEGATED_PREFIX_RE

# Origin pairing, negation-aware (core-completion round): the same
# denial-vs-assertion distinction as claims. A bean paired with the WRONG
# region is an assertion only when the pairing is not denied:
#   "Không phải Robusta Cầu Đất"      — negation right before the bean;
#   "Robusta không lấy từ Cầu Đất"    — negated sourcing verb between
#                                       bean and region.
# "không chỉ" is an AFFIRMATION ("không chỉ lấy từ Cầu Đất mà còn...")
# and never negates, mirroring the claim rule above.
_ORIGIN_NEGATED_SOURCING = (" khong lay tu ", " khong den tu ",
                            " khong phai ", " khong thuoc ",
                            " khong co nguon goc ", " khong lay ")


def _asserted_wrong_origin(norm: str, bean: str, home: str,
                           wrongs: list[str]) -> str | None:
    """The wrong region the normalized sentence ASSERTS for `bean`
    (nearest-region pairing), or None. Honours the negations documented
    above.

    NEAREST-REGION pairing (evaluation R1): each bean occurrence is
    paired with the CLOSEST region mention in the sentence (either
    direction). The old rule only looked at regions AFTER the bean and
    skipped the bean entirely when its home appeared anywhere in the
    sentence — so a full swap ("Arabica Đắk Lắk, Robusta Cầu Đất")
    flagged NEITHER bean (each home was present, just attached to the
    other bean), and region-before-bean phrasing ("Đắk Lắk là quê
    hương của hạt Arabica") was invisible. Nearest-region pairing
    catches both while keeping correct listings clean: in "Arabica
    Cầu Đất, Robusta Đắk Lắk" each bean's nearest region is its home.
    `wrongs` is the list of regions that are wrong FOR THIS BEAN (see
    _wrong_regions) — more than one since per-brand fact bases."""
    bean_at = [m.start() for m in re.finditer(rf"\b{re.escape(bean)}\b", norm)]
    home_at = [m.start() for m in re.finditer(rf"\b{re.escape(home)}\b", norm)]
    wrong_at = [(m.start(), region) for region in wrongs
                for m in re.finditer(rf"\b{re.escape(region)}\b", norm)]
    for b in bean_at:
        candidates = [(abs(o - b), o, region) for o, region in wrong_at]
        candidates += [(abs(o - b), o, None) for o in home_at]
        if not candidates:
            continue
        _dist, o, wrong = min(candidates)
        if wrong is None:
            continue  # nearest region is the bean's true home — correct
        between = norm[min(b, o):max(b, o)]
        if "khong chi" in between or "khong chi" in norm[:b].rstrip()[-24:]:
            return wrong  # "không chỉ" affirms — an assertion, not a denial
        if _NEGATED_PREFIX_RE.search(norm[:b].rstrip()):
            continue  # denied before the bean: "không phải Robusta ..."
        if any(marker in between for marker in _ORIGIN_NEGATED_SOURCING):
            continue  # denied sourcing: "Robusta không lấy từ ..."
        return wrong
    return None


_WORD_NUMERALS = {"0": "khong", "1": "mot", "2": "hai", "3": "ba",
                  "4": "bon", "5": "nam", "6": "sau", "7": "bay",
                  "8": "tam", "9": "chin"}


def _word_numeral_variant(phrase: str) -> str | None:
    """The phrase with single-digit tokens spelled out as Vietnamese
    word numerals ("số 1" -> "so mot"), or None when the phrase has no
    single-digit token. Copy writes "số một" at least as often as
    "số 1"; the digit form alone was a paraphrase hole (R1-D11)."""
    tokens = normalize_for_match(phrase).split()
    if not any(t in _WORD_NUMERALS for t in tokens):
        return None
    return " ".join(_WORD_NUMERALS.get(t, t) for t in tokens)


# Speech-act denials govern a claim that follows within a few words,
# not only one glued to them: "không khẳng định hạt ngon nhất" puts the
# object noun "hạt" between the denial and the claim. Bounded: the
# denial verb must appear within 40 chars before the claim with NO
# clause boundary (comma/semicolon/sentence punctuation) in between —
# "không khẳng định gì, hạt này tốt nhất" crosses a comma and still
# counts as an assertion. (Review M15: the window was 24 chars and
# the verb list missed "không bao giờ (nhận)", "không hề", "không
# dám" — distant denials still flagged as assertions. "không phải"
# belongs here too: "không phải cà phê tốt nhất Việt Nam đâu" puts
# the object noun between the denial and the claim exactly the same
# way, and the anchored prefix regex cannot see it.)
_DISTANT_DENIAL_RE = re.compile(
    r"(khong khang dinh|khong cong nhan|khong thua nhan"
    r"|chua tung nhan|khong nhan|khong dam nhan"
    r"|khong bao gio|khong he|khong dam"
    r"|khong phai|chang phai)[^.!?,;]{0,40}$")

# SUFFIX denial (review M15): Vietnamese also denies AFTER the claim —
# "ngon nhất đâu" / "tốt nhất đâu phải" puts the negation particle
# "đâu" (or "không phải") right behind the superlative. Bounded: the
# particle must appear within the first 3 tokens after the claim span
# with no clause boundary in between; "tốt nhất Việt Nam" (scope, not
# denial) has no such particle and still flags.
_SUFFIX_DENIAL_HEAD_RE = re.compile(r"^(khong phai|khong dung)\b")

# Time units for the freshness-window rule below (normalized forms).
_TIME_UNIT_TOKENS = {"ngay", "tuan", "thang", "nam", "gio", "phut", "dem"}


def _suffix_denied(norm: str, end: int) -> bool:
    """True when the claim ending at `end` in normalized text is
    denied by what follows it (see _SUFFIX_DENIAL_HEAD_RE note).
    The particle test is deliberately narrow: "đâu" (denial) and
    "đậu" (beans — a noun this domain uses constantly) both
    normalize to "dau", so a bare "dau" token is not enough — it
    must close the clause or pair with "có"/"phải"/"mà", the shapes
    the denial particle actually takes ("…đâu", "…đâu có rẻ",
    "…đâu phải")."""
    tail = norm[end:end + 40].strip()
    if _SUFFIX_DENIAL_HEAD_RE.match(tail):
        return True
    tokens = tail.split()
    for i, tok in enumerate(tokens[:3]):
        if tok != "dau":
            continue
        if i == len(tokens) - 1 or tokens[i + 1] in ("co", "phai", "ma"):
            return True
        # "ngon nhất đâu, đừng tin…" — clause-initial "đâu" followed
        # by "đừng" (normalized "dung") is the denial closing its
        # clause before the comma (invisible after normalization).
        # Position-0 only: mid-tail "dau dung" is "đậu dùng…" (beans
        # being used), ordinary prose.
        if i == 0 and tokens[i + 1] == "dung":
            return True
    return False


def _is_freshness_window(norm: str, end: int) -> bool:
    """True when a bare superlative ("ngon nhất") is followed by
    "trong <time>" — a FRESHNESS window, not a superlative claim:
    "ngon nhất trong vài tuần đầu" says when the coffee tastes best,
    not that it outranks everything (review M15: 3 of the 5 shipped
    CNM review claims were this shape). Bounded: "trong" must be the
    very next token and a time marker (khoảng/vài/a digit/a time
    unit) must follow within two tokens — "tốt nhất trong khu vực"
    (best IN THE REGION) is a scope claim and still flags."""
    tokens = norm[end:end + 40].split()
    if len(tokens) < 2 or tokens[0] != "trong":
        return False
    following = tokens[1:3]
    return any(t in _TIME_UNIT_TOKENS or t in ("khoang", "vai")
               or t.isdigit() for t in following)


def find_claim_detail(sentence: str, phrases: list[str]) -> tuple[str, int, str] | None:
    """(phrase, span_start, norm) of the first NON-NEGATED occurrence of
    any phrase (list order), or None. `norm` is the sentence's
    normalized form the span indexes into (returned so callers can run
    context checks — attribution — on the same coordinates).

    Matching is diacritics/punctuation-insensitive with digit-boundary
    guards (see linter.normalize_for_match), markup-stripped and
    letter-spacing-collapsed, and includes the bounded fuzzy variants
    (one inserted token: "chữa được mất ngủ" matches "chữa mất ngủ" —
    R1-D11) and the word-numeral variant of digit phrases ("số một")."""
    norm = collapse_letter_spacing(normalize_for_match(strip_markup(sentence)))
    for phrase in phrases:
        spans: list[tuple[int, int]] = []
        for variant in [phrase, _word_numeral_variant(phrase)]:
            if variant:
                spans += fuzzy_phrase_spans(norm, variant)
        phrase_norm = normalize_for_match(phrase)
        for start, end in sorted(set(spans)):
            prefix = norm[:start].rstrip()
            if _NEGATED_PREFIX_RE.search(prefix) \
                    or _DISTANT_DENIAL_RE.search(prefix):
                continue  # denied, not asserted — try the next occurrence
            if "khong chi" not in prefix and _suffix_denied(norm, end):
                continue  # denied AFTER the claim ("ngon nhất đâu") —
                # but "không chỉ … đâu" AFFIRMS ("Không chỉ tốt nhất
                # Việt Nam đâu" = "not ONLY the best in Vietnam"):
                # the affirmation wins, the trailing "đâu" is mere
                # emphasis there (pinned by test_core_closing).
            if phrase_norm == "tot nhat" and start == 0 \
                    and norm[end:end + 3] == " la":
                continue  # sentence-initial "Tốt nhất là…" = "had
                # better…" — advice idiom, not a superlative claim
            if phrase_norm.split()[-1:] == ["nhat"] \
                    and _is_freshness_window(norm, end):
                continue  # "ngon nhất trong vài tuần" = freshness
                # window, not a ranking claim
            return phrase, start, norm
    return None


def find_claim(sentence: str, phrases: list[str]) -> str | None:
    """First phrase (list order) with a NON-NEGATED normalized match in the
    sentence, or None. See find_claim_detail for the matching rules."""
    detail = find_claim_detail(sentence, phrases)
    return detail[0] if detail else None


# Attribution (evaluation R1-D6): a claim the sentence attributes to a
# COMPETITOR ("đối thủ tự nhận là tốt nhất...") or reports as a QUESTION
# being asked ("khách hỏi có phải ngon nhất không") is not the brand
# asserting it — it is recorded as FLAGGED, not blocking. Bounded on
# purpose: customer praise quoted approvingly ("khách khen đây là hạt
# ngon nhất") still blocks, exactly as R1 labelled it — the claim
# appears in the copy as an endorsement.
# Interrogative frames (review M15): a claim inside a QUESTION the
# copy is asking ("Cà phê rang xong bao lâu thì uống ngon nhất",
# "vì sao hạt này ngon nhất?") is reported speech, not an assertion
# by the brand — the same reasoning as the "có phải" case, widened
# to the question words Vietnamese headings actually use. Bounded
# to the 48 chars before the claim, like "co phai" always was.
_QUESTION_MARKERS_NORM = ("co phai", "bao lau", "phai khong",
                          "hay khong", "vi sao", "tai sao", "lam sao",
                          "the nao", "khi nao")


def _claim_attributed(norm: str, start: int) -> bool:
    prefix = norm[:start]
    if "doi thu" in prefix:
        return True
    if any(marker in prefix[-48:] for marker in _QUESTION_MARKERS_NORM):
        return True
    return False

# Statistic-shaped numbers: a bare digit-number glued to a countable unit is
# a factual claim; if no fact supports it, it needs a source.
# Evaluation R1 rework: "kg"/"tấn" are WEIGHTS, not statistics — they are
# the weight checker's job ("một gói 1kg" is ordinary prose, and flagging
# it here double-reported it). The bare units "người"/"khách" additionally
# require a claim-ish context (see _stat_context_ok): "pha cho 2 người"
# is a serving instruction, not a statistic.
_STAT_UNITS = ["khách hàng", "người dùng", "năm kinh nghiệm", "cửa hàng",
               "chi nhánh", "khách", "người", "năm"]
_STAT_BARE_UNITS = {"khách", "người"}
_STAT_QUANTIFIERS = ("hơn", "khoảng", "gần", "trên", "đạt", "thu hút",
                     "phục vụ", "đã có", "tới")
_STAT_TRUST_VERBS = ("tin dùng", "tin tưởng", "quay lại", "sử dụng",
                     "đã mua", "yêu thích", "ủng hộ")


def _stat_context_ok(sentence: str, m: re.Match) -> bool:
    """Claim-ish context for a bare người/khách statistic: a quantifier
    shortly before ("hơn 500 khách", "đã có 250 khách hàng" — the latter
    is a full unit anyway) or a verb of use/trust shortly after
    ("500 khách tin dùng"). Serving/group prose ("pha cho 2 người
    uống", "nhóm 5 người chia nhau") has neither and stays clean."""
    before = sentence[max(0, m.start() - 40):m.start()].lower()
    after = sentence[m.end():m.end() + 30].lower()
    return (any(q in before for q in _STAT_QUANTIFIERS)
            or any(v in after for v in _STAT_TRUST_VERBS))
_STAT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(" + "|".join(re.escape(u) for u in _STAT_UNITS) + r")\b",
    re.IGNORECASE)

# Statistic-shaped claims with the number written as Vietnamese WORDS
# (core-completion round). Bounded on purpose: only SCALE shapes are
# recognised — a number phrase built on trăm/nghìn/triệu (the shapes
# marketing copy actually inflates with), plus the vague quantifier
# "hàng <scale>" — glued to an audience noun, matched on the NORMALIZED
# sentence: "năm trăm khách hàng" (500 customers), "hàng nghìn người
# dùng". A lone word-number ("một khách hàng hỏi...") is ordinary prose,
# not a statistic, and is NOT matched. Money spelled out in words
# ("một trăm ... nghìn đồng") is likewise not parsed — see README.
_WORD_DIGIT = r"(?:mot|hai|ba|bon|tu|nam|lam|sau|bay|tam|chin)"
_WORD_STAT_UNITS = ["khach hang", "nguoi dung", "cua hang", "chi nhanh",
                    "nguoi", "khach"]
# Core-completion extension (evaluation R1-D10): chained scales
# ("hàng trăm nghìn", "hai trăm nghìn"), the "hàng chục <scale>" shape,
# quantifier-led scales ("cả nghìn", "hơn trăm") and a bare scale word
# ("Nghìn khách hàng tin tưởng") — all shapes the original grammar
# missed at a 58% recall. Sub-scale word numbers ("sáu mươi khách
# hàng") remain a documented non-goal: without a scale word they are
# indistinguishable from ordinary counted prose.
_WORD_NUMBER = (
    r"(?:hang\s+(?:tram|nghin|trieu)(?:\s+(?:nghin|trieu))?"
    r"|hang\s+chuc\s+(?:nghin|trieu)"
    r"|(?:ca|hon|gan|khoang)\s+(?:tram|nghin|trieu)"
    r"|" + _WORD_DIGIT + r"\s+(?:tram|nghin|trieu)"
    r"(?:\s+(?:nghin|trieu))?"
    r"(?:\s+(?:le|linh)\s+" + _WORD_DIGIT + r")?"
    r"(?:\s+" + _WORD_DIGIT + r"\s+muoi(?:\s+" + _WORD_DIGIT + r")?)?"
    r"|" + _WORD_DIGIT + r"\s+muoi\s+(?:nghin|trieu)"
    r"|" + _WORD_DIGIT + r"\s+chuc\s+(?:nghin|trieu)"
    r"|muoi(?:\s+" + _WORD_DIGIT + r")?\s+(?:nghin|trieu)"
    r"|(?:nghin|trieu))"
)
_WORD_STAT_RE = re.compile(
    r"\b" + _WORD_NUMBER + r"\s+(" + "|".join(_WORD_STAT_UNITS) + r")\b")
# REMOVED (evaluation R1): _FACT_NUMBER_TOKENS = {"185", "250", "1000"}
# used to exempt any statistic whose number happened to equal a fact
# number — "250 khách hàng", "1000 khách hàng đầu tiên" sailed through
# BECAUSE 250 is the pack weight. A number is now exempt only when it is
# literally part of a money amount or a weight mention (span overlap,
# checked in check_section) — the fact-shaped exemptions that remain are
# structural, not numerical coincidences.

# Percent mentions that are COMPOSITION, not statistics (evaluation R1):
# "100% hạt Arabica nguyên chất" states what the pack is made of. A
# percent directly followed by an ingredient noun is composition; any
# other bare percent ("tăng 20% doanh số") is a statistic-shaped claim.
_COMPOSITION_NOUNS_NORM = {"hat", "arabica", "robusta", "ca", "phe",
                           "cacao", "ca cao"}


def _is_composition_percent(sentence: str, m: re.Match) -> bool:
    after = normalize_for_match(sentence[m.end():m.end() + 30])
    tokens = after.split()
    if not tokens:
        return False
    return tokens[0] in _COMPOSITION_NOUNS_NORM or \
        " ".join(tokens[:2]) in _COMPOSITION_NOUNS_NORM


def _known_prices(facts: dict) -> set[int]:
    """Every price the facts doc VERIFIES: the primary pack price plus
    the optional `known_prices_vnd` list (other pack sizes / product
    lines whose prices the facts doc records — per-brand fact bases,
    core-completion round). An amount equal to any of them is a correct
    price, not a wrong one. The shipped Mộc Brew facts carry no list,
    so their known set is exactly {185000} as before."""
    prices = {_fact_price(facts)}
    for value in (facts.get("facts") or {}).get("known_prices_vnd") or []:
        prices.add(int(value))
    return prices


def _allowed_weights(facts: dict) -> tuple[float, ...]:
    """Pack weights the facts doc allows. Default (and the shipped
    Mộc Brew behaviour): the primary pack weight and 1kg. A facts doc
    may declare `pack_weights_g` explicitly (a brand selling
    250/500/1000g packs) — the weight check then uses exactly that."""
    f = facts["facts"]
    declared = f.get("pack_weights_g")
    if declared:
        return tuple(float(w) for w in declared)
    return (float(f["pack_weight_g"]), 1000.0)


def check_section(text: str, facts: dict, brand: dict, section_id: str = "") -> tuple[list[dict], list[dict]]:
    issues: list[dict] = []
    flagged: list[dict] = []
    f = facts["facts"]
    price = _fact_price(facts)
    known_prices = _known_prices(facts)
    pairs = _origin_pairs(facts)

    # --- money: every amount PRESENTED AS THE PRODUCT PRICE must be a
    #     price the facts doc verifies. Amounts framed as a shipping
    #     fee / total / discount / old price / unit-consistent bulk
    #     amount are doing a different job (see _money_role) and are
    #     not price claims. ---
    pack_weight = float(f["pack_weight_g"])
    for start, end, value, raw in find_money_amounts(text):
        if value not in known_prices and _money_role(text, start, end, value, price,
                                                     pack_weight) is None:
            issues.append({"section_id": section_id, "type": "wrong_price",
                           "detail": f"{raw} = {value}đ != fact {_price_display(facts)}"})

    # --- weight / delivery ---
    allowed_weights = _allowed_weights(facts)
    for m in WEIGHT.finditer(text):
        if _weight_grams(m) not in allowed_weights:
            issues.append({"section_id": section_id, "type": "unknown_weight", "detail": m.group(0)})
    _days = [int(n) for n in re.findall(r"\d+", str(f.get("delivery_days") or ""))]
    # A facts doc with NO delivery fact (a brand whose delivery promise
    # was never recorded) skips the delivery checks entirely — judging
    # delivery claims against an empty fact would flag every phrasing.
    delivery_known = bool(_days)
    fact_lo = _days[0] if _days else 0
    fact_hi = _days[1] if len(_days) > 1 else fact_lo

    def _single_delivery_issue(value: int, raw: str) -> None:
        if not (fact_lo <= value <= fact_hi):
            issues.append({"section_id": section_id, "type": "wrong_delivery",
                           "detail": raw})

    # Delivery RANGES are checked per sentence and only with delivery
    # context (evaluation R1): the same "5-7 ngày" shape is a use-by
    # window in "dùng trong 5-7 ngày" and a delivery claim in "giao
    # trong 5-7 ngày" — the sentence's verbs decide, not the digits.
    # All delivery checks are skipped when the facts doc records no
    # delivery fact (delivery_known is False).
    for sentence in split_sentences(text) if delivery_known else []:
        # "trong vòng N ngày" and "chậm nhất N ngày" are unambiguous
        # promise shapes — they are checked WITHOUT the context gate
        # (R1's missed case "Hàng tới nơi trong vòng 5 ngày" has no
        # delivery verb at all). The remaining forms stay gated.
        for rx in (DELIVERY_WITHIN, DELIVERY_LATEST):
            for m in rx.finditer(sentence):
                _single_delivery_issue(int(m.group(1)), m.group(0))
        if not _has_delivery_context(sentence):
            continue
        for rx in (DELIVERY, DELIVERY_RANGE_WORDS):
            for m in rx.finditer(sentence):
                if (int(m.group(1)), int(m.group(2))) != (fact_lo, fact_hi):
                    issues.append({"section_id": section_id,
                                   "type": "wrong_delivery", "detail": m.group(0)})
        for m in DELIVERY_AFTER.finditer(sentence):
            _single_delivery_issue(int(m.group(1)), m.group(0))
        for m in DELIVERY_HOURS.finditer(sentence):
            days = int(m.group(1)) / 24
            if days < fact_lo or days > fact_hi:
                issues.append({"section_id": section_id,
                               "type": "wrong_delivery", "detail": m.group(0)})
    # Single-value delivery: inside the fact range passes ("giao trong
    # 3 ngày" for a 2-3 fact), outside contradicts it. A NEGATED delivery
    # verb is a denial, not a promise ("Mộc Brew không giao trong 5
    # ngày") — evaluation R1 false-flag, now guarded like claims are.
    for m in SINGLE_DELIVERY.finditer(text) if delivery_known else []:
        prefix = normalize_for_match(text[max(0, m.start() - 60):m.start()])
        if prefix.rstrip().endswith("khong") or \
                re.search(r"khong\s+(giao|ship|nhan|van chuyen)$", prefix.rstrip()):
            continue
        _single_delivery_issue(int(m.group(1)), m.group(0))
    # Same-day delivery claims promise 0 days — they contradict any
    # multi-day fact range. Phrasings (evaluation R1): "giao ngay trong
    # ngày", "giao/ship/nhận trong ngày", "trong ngày là tới", and the
    # "hỏa tốc" (express) family next to a delivery verb.
    norm_text = normalize_for_match(text)
    _same_day = ("giao ngay trong ngay", "giao trong ngay", "ship trong ngay",
                 "nhan trong ngay", "trong ngay la toi")
    if delivery_known and fact_lo > 0 and (any(p in norm_text for p in _same_day)
                        or ("hoa toc" in norm_text
                            and ("giao" in norm_text or "ship" in norm_text))):
        issues.append({"section_id": section_id, "type": "wrong_delivery",
                       "detail": f"giao ngay trong ngày (same-day) != fact {f['delivery_days']}"})

    fact_roast_day_norm = normalize_for_match(str(f.get("roast_weekday", "")))

    for sentence in split_sentences(text):
        marked = NEEDS_SOURCE in sentence
        norm = normalize_for_match(sentence)

        # --- roast weekday: CLAUSE-scoped (evaluation R1-D5). The old
        #     rule — any weekday in any sentence containing "rang", with
        #     negation searched over the WHOLE prefix — conflated
        #     activities ("Thứ Sáu là ngày giao hàng; hạt rang vào thứ
        #     Ba" flagged thứ Sáu) and let one negation bleed forward
        #     ("không rang vào thứ Ba mà rang vào thứ Năm" hid the
        #     affirmed thứ Năm). Now: a weekday counts as a roast claim
        #     only inside a clause that itself mentions roasting, and a
        #     negation covers a weekday only LOCALLY — a "không rang"
        #     is superseded by a later affirmed "rang" before the
        #     weekday, and a post-posed denial ("thứ Tư không phải
        #     ngày rang") is honoured too. ---
        if fact_roast_day_norm and "rang" in norm:
            roast_flagged = False
            for clause in re.split(r"[,;]", sentence):
                cnorm = normalize_for_match(clause)
                if "rang" not in cnorm:
                    continue
                for m in _WEEKDAY_RE_NORM.finditer(cnorm):
                    before = cnorm[:m.start()]
                    neg = list(_ROAST_NEGATED_RE.finditer(before))
                    if neg and "rang" not in before[neg[-1].end():]:
                        continue  # locally denied, never re-affirmed
                    if re.search(r"khong phai( la)?( ngay| rang)",
                                 cnorm[m.end():m.end() + 24]):
                        continue  # post-posed denial
                    token = (m.group(1) or "chu nhat")
                    if _WEEKDAY_CANON_NORM.get(token) != fact_roast_day_norm:
                        issues.append({"section_id": section_id, "type": "wrong_roast_day",
                                       "detail": f"{m.group(0)} != fact {f['roast_weekday']}"})
                        roast_flagged = True
                        break
                if roast_flagged:
                    break

        # --- origin pairing: per CLAUSE of the raw sentence (split on
        #     commas/semicolons/dashes and the clause joiners "còn"/"và"),
        #     each bean occurrence pairs with the NEAREST region in its
        #     clause (see _asserted_wrong_origin). Clause-splitting keeps
        #     "Arabica cho vị chua, còn Robusta Đắk Lắk cho hậu đậm"
        #     clean: the unpinned bean's clause names no region at all.
        #     The pairs come from the ACTIVE facts doc (_origin_pairs). ---
        flagged_beans: set[str] = set()
        for clause in re.split(r"[,;:—–-]|\bcòn\b|\bvà\b", sentence):
            cnorm = normalize_for_match(clause)
            if not cnorm:
                continue
            for bean, home in pairs.items():
                if bean in flagged_beans:
                    continue
                wrong = _asserted_wrong_origin(
                    cnorm, bean, home, _wrong_regions(pairs, home))
                if wrong:
                    issues.append({"section_id": section_id, "type": "wrong_origin",
                                   "detail": f"{bean} paired with {wrong}, fact is {bean} {home}"})
                    flagged_beans.add(bean)

        # --- another region claimed as the brand's bean source:
        #     CLAUSE-scoped (core-completion round). The old test was
        #     sentence-wide substring matching, and the brand's own name
        #     is itself a sourcing marker — so a COMPARISON clause
        #     naming another region ("Khác với hạt Bảo Lộc..., Mộc
        #     Brew dùng Arabica Cầu Đất") flagged as the brand's source.
        #     Region and marker must now share a clause, and a clause
        #     about a competitor ("đối thủ") never counts. ---
        for clause in re.split(r"[,;:—–-]|\bcòn\b|\bvà\b", sentence):
            cnorm = normalize_for_match(clause)
            if not cnorm or "doi thu" in cnorm:
                continue
            region_hit = next((r for r in _OTHER_REGIONS_NORM if r in cnorm), None)
            if region_hit and any(marker in cnorm for marker in _SOURCING_MARKERS_NORM):
                issues.append({"section_id": section_id, "type": "unknown_origin",
                               "detail": f"{region_hit} presented as bean source; facts: {', '.join(f.get('origins') or [])}"})
                break

        # --- claims: prohibited phrases block, evidence-requiring phrases and
        #     statistic-shaped numbers block unless marked [NEEDS SOURCE].
        #     Normalized, negation-aware matching (find_claim): a denied
        #     claim is not an assertion. ---
        claim_found = False

        def _record_claim(phrases: list[str], issue_type: str) -> bool:
            """Record the first claim from `phrases` in this sentence.
            Marked [NEEDS SOURCE] -> flagged. Attributed to a competitor
            or reported as a question (see _claim_attributed) -> flagged.
            Otherwise -> a blocking issue of `issue_type`."""
            detail = find_claim_detail(sentence, phrases)
            if detail is None:
                return False
            hit, start, cnorm = detail
            if marked:
                flagged.append({"section_id": section_id, "type": "flagged_claim",
                                "detail": hit})
            elif _claim_attributed(cnorm, start):
                flagged.append({"section_id": section_id, "type": "flagged_claim",
                                "detail": f"{hit} (attributed)"})
            else:
                issues.append({"section_id": section_id, "type": issue_type,
                               "detail": hit})
            return True

        if _record_claim(prohibited_claims(brand), "prohibited_claim"):
            claim_found = True
        elif _record_claim(CLAIM_PHRASES, "unsupported_claim"):
            claim_found = True
        # EVERY percent in the sentence is examined (review L1): the
        # old code looked at the FIRST percent only, so a composition
        # percent first ("100% hạt Arabica, tăng 20% doanh số") made
        # the genuinely unsupported second percent invisible. The
        # first NON-composition percent decides, exactly as the first
        # percent used to — one entry per sentence, as before.
        pct = next((m for m in PERCENT.finditer(sentence)
                    if not _is_composition_percent(sentence, m)), None)
        if not claim_found and pct:
            if marked:
                # A marked percent claim is FLAGGED, never silently
                # dropped (evaluation R1: "tăng 20% doanh số [NEEDS
                # SOURCE]" used to vanish from every report).
                flagged.append({"section_id": section_id, "type": "flagged_claim",
                                "detail": pct.group(0)})
            else:
                issues.append({"section_id": section_id,
                               "type": "unsupported_statistic",
                               "detail": sentence[:60]})
            claim_found = True

        # --- unsupported statistic-shaped numbers (digit + countable unit).
        #     The old fact-token exemption and hyphen-range guard are gone
        #     (evaluation R1): "250 khách hàng" and "5-7 khách hàng mới"
        #     are statistics and are checked. A number is skipped only
        #     when it is literally part of a money amount or a weight
        #     mention in the same sentence (span overlap), or — for the
        #     bare units người/khách — when it has no claim-ish context. ---
        _fact_spans = [(s, e) for s, e, _v, _r in find_money_amounts(sentence)]
        _fact_spans += [(m.start(), m.end()) for m in WEIGHT.finditer(sentence)]
        for m in _STAT_RE.finditer(sentence):
            if any(m.start() < e and s < m.end() for s, e in _fact_spans):
                continue  # the price/weight itself, not a statistic
            if m.group(2).lower() in _STAT_BARE_UNITS \
                    and not _stat_context_ok(sentence, m):
                continue  # serving/group prose, not a claim
            if marked:
                flagged.append({"section_id": section_id, "type": "flagged_claim",
                                "detail": m.group(0)})
            else:
                issues.append({"section_id": section_id, "type": "unsupported_number",
                               "detail": m.group(0)})

        # --- unsupported statistics with the number in WORDS (normalized
        #     sentence; scale shapes only — see _WORD_STAT_RE) ---
        for m in _WORD_STAT_RE.finditer(norm):
            if marked:
                flagged.append({"section_id": section_id, "type": "flagged_claim",
                                "detail": m.group(0)})
            else:
                issues.append({"section_id": section_id, "type": "unsupported_number",
                               "detail": m.group(0)})

    # --- origin pairing SPLIT ACROSS SENTENCES (evaluation R1 §5):
    #     "Hạt Arabica của Mộc Brew. Hạt này được trồng ở Đắk Lắk." —
    #     per-sentence checking never sees bean + region together.
    #     Bounded: the first sentence must name exactly ONE bean and NO
    #     pair region, and the next sentence must open with a pronoun
    #     reference ("hạt này", "loại này", "giống này", "hạt đó") and
    #     not name the bean itself (that case is the per-sentence
    #     check's job). The pairing itself is judged by the same
    #     nearest-region assertion logic on a synthetic join. ---
    _PRONOUN_HEADS = ("hat nay", "loai nay", "giong nay", "hat do")
    all_sentences = split_sentences(text)
    for i in range(len(all_sentences) - 1):
        n1 = normalize_for_match(all_sentences[i])
        beans1 = [b for b in pairs
                  if re.search(rf"\b{re.escape(b)}\b", n1)]
        if len(beans1) != 1:
            continue
        bean = beans1[0]
        home = pairs[bean]
        wrongs = _wrong_regions(pairs, home)
        if any(r in n1 for r in [home, *wrongs]):
            continue  # a region is already in sentence 1 — not a split
        n2 = normalize_for_match(all_sentences[i + 1])
        if re.search(rf"\b{re.escape(bean)}\b", n2):
            continue
        head = " ".join(n2.split()[:3])
        if not any(p in head for p in _PRONOUN_HEADS):
            continue
        wrong = _asserted_wrong_origin(f"{bean} {n2}", bean, home, wrongs)
        if wrong:
            issues.append({"section_id": section_id, "type": "wrong_origin",
                           "detail": f"{bean} paired with {wrong} across sentences, "
                                     f"fact is {bean} {home}"})
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


# Brief-level negation (evaluation R8): a brief that DISCLAIMS a claim
# ("cam kết không chữa mất ngủ") is not REQUESTING it. The brief-level
# pattern is wider than the draft-level NEGATED_PREFIX_RE — it also
# honours a bare "không"/"chưa từng" right before the claim — because a
# brief is an instruction ABOUT the writing, not the writing itself.
# "không chỉ …" still affirms (the "chỉ" intervenes, so the prefix does
# not end with the negation) and an unnegated occurrence anywhere else
# in the brief still refuses. Draft text keeps the narrower rule.
_BRIEF_NEGATED_PREFIX_RE = re.compile(
    r"(khong phai|chang phai|dau phai|khong nhan|khong dam nhan"
    r"|chua tung nhan|chua tung|chua bao gio|khong|chang)( la)?$")


def find_refused_claims(brief_text: str, brand: dict) -> list[str]:
    """Claims the BRIEF asks for that the core must refuse to write.
    Normalized matching (diacritics/punctuation-insensitive), so a brief
    written without diacritics ("100% chua mat ngu") is still refused.
    Negation-aware (see _BRIEF_NEGATED_PREFIX_RE): a disclaimed claim is
    not a request; any unnegated occurrence still refuses the claim."""
    norm = normalize_for_match(brief_text)
    refused: list[str] = []
    for phrase in prohibited_claims(brand):
        rx = _phrase_regex(phrase)
        if rx is None:
            continue
        for m in rx.finditer(norm):
            if _BRIEF_NEGATED_PREFIX_RE.search(norm[:m.start()].rstrip()):
                continue  # disclaimed here — keep looking for a real ask
            refused.append(phrase)
            break
    return refused
