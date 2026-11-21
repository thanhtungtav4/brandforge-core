"""Rebuilt Round-2 linter evasion probe.

The 14 successful banned-term evasions quoted verbatim in
evaluation/round-2-linter.md, plus the pipe-disguise sentence-length
evasion. An evasion "succeeds" when lint_section reports no banned_term
violation (for the pipe case: no sentence_too_long violation).

    python evaluation/probes/linter_probe.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from content_core.linter import lint_section
from content_core.loaders import load_brand

PAD = ("Mộc Brew rang hạt vào thứ Ba hằng tuần cho khách quen. "
       "Mùi thơm nhẹ lan tỏa trong không gian quán nhỏ yên tĩnh. "
       "Bạn pha phin chậm rãi rồi ngồi ngắm vườn xanh mát. "
       "Giá một gói hai trăm năm mươi gam là 185.000đ rõ ràng. ")

EVASIONS = [
    ("zwsp U+200B", "Bài này sẽ ele\u200bvate trải nghiệm uống cà phê mỗi sáng của bạn."),
    ("soft hyphen U+00AD", "Bài này sẽ ele\u00advate trải nghiệm uống cà phê mỗi sáng của bạn."),
    ("U+FEFF", "Bài này sẽ ele\ufeffvate trải nghiệm uống cà phê mỗi sáng của bạn."),
    ("zwj U+200D", "Bài này sẽ ele\u200dvate trải nghiệm uống cà phê mỗi sáng của bạn."),
    ("cyrillic a in elevate", "Bài này sẽ elev\u0430te trải nghiệm uống cà phê mỗi sáng của bạn."),
    ("cyrillic a in game-changing", "Đây là dòng hạt g\u0430me-changing cho người mới uống."),
    ("fullwidth latin", "Bài này sẽ ｅｌｅｖａｔｅ trải nghiệm uống cà phê mỗi sáng của bạn."),
    ("digit guard suffix", "Bài này sẽ elevate7 trải nghiệm uống cà phê mỗi sáng của bạn."),
    ("digit guard prefix", "Bài này sẽ 7elevate trải nghiệm uống cà phê mỗi sáng của bạn."),
    ("letter-spacing", "Trải nghiệm uống cà phê thật s e a m l e s s mỗi sáng."),
    ("letter-dotting", "Bài này sẽ e.l.e.v.a.t.e trải nghiệm uống cà phê mỗi sáng."),
    ("html tag split", "Đây là dòng hạt game-<b>changing</b> cho người mới uống."),
    ("html entity", "Đây là dòng hạt game&#45;changing cho người mới uống."),
    ("inserted word", "Ai cũng khen cà phê tốt nhất ở Việt Nam dạo này."),
]

LONG_26 = ("ngụm bàn gỗ cửa sổ mở vườn xanh lá cây gió mát trời khách tới "
           "ngồi xuống chiếc ghế bên cạnh mùi hương thơm nhẹ lan")


def main() -> None:
    brand = load_brand()
    caught, evaded = [], []
    for name, text in EVASIONS:
        violations, _score = lint_section(PAD + text, "blog", brand, "s1")
        types = {v["type"] for v in violations}
        (caught if "banned_term" in types else evaded).append(name)
    print(f"banned-term evasions: {len(evaded)}/14 still evade")
    for n in evaded:
        print(f"  EVADES: {n}")
    for n in caught:
        print(f"  caught: {n}")

    pipe_text = ("Mùi hương thơm nhẹ lan tỏa trong không gian quán nhỏ yên tĩnh vô cùng | "
                 "ly nước ấm vị dịu hậu rõ từng ngụm bàn gỗ cửa sổ mở vườn xanh lá cây | "
                 "gió mát trời khách tới ngồi xuống chiếc ghế bên cạnh mùi hương thơm nhẹ lan tỏa.")
    violations, score = lint_section(pipe_text, "blog", brand, "s1")
    types = {v["type"] for v in violations}
    print(f"pipe-disguise 60-word sentence: sentence_too_long "
          f"{'FIRES (fixed)' if 'sentence_too_long' in types else 'still evades'} "
          f"(score {score})")

    # Negated / quoted banned terms (item 13a): warning, not hard.
    for label, text in [
        ("negated", "Mộc Brew không nhận là tốt nhất Việt Nam như nhiều nơi quảng cáo."),
        ("quoted", "Nhiều quảng cáo ghi “tốt nhất Việt Nam” nhưng không đưa ra căn cứ nào."),
        ("asserted", "Mộc Brew là cà phê tốt nhất Việt Nam hiện nay."),
    ]:
        violations, score = lint_section(PAD + text, "blog", brand, "s1")
        kinds = [(v["type"], bool(v.get("warning"))) for v in violations
                 if "banned" in v["type"]]
        print(f"banned context [{label}]: {kinds} score={score}")


if __name__ == "__main__":
    main()
