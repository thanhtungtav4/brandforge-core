"""Style Extraction — a writing-style fingerprint measured from samples.

The user pastes links/texts whose voice they like; this module turns those
samples into a **Style Profile**:

- a DETERMINISTIC fingerprint — code measures, never guesses (sentence
  length median AND spread/burstiness, short/long sentence share, question
  and first-person rates, number density, transition habits, vocabulary
  richness);
- a QUALITATIVE schema — tone traits, opening/closing patterns, signature
  devices, stance, never-do list. Code only defines, validates and stores
  these fields; a MODEL fills them in after actually reading the samples;
- optionally, short exemplar passages taken from the samples.

COPYRIGHT RULE: excerpts are stored ONLY when the caller explicitly passes
store_excerpts=True, which is reserved for the user's OWN or licensed
samples. The default is False: for other people's writing we keep the
distilled traits and the measured fingerprint, never their passages.

Persistence is NOT here: all style-profile file IO lives in loaders.py
(load_style_profile / list_style_profiles / save_style_profile).

ALGORITHM VERSIONING (core-completion round, evaluation R4): every
profile built by the current code carries `algorithm_version` plus a
`consistency_detail` block (the per-sample key metrics behind its
consistency warnings). A stored profile whose stamp predates the
current algorithm is REFRESHED on load (refresh_profile): its
sample-count and consistency warnings are recomputed from the stored
evidence instead of being served frozen — a warning written by an
older, buggier fingerprint must not outlive the fix. When the stored
evidence cannot re-verify a consistency warning (profiles built
before per-sample metrics were stored), the warning is DROPPED and
the refresh says so; rebuild from the samples for a fresh check.
"""
from __future__ import annotations

import copy
import hashlib
import re
import statistics
import unicodedata
from collections import Counter

from .linter import is_table_like, split_sentences

#: Version of the fingerprint/consistency algorithm that built (or
#: last refreshed) a profile. Bump when fingerprint or warning
#: semantics change; refresh_profile migrates older profiles on load.
ALGORITHM_VERSION = 2

#: Type-token ratio is computed on at most this many leading tokens:
#: raw TTR falls mechanically as a text grows, so an uncapped TTR
#: makes a 200-word draft and a 2,000-word profile sample
#: incomparable (evaluation R4). The cap makes the metric a property
#: of the writing, not of the sample size, beyond the window.
TTR_WINDOW_TOKENS = 500

# ---------------------------------------------------------------------------
# Constants (documented thresholds — tune here, nowhere else)
# ---------------------------------------------------------------------------

#: Cross-sample coefficient of variation (pstdev / mean) above which a key
#: metric is considered divergent — the classic "mixed authors" signal.
DIVERGENCE_CV = 0.35

#: CV is meaningless when a metric's mean is near zero (calibration F2):
#: first-person rates of [0, 0, 0, 0, 0.1] have CV 2.0 and triggered a
#: "possibly mixed authors" warning on five posts by ONE author. Below
#: this mean (rates are per 100 words; sentence metrics are word counts
#: and never land here in practice), divergence is judged on the
#: ABSOLUTE spread instead: the samples warn only when their pstdev
#: exceeds DIVERGENCE_ABS_PSTDEV — real disagreement, not division by
#: almost-nothing.
DIVERGENCE_MIN_MEAN = 0.5
DIVERGENCE_ABS_PSTDEV = 0.5

#: Below this many total sample words a fingerprint is shaky.
MIN_RELIABLE_WORDS = 800

#: Recommended sample count (from ONE author / one voice).
RECOMMENDED_SAMPLES = "3-5"

#: VN transition / discourse markers whose frequency is part of the voice.
TRANSITIONS = ["tuy nhiên", "nhưng", "vì vậy", "thực ra", "hóa ra",
               "đầu tiên", "sau đó", "cuối cùng", "ví dụ", "nói cách khác",
               "tóm lại", "đặc biệt", "nhất là"]

#: First-person markers (counted on tokens, bigrams first).
_FIRST_PERSON_BIGRAMS = {("chúng", "mình"), ("chúng", "tôi")}
_FIRST_PERSON_SINGLES = {"mình", "tôi", "ta"}

