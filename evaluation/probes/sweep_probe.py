"""Rebuilt Round-5 monotonicity random sweep.

420 (base, injector) cases: randomised blog/email bases of 2-6 pool-word
sentences x 4 injectors (banned term, exclamation, weak word, append a
26-word sentence). An inversion is a case where ADDING a violation raises
the brand score or the quality total. Seeded RNG — identical cases on
every run and every checkout.

    python evaluation/probes/sweep_probe.py
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from content_core.gates import evaluate_gate
from content_core.loaders import load_brand, load_facts
from content_core.quality import evaluate_quality, preset_profile

POOL = ("mùi hương thơm nhẹ lan tỏa trong không gian quán nhỏ yên tĩnh ly nước "
        "ấm vị dịu hậu rõ từng ngụm bàn gỗ cửa sổ mở vườn xanh lá cây gió mát "
        "trời khách tới ngồi xuống chiếc ghế bên cạnh").split()

LONG_26 = ("ngụm bàn gỗ cửa sổ mở vườn xanh lá cây gió mát trời khách tới "
           "ngồi xuống chiếc ghế bên cạnh mùi hương thơm nhẹ lan.")

JUDGED = None  # judged axes excluded (renormalised); inversions live in
# the measured axes (brand median / craft rhythm), as in R5 §1c.


def make_base(rng: random.Random) -> str:
    sentences = []
    for _ in range(rng.randint(2, 6)):
        n = rng.randint(8, 14)
        sentences.append(" ".join(rng.choice(POOL) for _ in range(n)).capitalize() + ".")
    return " ".join(sentences)


def inject(text: str, kind: str) -> str:
    if kind == "banned":
        return text.replace(".", " delve.", 1)
    if kind == "exclamation":
        return text.replace(".", "!", 1)
    if kind == "weak":
        return text[:-1] + " tuyệt vời."
    return text + " " + LONG_26  # too_long


def measures(text: str, fmt: str, brand, facts):
    sections = [{"id": "s1", "text": text}]
    gate = evaluate_gate(sections, fmt, brand, facts)
    quality = evaluate_quality(sections, fmt, preset_profile(fmt), JUDGED, brand, facts)
    return gate["hard"]["brand_score"], quality["weighted_total"]


def main() -> None:
    brand, facts = load_brand(), load_facts()
    rng = random.Random(20261122)
    kinds = ["banned", "exclamation", "weak", "too_long"]
    total, inversions = 0, 0
    by_kind: dict[str, int] = {k: 0 for k in kinds}
    for i in range(105):
        fmt = "blog" if i % 2 == 0 else "email"
        base = make_base(rng)
        b0, t0 = measures(base, fmt, brand, facts)
        for kind in kinds:
            total += 1
            b1, t1 = measures(inject(base, kind), fmt, brand, facts)
            if b1 > b0 or t1 > t0:
                inversions += 1
                by_kind[kind] += 1
    print(f"sweep cases: {total}")
    print(f"inversions: {inversions}  by injector: {by_kind}")
    # Regression threshold (review L17): the 9 residual inversions are
    # the documented layer-separation trade-off; the bound leaves
    # noise room but catches a real monotonicity regression.
    ok = inversions <= 12
    print(f"thresholds: inversions<=12 -> {'OK' if ok else 'REGRESSED'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