#: Small built-in VN stopword list for top_words (function words carry no
#: voice signal; content words do).
STOPWORDS = {
    "và", "của", "là", "có", "cho", "các", "những", "này", "đó", "được",
    "khi", "với", "trong", "một", "không", "đã", "sẽ", "thì", "mà", "ở",
    "trên", "để", "từ", "theo", "vào", "ra", "lên", "xuống", "cũng", "rất",
    "nhiều", "việc", "làm", "đi", "đến", "về", "lại", "hơn", "như", "nếu",
    "vì", "nên", "nhưng", "còn", "hay", "hoặc", "tại", "bằng", "qua", "sau",
    "trước", "mỗi", "từng", "cái", "con", "sự", "bị", "đang", "chúng",
    "người", "gì", "nào", "ai", "đâu", "sao", "bao", "nhiêu", "khi",
    "thế", "vậy", "ấy", "kia", "nọ", "rồi", "nhé", "nhỉ", "ạ", "ơi",
    "chỉ", "vẫn", "đều", "cả", "hết", "tới", "nữa", "thôi", "thật",
}

_WORD_RE = re.compile(r"[a-zà-ỹ0-9]+")

#: The qualitative schema a model fills in after reading the samples.
#: Unknown keys are IGNORED on build (documented choice: forward-compatible
#: with newer model outputs); missing keys default to empty.
QUALITATIVE_FIELDS = ["tone_traits", "opening_patterns", "closing_patterns",
                      "signature_devices", "stance", "never_do"]


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _round(x: float) -> float:
    return round(float(x), 2)


# ---------------------------------------------------------------------------
# Fingerprint — deterministic, code measures everything
# ---------------------------------------------------------------------------

def fingerprint(text: str) -> dict:
    """Measure the style fingerprint of ONE text.

    Rates are documented per field: question/exclamation rate is per 100
    SENTENCES; first-person / reader / number density are per 100 WORDS;
    shares are fractions (0-1). Sentence stats use the population stdev
    (statistics.pstdev) so a single-sentence text has stdev 0.
    Paragraphs split on blank lines; when the text has no blank lines,
    sentences are grouped into chunks of 3 as pseudo-paragraphs.

    Sentence statistics ignore table-like lines (evaluation R4): a
    markdown price table's rows are not sentences, and counting them
    corrupted the median/stdev of any sample containing one. The
    type-token ratio is computed on the first TTR_WINDOW_TOKENS
    tokens (see the constant's note).
    """
    sentences = [s for s in split_sentences(text) if not is_table_like(s)]
    lengths = [len(s.split()) for s in sentences]
    ws_tokens = text.split()
    word_count = len(ws_tokens)
    tokens = _tokens(text)

    if lengths:
        sl = {"median": _round(statistics.median(lengths)),
              "mean": _round(statistics.fmean(lengths)),
              "stdev": _round(statistics.pstdev(lengths))}
        short_share = _round(sum(1 for n in lengths if n <= 6) / len(lengths))
        long_share = _round(sum(1 for n in lengths if n >= 20) / len(lengths))
    else:
        sl = {"median": 0.0, "mean": 0.0, "stdev": 0.0}
        short_share = long_share = 0.0

    # Paragraphs: blank-line split, else sentence groups of 3.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) >= 2:
        para_lengths = [len(p.split()) for p in paragraphs]
    elif sentences:
        para_lengths = [sum(len(s.split()) for s in sentences[i:i + 3])
                        for i in range(0, len(sentences), 3)]
    else:
        para_lengths = []
    paragraph_len_median = _round(statistics.median(para_lengths)) if para_lengths else 0.0

    n_sent = len(sentences) or 1
    per100w = (100.0 / word_count) if word_count else 0.0

    # First person: scan tokens, bigrams ("chúng mình") before singles.
    # "người ta" is generic third person, NOT first person — its "ta" is
    # never counted.
    fp = 0
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens) and (tokens[i], tokens[i + 1]) in _FIRST_PERSON_BIGRAMS:
            fp += 1
            i += 2
            continue
        if tokens[i] in _FIRST_PERSON_SINGLES:
            if not (tokens[i] == "ta" and i > 0 and tokens[i - 1] == "người"):
                fp += 1
        i += 1

    low = text.lower()
    transition_counts = {t: low.count(t) for t in TRANSITIONS}
    transition_freq = [{"term": t, "count": c} for t, c in
                       sorted(transition_counts.items(), key=lambda kv: -kv[1]) if c > 0]

    content_words = [t for t in tokens if t not in STOPWORDS and not t.isdigit()]
    top_words = [{"word": w, "count": c} for w, c in Counter(content_words).most_common(15)]
    window = tokens[:TTR_WINDOW_TOKENS]
    ttr = _round(len(set(window)) / len(window)) if window else 0.0

    return {
        "word_count": word_count,
        "sentence_count": len(sentences),
        "sentence_len": sl,
        "short_sentence_share": short_share,
        "long_sentence_share": long_share,
        "paragraph_len_median": paragraph_len_median,
        "question_rate": _round((text.count("?") + text.count("？")) / n_sent * 100),
        "exclamation_rate": _round((text.count("!") + text.count("！")) / n_sent * 100),
        "first_person_rate": _round(fp * per100w),
        "reader_rate": _round(tokens.count("bạn") * per100w),
        "number_density": _round(sum(1 for t in ws_tokens if any(ch.isdigit() for ch in t)) * per100w),
        "transition_freq": transition_freq,
        "type_token_ratio": ttr,
        "top_words": top_words,
    }


def _metric(fp: dict, name: str) -> float:
    if name == "sentence_median":
        return fp["sentence_len"]["median"]
    if name == "sentence_stdev":
        return fp["sentence_len"]["stdev"]
    return float(fp[name])


_KEY_METRICS = ["sentence_median", "sentence_stdev", "first_person_rate"]


def analyze_samples(samples: list[str]) -> dict:
    """Fingerprint each sample + the aggregate, with a consistency report.

    The aggregate is computed over the concatenated samples (paragraph-joined).
    Consistency: for the key metrics (sentence median, sentence stdev,
    first-person rate) the cross-sample coefficient of variation
    (pstdev/mean) is compared against DIVERGENCE_CV — above it, the samples
    probably do not come from one voice.
    """
    samples = [s for s in samples if s and s.strip()]
    # Dedupe (evaluation R4): the same text pasted three times is ONE
    # sample, not three — duplicates used to inflate the sample count
    # and word total while adding zero voice information. Exact and
    # whitespace-normalised duplicates are dropped and the drop is
    # reported, never silent.
    seen: set[str] = set()
    unique: list[str] = []
    dropped = 0
    for s in samples:
        key = " ".join(s.split())
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        unique.append(s)
    samples = unique
    per_sample = [fingerprint(s) for s in samples]
    aggregate = fingerprint("\n\n".join(samples)) if samples else fingerprint("")

    warnings: list[str] = []
    if dropped:
        warnings.append(
            f"{dropped} duplicate sample(s) dropped — a repeated text is "
            f"one sample, not independent evidence of the voice")
    if len(samples) < 3:
        warnings.append(
            f"only {len(samples)} sample(s) — {RECOMMENDED_SAMPLES} samples "
            f"from ONE author recommended")
    total_words = aggregate["word_count"]
    if total_words < MIN_RELIABLE_WORDS:
        warnings.append(
            f"total words {total_words} < {MIN_RELIABLE_WORDS} — "
            f"fingerprint may be unreliable")

    slim = [{name: _metric(fp, name) for name in _KEY_METRICS}
            for fp in per_sample]
    divergence, divergence_warnings = _divergence(slim)
    warnings += divergence_warnings

    return {"sample_count": len(samples),
            "per_sample": per_sample,
            "fingerprint": aggregate,
            "consistency": {"divergence_cv": divergence,
                            "warnings": warnings},
            "consistency_detail": {"divergence_cv": divergence,
                                   "per_sample": slim}}


def _divergence(per_sample_metrics: list[dict]) -> tuple[dict, list[str]]:
    """The consistency judgement over per-sample key metrics, shared by
    analyze_samples and refresh_profile so a refreshed profile is
    judged by EXACTLY the rule that built it.

    CV rule when the mean is meaningful; near a zero mean CV explodes
    on agreement (calibration F2), so divergence is judged on the
    ABSOLUTE spread instead — real disagreement, not division by
    almost-nothing.
    """
    divergence: dict[str, float] = {}
    warnings: list[str] = []
    if len(per_sample_metrics) >= 2:
        for name in _KEY_METRICS:
            values = [m[name] for m in per_sample_metrics]
            mean = statistics.fmean(values)
            if mean > 0:
                divergence[name] = _round(statistics.pstdev(values) / mean)
            if mean >= DIVERGENCE_MIN_MEAN:
                if divergence.get(name, 0.0) > DIVERGENCE_CV:
                    warnings.append(
                        f"samples diverge on {name} "
                        f"(CV {divergence[name]} > {DIVERGENCE_CV}) — "
                        f"possibly mixed authors")
            elif statistics.pstdev(values) > DIVERGENCE_ABS_PSTDEV:
                warnings.append(
                    f"samples diverge on {name} "
                    f"(spread {round(statistics.pstdev(values), 2)} with "
                    f"near-zero mean {round(mean, 2)}) — "
                    f"possibly mixed authors")
    return divergence, warnings


# ---------------------------------------------------------------------------
# Profile building — code validates/stores; the MODEL supplies `qualitative`
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    nfkd = unicodedata.normalize("NFD", name.lower().replace("đ", "d"))
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-") or "style"


def _validate_qualitative(qualitative: dict | None) -> dict:
    """Keep only QUALITATIVE_FIELDS (unknown keys ignored); missing keys
    default to empty. tone_traits entries are normalized to {trait, do, dont}."""
    q = qualitative or {}
    out: dict = {}
    for field in QUALITATIVE_FIELDS:
        value = q.get(field)
        if field == "tone_traits":
            traits = []
            for item in value or []:
                if isinstance(item, dict):
                    traits.append({"trait": str(item.get("trait", "")),
                                   "do": str(item.get("do", "")),
                                   "dont": str(item.get("dont", ""))})
                elif item:
                    traits.append({"trait": str(item), "do": "", "dont": ""})
            out[field] = traits
        elif field == "stance":
            out[field] = str(value or "")
        else:
            out[field] = [str(v) for v in (value or [])]
    return out


def _passages(text: str) -> list[str]:
    """Candidate passages of one sample: its paragraphs, or — when it has no
    blank lines — sentence groups, each trimmed to <=120 words at a sentence
    boundary (hard word-truncation only if a single sentence exceeds 120)."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) < 2:
        sentences = split_sentences(text)
        paragraphs = [" ".join(sentences[i:i + 4]) for i in range(0, len(sentences), 4)]
    out = []
    for para in paragraphs:
        words = para.split()
        if len(words) <= 120:
            out.append(para)
            continue
        kept, count = [], 0
        for sentence in split_sentences(para):
            n = len(sentence.split())
            if count + n > 120:
                break
            kept.append(sentence)
            count += n
        out.append(" ".join(kept) if kept else " ".join(words[:120]))
    return [p for p in out if p.strip()]


def build_profile(name: str, samples: list[str], qualitative: dict | None = None,
                  store_excerpts: bool = False, brand_id: str = "moc-brew",
                  profile_id: str | None = None) -> dict:
    """Build a Style Profile dict from samples + model-filled qualitative.

    COPYRIGHT RULE: `store_excerpts` defaults to False — exemplar passages
    from the samples are stored ONLY when the samples are the user's own or
    licensed. Otherwise `exemplars` is [] and `excerpts_note` says so.
    `profile_id` overrides the generated id (slug + content hash), used for
    the shipped default profile.

    Raises ValueError when there are no usable samples: a profile built
    from zero samples has an all-zero fingerprint and would silently
    poison every later style_match comparison (audit round 2).
    """
    samples = [s for s in samples if s and s.strip()]
    if not samples:
        raise ValueError("at least one non-empty sample is required to "
                         "build a style profile")
    analysis = analyze_samples(samples)
    digest = hashlib.sha1("\n".join(samples).encode("utf-8")).hexdigest()[:6]
    pid = profile_id or f"{_slugify(name)}-{digest}"

    exemplars: list[str] = []
    if store_excerpts:
        for sample in samples:
            if len(exemplars) >= 3:
                break
            passages = _passages(sample)
            if passages:
                exemplars.append(passages[0])

    return {
        "id": pid,
        "name": name,
        "brand_id": brand_id,
        "algorithm_version": ALGORITHM_VERSION,
        "created_from": {"sample_count": len(samples),
                         "total_words": analysis["fingerprint"]["word_count"]},
        "fingerprint": analysis["fingerprint"],
        "consistency_warnings": analysis["consistency"]["warnings"],
        "consistency_detail": analysis["consistency_detail"],
        "qualitative": _validate_qualitative(qualitative),
        "exemplars": exemplars,
        "excerpts_note": ("excerpts stored from the samples (user's own / "
                          "licensed texts)" if store_excerpts else
                          "excerpts not stored — fingerprint + distilled "
                          "traits only (default; use store_excerpts=True "
                          "only for your own/licensed samples)"),
    }


def refresh_profile(profile: dict) -> dict:
    """Bring a stored profile's WARNINGS up to the current algorithm.

    A profile already stamped with ALGORITHM_VERSION is returned
    unchanged. An older (or unstamped) profile is copied and:
    - its sample-count / total-words warnings are recomputed from
      `created_from` (the same strings analyze_samples produces);
    - its divergence warnings are recomputed from the stored
      per-sample metrics when the profile carries them
      (`consistency_detail`, stored since ALGORITHM_VERSION 2);
    - when it carries NO per-sample metrics, its divergence warnings
      cannot be re-verified and are DROPPED, with a note saying so —
      serving a warning written by a fixed bug (the shipped CNM
      profile's pre-fix "mixed authors") is worse than dropping it.
    Other warnings (e.g. the duplicate-sample note, a build-time fact)
    pass through untouched. The fingerprint itself is NOT recomputed:
    the samples are not stored, by design (copyright rule).
    """
    if not isinstance(profile, dict):
        return profile
    if profile.get("algorithm_version") == ALGORITHM_VERSION:
        return profile
    refreshed = copy.deepcopy(profile)
    old = refreshed.get("consistency_warnings", [])
    kept = [w for w in old
            if not (w.startswith("only ") or w.startswith("total words ")
                    or w.startswith("samples diverge on "))]
    created = refreshed.get("created_from", {})
    n = created.get("sample_count")
    if isinstance(n, int) and n < 3:
        kept.append(f"only {n} sample(s) — {RECOMMENDED_SAMPLES} samples "
                    f"from ONE author recommended")
    total_words = created.get("total_words")
    if isinstance(total_words, int) and total_words < MIN_RELIABLE_WORDS:
        kept.append(f"total words {total_words} < {MIN_RELIABLE_WORDS} — "
                    f"fingerprint may be unreliable")
    detail = refreshed.get("consistency_detail") or {}
    per_sample = detail.get("per_sample") or []
    if len(per_sample) >= 2:
        _, divergence_warnings = _divergence(per_sample)
        kept += divergence_warnings
    elif any(w.startswith("samples diverge on ") for w in old):
        kept.append(
            "consistency warnings from an older algorithm version "
            "could not be re-verified (no stored per-sample metrics) "
            "and were dropped — rebuild the profile from its samples "
            "for a fresh consistency check")
    refreshed["consistency_warnings"] = kept
    refreshed["warnings_refreshed_from"] = profile.get("algorithm_version")
    refreshed["algorithm_version"] = ALGORITHM_VERSION
    return refreshed


# ---------------------------------------------------------------------------
# style_match — how close is a draft to the profile voice?
# ---------------------------------------------------------------------------

#: Below this many words, per-100-word DENSITY metrics swing wildly on
#: honest writing (calibration R6: at 149 words, "bạn" x2 read as a
#: reader_rate of 1.34 against a 0.20 profile). Density metrics on such
#: texts are marked low-reliability and judged with widened ranges;
#: sentence-shape metrics (median, stdev, shares) are length-stable and
#: keep their normal ranges at any length — the calibration trial's
#: stdev miss (3.2 vs 10.44) was real signal and still fails.
SHORT_TEXT_WORDS = 300

#: The per-100-word density metrics (unstable on short texts).
_DENSITY_METRICS = {"first_person_rate", "reader_rate", "number_density"}


def _within(metric: str, profile_value: float, draft_value: float,
            relaxed: bool = False, n_sentences: int | None = None) -> bool:
    """Range rule per metric (documented, symmetric):
    - sentence_median: within ±35% of profile OR ±3 words, whichever larger.
    - sentence_stdev (burstiness): draft/profile ratio within [0.5, 2.0];
      a zero profile stdev requires a near-zero draft stdev (<= 0.5).
    - shares (0-1 fractions): within ±50% OR 0.05 absolute, whichever larger.
    - rates (per 100 words): within ±50% OR 0.5 absolute, whichever larger.
    - RELAXED rates (short texts only, density metrics): within ±100% OR
      1.5 absolute, whichever larger — counting noise at < 300 words is
      larger than the normal band, so the band honestly widens instead
      of failing honest short copy on arithmetic.
    - punctuation rates (per 100 SENTENCES — question/exclamation,
      evaluation R4): within ±60% OR 12 per-100-sentences OR twice the
      sampling granularity (200 / sentence count), whichever largest.
      The band is deliberately wide: a single text legitimately asks
      more or fewer questions than the sample average, and a rate
      estimated from n sentences moves in steps of 100/n — an 8-sentence
      section with zero questions is weak evidence against a profile
      rate of ~23, not a voice swap (the CNM writer-trial regression:
      85 -> 77 without the granularity term). What the metric catches
      is a wholesale swap — a 0-rate profile against a text that ends
      every sentence with '!' (rate ~100) — which the matcher
      previously could not see at all (a draft with every '.' replaced
      by '!' scored a perfect 100 against its own profile).
    """
    if metric == "sentence_median":
        return abs(draft_value - profile_value) <= max(0.35 * profile_value, 3.0)
    if metric == "sentence_stdev":
        if profile_value <= 0:
            return draft_value <= 0.5
        return 0.5 <= draft_value / profile_value <= 2.0
    if metric == "short_sentence_share":
        return abs(draft_value - profile_value) <= max(0.5 * profile_value, 0.05)
    if metric in ("question_rate", "exclamation_rate"):
        granularity = 200.0 / n_sentences if n_sentences else 0.0
        return abs(draft_value - profile_value) <= max(
            0.6 * profile_value, 12.0, granularity)
    if relaxed:
        return abs(draft_value - profile_value) <= max(1.0 * profile_value, 1.5)
    return abs(draft_value - profile_value) <= max(0.5 * profile_value, 0.5)


_ADVICE = {
    "sentence_median": (
        lambda p, d: (f"sentences run longer than the sample voice "
                      f"(median {d} vs ~{p} words): split and shorten sentences"
                      if d > p else
                      f"sentences are shorter than the sample voice "
                      f"(median {d} vs ~{p} words): let some sentences breathe longer")),
    "sentence_stdev": (
        lambda p, d: ("draft sentences too uniform: mix in short 3-6 word "
                      "sentences and some longer ones — burstiness is the "
                      "sample voice's human signature"
                      if d < p else
                      "draft rhythm swings wider than the sample voice: "
                      "even out the extreme sentence lengths")),
    "short_sentence_share": (
        lambda p, d: ("add short punchy sentences (≤6 words) — the samples "
                      "use them to break the rhythm"
                      if d < p else
                      "too many fragments: merge some short sentences")),
    "first_person_rate": (
        lambda p, d: ("the sample voice says 'mình/tôi' more: write in "
                      "first person more often"
                      if d < p else
                      "more first person than the samples: pull back the 'mình/tôi'")),
    "reader_rate": (
        lambda p, d: ("address the reader as 'bạn' more often, like the samples"
                      if d < p else
                      "you address 'bạn' more than the samples do: ease off")),
    "number_density": (
        lambda p, d: ("the samples are more concrete: add specific numbers "
                      "and measurable details"
                      if d < p else
                      "more numbers than the sample voice: drop some figures")),
    "question_rate": (
        lambda p, d: ("the sample voice asks the reader more questions: "
                      "let some statements become genuine questions"
                      if d < p else
                      "more questions than the sample voice: turn some "
                      "back into statements")),
    "exclamation_rate": (
        lambda p, d: ("the sample voice exclaims more than this draft does"
                      if d < p else
                      "exclamation marks are not this voice: the samples "
                      "stay level — remove the '!' endings")),
}

_MATCH_METRICS = ["sentence_median", "sentence_stdev", "short_sentence_share",
                  "first_person_rate", "reader_rate", "number_density",
                  "question_rate", "exclamation_rate"]
_KEY_MATCH = {"sentence_median", "sentence_stdev"}

#: Short-text confidence ceiling (evaluation R4): when a short text
#: passes TWO OR MORE density metrics only because their ranges were
#: widened, the match rests on widened evidence — the score is capped
#: here instead of saturating at 100. (The Đỉnh Cao hype fixture scored
#: a perfect 100 against the Mộc Brew profile on exactly this stack of
#: relaxations.) Texts passing on their normal ranges, and long texts,
#: are never capped.
SHORT_TEXT_SCORE_CAP = 88.0


def style_match(text: str, profile: dict) -> dict:
    """Compare a draft's fingerprint against a Style Profile.

    Score: start 100, −15 per out-of-range KEY metric (sentence median,
    sentence stdev/burstiness), −8 per other out-of-range metric, floor 0.
    Advisory signal — it measures closeness to the sample voice, it does
    not judge whether the draft is good.

    Short texts (< SHORT_TEXT_WORDS words): the per-100-word density
    metrics are marked "reliability": "low" and judged with the widened
    relaxed ranges (see _within); the result then carries a
    `reliability_note` saying so. Sentence-shape metrics always carry
    "reliability": "normal" and their usual ranges.

    The matcher also scores question_rate and exclamation_rate
    (evaluation R4 — previously invisible, so a draft with every '.'
    swapped for '!' scored 100 against its own profile), and caps a
    short text's score at SHORT_TEXT_SCORE_CAP when two or more density
    metrics passed only on the widened ranges.
    """
    # A stored profile from an older algorithm version is refreshed
    # first, so its warnings (surfaced by callers from the profile they
    # passed in — here, the refreshed copy's judgement is what the
    # metrics below are compared under) are current, never frozen.
    profile = refresh_profile(profile)
    draft_fp = fingerprint(text)
    prof_fp = profile.get("fingerprint", {})
    word_count = draft_fp["word_count"]
    short_text = word_count < SHORT_TEXT_WORDS
    metrics, advice, score = [], [], 100.0
    relaxed_only_passes = 0
    for name in _MATCH_METRICS:
        if prof_fp:
            try:
                pv = _metric(prof_fp, name)
            except (KeyError, TypeError):
                continue  # partial/hand-built profile: metric not carried
        else:
            pv = 0.0
        dv = _metric(draft_fp, name)
        relaxed = short_text and name in _DENSITY_METRICS
        n_sent = draft_fp.get("sentence_count") or None
        ok = _within(name, pv, dv, relaxed=relaxed, n_sentences=n_sent)
        if relaxed and ok and not _within(name, pv, dv, relaxed=False,
                                          n_sentences=n_sent):
            relaxed_only_passes += 1  # passed ONLY on the widened range
        metrics.append({"metric": name, "profile_value": pv,
                        "draft_value": dv, "within_range": ok,
                        "reliability": "low" if relaxed else "normal"})
        if not ok:
            score -= 15 if name in _KEY_MATCH else 8
            advice.append(_ADVICE[name](pv, dv))
    score = max(0.0, score)
    capped = short_text and relaxed_only_passes >= 2 and score > SHORT_TEXT_SCORE_CAP
    if capped:
        score = SHORT_TEXT_SCORE_CAP
    result: dict = {"score": score, "metrics": metrics, "advice": advice}
    if short_text:
        result["reliability_note"] = (
            f"short text ({word_count} words < {SHORT_TEXT_WORDS}): "
            f"per-100-word density metrics (first_person_rate, "
            f"reader_rate, number_density) swing wildly at this length, "
            f"so they are marked reliability='low' and judged with "
            f"widened ranges; sentence-shape metrics keep normal ranges")
        if capped:
            result["reliability_note"] += (
                f"; {relaxed_only_passes} density metrics passed only on "
                f"the widened ranges, so the score is capped at "
                f"{SHORT_TEXT_SCORE_CAP} (low-confidence match)")
    return result
