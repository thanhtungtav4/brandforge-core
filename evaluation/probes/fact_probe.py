"""Rebuilt Round-1 fact-check mutation probe (205 cases).

Rebuilt from evaluation/round-1-fact-check.md: same category design and
totals (116 false / 84 true / 5 marked), every case quoted in the report
included verbatim, remaining slots filled with cases of the documented
kinds. Run against any checkout of the core:

    python evaluation/probes/fact_probe.py

Prints recall (expected type), false-flag rate and marked handling, plus
per-category detail. Compare runs across commits for before/after.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from content_core.fact_check import check_section
from content_core.loaders import load_brand, load_facts

CLAIM_TYPES = {"prohibited_claim", "unsupported_claim"}
STAT_TYPES = {"unsupported_number", "unsupported_statistic"}

# (category, kind, expected_type_or_None, text)
# kind: F = false (expected issue type must fire), T = true (no blocking
# issue may fire), M = marked (no blocking issue, >=1 flagged entry).
CASES = [
    # ---------------- Money: 15F / 18T ----------------
    ("money", "F", "wrong_price", "Giá một gói là 200.000đ."),
    ("money", "F", "wrong_price", "Giá một gói là ₫200.000."),
    ("money", "F", "wrong_price", "Một gói Mộc Brew giá 190.000đ."),
    ("money", "F", "wrong_price", "Gói 250g giá 210.000 VND."),
    ("money", "F", "wrong_price", "Một gói giá 185,500đ."),
    ("money", "F", "wrong_price", "Giá gói là 200k."),
    ("money", "F", "wrong_price", "Một gói giá 175 nghìn đồng."),
    ("money", "F", "wrong_price", "Giá 199.000đ cho một gói 250g."),
    ("money", "F", "wrong_price", "Một gói giá 200.000."),
    ("money", "F", "wrong_price", "Giá gói hạt là hai trăm nghìn đồng."),
    ("money", "F", "wrong_price", "Một gói giá 200.000 USD."),
    ("money", "F", "wrong_price", "Giá một gói là 200.\n000đ."),
    ("money", "F", "wrong_price", "Giá gói là 185.000đ cho 500g, rẻ hơn hẳn khi tính theo gram."),
    ("money", "F", "wrong_price", "Một gói 250g giá 186.000đ."),
    ("money", "F", "wrong_price", "Giá bán 180.000đ/gói."),
    ("money", "T", None, "Giá một gói là 185.000đ."),
    ("money", "T", None, "Gói 250g giá 185.000 đ."),
    ("money", "T", None, "185.000đ/gói, rang vào thứ Ba."),
    ("money", "T", None, "Giá 185.000 VND cho một gói."),
    ("money", "T", None, "Một gói 185.000 vnđ."),
    ("money", "T", None, "Giá một gói là 185.000 đồng."),
    ("money", "T", None, "Một gói giá 185000đ."),
    ("money", "T", None, "Giá gói là 185k."),
    ("money", "T", None, "Một gói 185 nghìn đồng."),
    ("money", "T", None, "Một gói giá 185 ngàn."),
    ("money", "T", None, "Giá một gói: 185,000đ."),
    ("money", "T", None, "Một gói giá 185.000₫."),
    ("money", "T", None, "(Giá: 185.000đ) áp dụng từ tuần này."),
    ("money", "T", None, "Phí ship cố định 30.000đ mỗi đơn."),
    ("money", "T", None, "Hai gói có tổng 370.000đ."),
    ("money", "T", None, "Giảm 20.000đ cho đơn đầu tiên."),
    ("money", "T", None, "Giá cũ 220.000đ đã được hạ xuống 185.000đ."),
    ("money", "T", None, "Một cân hạt giá 740.000đ."),
    # ---------------- Weight: 10F / 9T ----------------
    ("weight", "F", "unknown_weight", "Gói 300g rang sẵn tiện lợi."),
    ("weight", "F", "unknown_weight", "Gói 300gr rang sẵn."),
    ("weight", "F", "unknown_weight", "Một gói 400g cho cả tuần."),
    ("weight", "F", "unknown_weight", "Gói hạt 0,3kg tiện lợi."),
    ("weight", "F", "unknown_weight", "Gói 250,5g đóng sẵn."),
    ("weight", "F", "unknown_weight", "Một gói 600g để dành uống dần."),
    ("weight", "F", "unknown_weight", "Có gói 750 gam cho nhóm bạn."),
    ("weight", "F", "unknown_weight", "Gói 100g dùng thử."),
    ("weight", "F", "unknown_weight", "Một gói 2kg cho quán nhỏ."),
    ("weight", "F", "unknown_weight", "Gói 450 gram nguyên hạt."),
    ("weight", "T", None, "Gói 250g rang vào thứ Ba."),
    ("weight", "T", None, "Một gói 250 gam nguyên hạt."),
    ("weight", "T", None, "Có gói 1kg cho người uống hằng ngày."),
    ("weight", "T", None, "Một gói 1 kg hạt rang sẵn."),
    ("weight", "T", None, "Gói 1000g cho quán."),
    ("weight", "T", None, "Một gói 1000 gam."),
    ("weight", "T", None, "Gói 250g đóng gói hút chân không."),
    ("weight", "T", None, "Gói 250g, giá 185.000đ."),
    ("weight", "T", None, "Có gói 500g cho người uống nhiều."),
    # ---------------- Delivery: 15F / 11T ----------------
    ("delivery", "F", "wrong_delivery", "Giao hàng trong 5 ngày."),
    ("delivery", "F", "wrong_delivery", "Giao hàng 4-6 ngày."),
    ("delivery", "F", "wrong_delivery", "Ship trong ngày là tới."),
    ("delivery", "F", "wrong_delivery", "Giao hàng sau 5 ngày làm việc."),
    ("delivery", "F", "wrong_delivery", "Hàng tới nơi trong vòng 5 ngày."),
    ("delivery", "F", "wrong_delivery", "Giao trong vòng 5 ngày."),
    ("delivery", "F", "wrong_delivery", "Bạn sẽ nhận được hàng sau 5 ngày."),
    ("delivery", "F", "wrong_delivery", "Giao hỏa tốc trong 24 giờ."),
    ("delivery", "F", "wrong_delivery", "Thời gian giao là 5 đến 7 ngày."),
    ("delivery", "F", "wrong_delivery", "Giao chậm nhất 6 ngày."),
    ("delivery", "F", "wrong_delivery", "Đơn hàng giao trong 1 ngày."),
    ("delivery", "F", "wrong_delivery", "Giao hàng trong 4 ngày làm việc."),
    ("delivery", "F", "wrong_delivery", "Ship hàng 5–7 ngày tới nơi."),
    ("delivery", "F", "wrong_delivery", "Giao trong 7 ngày."),
    ("delivery", "F", "wrong_delivery", "Nhận hàng sau 1 ngày."),
    ("delivery", "T", None, "Giao hàng trong 2-3 ngày."),
    ("delivery", "T", None, "Giao hàng 2–3 ngày."),
    ("delivery", "T", None, "Giao hàng 2—3 ngày."),
    ("delivery", "T", None, "Giao hàng 2 - 3 ngày."),
    ("delivery", "T", None, "Bạn nhận hàng trong 3 ngày."),
    ("delivery", "T", None, "Giao trong vòng 3 ngày."),
    ("delivery", "T", None, "Thời gian giao là 2 đến 3 ngày."),
    ("delivery", "T", None, "Hàng tới nơi sau 2 ngày."),
    ("delivery", "T", None, "Mộc Brew không giao trong 5 ngày; thời gian giao luôn là 2-3 ngày."),
    ("delivery", "T", None, "Hạt sau khi mở túi nên dùng trong 5-7 ngày để ngon nhất."),
    ("delivery", "T", None, "Đơn được giao trong 2 ngày làm việc."),
    # ---------------- Roast day: 12F / 9T ----------------
    ("roast", "F", "wrong_roast_day", "Mộc Brew rang vào thứ Tư hàng tuần."),
    ("roast", "F", "wrong_roast_day", "Hạt rang vào thứ Năm mỗi tuần."),
    ("roast", "F", "wrong_roast_day", "Rang vao thu tu hang tuan."),
    ("roast", "F", "wrong_roast_day", "Mộc Brew rang thứ 5 hàng tuần."),
    ("roast", "F", "wrong_roast_day", "Ngày rang là thứ Tư, đóng gói thứ Sáu."),
    ("roast", "F", "wrong_roast_day", "Lịch rang cố định của Mộc Brew rất rõ. Ngày rang đó là thứ Tư."),
    ("roast", "F", "wrong_roast_day", "Mộc Brew không rang vào thứ Ba mà rang vào thứ Năm."),
    ("roast", "F", "wrong_roast_day", "Hạt được rang vào thứ Sáu để kịp giao cuối tuần."),
    ("roast", "F", "wrong_roast_day", "Mỗi tuần mình rang vào thứ Hai và thứ Ba."),
    ("roast", "F", "wrong_roast_day", "Rang vào thứ Bảy cho đơn cuối tuần."),
    ("roast", "F", "wrong_roast_day", "Mẻ rang thứ Tư vừa rồi thơm lắm."),
    ("roast", "F", "wrong_roast_day", "Mộc Brew rang vào thứ ba và thứ năm hằng tuần."),
    ("roast", "T", None, "Hạt rang vào thứ Ba hàng tuần."),
    ("roast", "T", None, "Mộc Brew rang thứ Ba mỗi tuần."),
    ("roast", "T", None, "Rang vào thứ Ba, không phải thứ Tư."),
    ("roast", "T", None, "Vào thứ Tư, Mộc Brew không rang; ngày rang là thứ Ba."),
    ("roast", "T", None, "Thứ Sáu là ngày giao hàng; hạt rang vào thứ Ba."),
    ("roast", "T", None, "Bài viết đăng vào thứ Năm, còn hạt rang thứ Ba."),
    ("roast", "T", None, "Hạt rang thứ Ba. Đơn giao vào thứ Sáu."),
    ("roast", "T", None, "Ngày rang cố định là thứ Ba."),
    ("roast", "T", None, "Mình chỉ rang vào thứ Ba thôi."),
    # ---------------- Origin: 14F / 8T ----------------
    ("origin", "F", "wrong_origin", "Mộc Brew dùng Robusta Cầu Đất."),
    ("origin", "F", "wrong_origin", "Arabica Đắk Lắk là hạt chính của mình."),
    ("origin", "F", "wrong_origin", "Arabica Dak Lak, Robusta Cau Dat."),
    ("origin", "F", "wrong_origin", "Đắk Lắk là quê hương của hạt Arabica nhà Mộc Brew."),
    ("origin", "F", "wrong_origin", "Cầu Đất là vùng trồng Robusta của mình."),
    ("origin", "F", "wrong_origin", "Hạt Robusta trồng tại Cầu Đất."),
    ("origin", "F", "wrong_origin", "Mình lấy Arabica từ Đắk Lắk."),
    ("origin", "F", "wrong_origin", "Robusta không chỉ lấy từ Cầu Đất mà còn là hạt chính."),
    ("origin", "F", "unknown_origin", "Hạt Arabica nhập từ Ethiopia cho dòng giới hạn."),
    ("origin", "F", "wrong_origin", "Hạt Arabica của Mộc Brew. Hạt này được trồng ở Đắk Lắk."),
    ("origin", "F", "wrong_origin", "Robusta Cầu Đất phối Arabica Cầu Đất."),
    ("origin", "F", "wrong_origin", "Nguồn Robusta của mình từ Cầu Đất, Lâm Đồng."),
    ("origin", "F", "wrong_origin", "Arabica trồng ở Đắk Lắk, Robusta trồng ở Cầu Đất."),
    ("origin", "F", "wrong_origin", "Hạt Robusta nhà mình gốc Cầu Đất."),
    ("origin", "T", None, "Mộc Brew phối Arabica Cầu Đất cùng Robusta Đắk Lắk."),
    ("origin", "T", None, "Arabica trồng tại Cầu Đất, Lâm Đồng."),
    ("origin", "T", None, "Robusta Đắk Lắk là hạt chính."),
    ("origin", "T", None, "Robusta không lấy từ Cầu Đất, chỉ lấy từ Đắk Lắk."),
    ("origin", "T", None, "Không phải Robusta Cầu Đất đâu, là Robusta Đắk Lắk."),
    ("origin", "T", None, "Khác với hạt Bảo Lộc trên thị trường, Mộc Brew dùng Arabica Cầu Đất."),
    ("origin", "T", None, "Hạt của đối thủ trồng tại Bảo Lộc, còn hạt của mình là Arabica Cầu Đất."),
    ("origin", "T", None, "Mình rang Arabica Cầu Đất vào thứ Ba."),
    # ---------------- Claims: 18F / 10T / 2M ----------------
    ("claims", "F", "CLAIM", "Cà phê này chữa mất ngủ hiệu quả."),
    ("claims", "F", "CLAIM", "Đây là cà phê tốt nhất Việt Nam."),
    ("claims", "F", "CLAIM", "Ca phe chua mat ngu, tot nhat Viet Nam."),
    ("claims", "F", "CLAIM", "Cà phê tốt-nhất Việt Nam hiện nay."),
    ("claims", "F", "CLAIM", "Uống là 100 % chữa mất ngủ."),
    ("claims", "F", "CLAIM", "Không chỉ tốt nhất Việt Nam, còn đẳng cấp thế giới."),
    ("claims", "F", "CLAIM", "Khách khen đây là hạt ngon nhất họ từng uống."),
    ("claims", "F", "CLAIM", "Hạt này chữa được mất ngủ sau vài ngày."),
    ("claims", "F", "CLAIM", "Uống vào là hết mất ngủ, trị tận gốc chứng khó ngủ."),
    ("claims", "F", "CLAIM", "Cà phê ngon số một hiện nay."),
    ("claims", "F", "CLAIM", "Đứng đầu Việt Nam về cà phê rang mộc."),
    ("claims", "F", "CLAIM", "Hạt tốt. Nhất Việt Nam về độ tươi."),
    ("claims", "F", "CLAIM", "Cà phê số 1 Việt Nam."),
    ("claims", "F", "CLAIM", "Thần dược cho người mất ngủ."),
    ("claims", "F", "CLAIM", "Cam kết hết mất ngủ sau một tuần."),
    ("claims", "F", "CLAIM", "Cà phê đẳng cấp thế giới từ Cầu Đất."),
    ("claims", "F", "CLAIM", "Ngon nhất khu vực, ai uống cũng khen."),
    ("claims", "F", "CLAIM", "100% chữa mất ngủ, uống là ngủ ngon."),
    ("claims", "T", None, "Mộc Brew không phải là tốt nhất, mình chỉ rang cẩn thận."),
    ("claims", "T", None, "Mình không nhận là chữa mất ngủ."),
    ("claims", "T", None, "Chẳng phải đẳng cấp thế giới gì đâu."),
    ("claims", "T", None, "Đâu phải số 1, mình chỉ làm nhỏ lẻ."),
    ("claims", "T", None, "Cà phê không tốt nhất đâu, uống thử rồi tự cảm nhận."),
    ("claims", "T", None, "Bài này không khẳng định hạt ngon nhất, chỉ ghi giá và ngày rang."),
    ("claims", "T", None, "Đối thủ tự nhận là tốt nhất Việt Nam, Mộc Brew không bình luận."),
    ("claims", "T", None, "Khách hỏi có phải ngon nhất không, mình trả lời là không."),
    ("claims", "T", None, "Mộc Brew chưa từng nhận là số 1."),
    ("claims", "T", None, "Giá 185.000đ, rang thứ Ba, giao 2-3 ngày."),
    ("claims", "M", None, "Uống vào tỉnh táo hơn hẳn [NEEDS SOURCE]."),
    ("claims", "M", None, "Hạt này chữa mất ngủ [NEEDS SOURCE]."),
    # ---------------- Statistics: 12F / 10T / 2M ----------------
    ("stats", "F", "STAT", "Đã có 250 khách hàng thân thiết."),
    ("stats", "F", "STAT", "Hơn 185 khách hàng quay lại mỗi tháng."),
    ("stats", "F", "STAT", "1000 khách hàng đầu tiên được giảm giá."),
    ("stats", "F", "STAT", "Mỗi ngày có 5-7 khách hàng mới."),
    ("stats", "F", "STAT", "Hơn 500 khách hàng đã tin dùng."),
    ("stats", "F", "STAT", "Khoảng 300 người dùng thử mỗi tuần."),
    ("stats", "F", "STAT", "Tăng 40% doanh số trong tháng này."),
    ("stats", "F", "STAT", "Đã phục vụ 2.000 khách hàng."),
    ("stats", "F", "STAT", "Gần 900 người theo dõi trang của mình."),
    ("stats", "F", "STAT", "Hơn 60% khách quay lại mua tiếp."),
    ("stats", "F", "STAT", "Tới 350 khách hàng thân thiết rồi."),
    ("stats", "F", "STAT", "Trên 45% khách hàng hài lòng."),
    ("stats", "T", None, "Một phin pha cho 2 người uống tại văn phòng."),
    ("stats", "T", None, "Công thức này đủ cho 4 người."),
    ("stats", "T", None, "Nhóm 5 người chia nhau một gói vẫn đủ tuần."),
    ("stats", "T", None, "100% hạt Arabica nguyên chất trong gói."),
    ("stats", "T", None, "Blend 70% Robusta Đắk Lắk và 30% Arabica Cầu Đất."),
    ("stats", "T", None, "Một gói pha được nhiều ly cho cả nhà."),
    ("stats", "T", None, "Giá 185.000đ cho gói 250g."),
    ("stats", "T", None, "Gói 1kg giá 740.000đ cho quán."),
    ("stats", "T", None, "Mình rang 3 mẻ mỗi tuần vào thứ Ba."),
    ("stats", "T", None, "Hai người bạn ghé quán thử cà phê."),
    ("stats", "M", None, "Tăng 20% doanh số [NEEDS SOURCE]."),
    ("stats", "M", None, "Đã có 500 khách hàng [NEEDS SOURCE]."),
    # ---------------- Word-number statistics: 12F / 6T / 1M ----------------
    ("wordstats", "F", "unsupported_number", "Năm trăm khách hàng đã mua hạt của mình."),
    ("wordstats", "F", "unsupported_number", "Hai nghìn khách hàng tin dùng."),
    ("wordstats", "F", "unsupported_number", "Mười nghìn người theo dõi."),
    ("wordstats", "F", "unsupported_number", "Ba mươi nghìn khách hàng mỗi tháng."),
    ("wordstats", "F", "unsupported_number", "Một trăm khách hàng đầu tiên."),
    ("wordstats", "F", "unsupported_number", "Hai trăm năm mươi khách hàng thân thiết."),
    ("wordstats", "F", "unsupported_number", "Hàng trăm nghìn khách hàng đã mua."),
    ("wordstats", "F", "unsupported_number", "Cả nghìn khách hàng đang dùng."),
    ("wordstats", "F", "unsupported_number", "Hàng chục nghìn người dùng."),
    ("wordstats", "F", "unsupported_number", "Sáu mươi khách hàng mỗi ngày."),
    ("wordstats", "F", "unsupported_number", "Nghìn khách hàng tin tưởng."),
    ("wordstats", "F", "unsupported_number", "Bốn trăm khách hàng quay lại."),
    ("wordstats", "T", None, "Một khách hàng hỏi giá gói 250g."),
    ("wordstats", "T", None, "Hai người bạn ghé chơi cuối tuần."),
    ("wordstats", "T", None, "Mình có ba mẻ rang trong tuần."),
    ("wordstats", "T", None, "Năm nay mình rang thêm nhiều mẻ mới."),
    ("wordstats", "T", None, "Mẻ này có năm loại hạt phối với nhau."),
    ("wordstats", "T", None, "Khách hàng đầu tiên của mình là người quen."),
    ("wordstats", "M", None, "Năm trăm khách hàng đã mua [NEEDS SOURCE]."),
    # ---------------- Multi-fact / boundary: 8F / 3T ----------------
    ("multi", "F", "wrong_roast_day", "Gói 250g giá 185.000đ, rang vào thứ Tư, giao 2-3 ngày, hạt Arabica Cầu Đất."),
    ("multi", "F", "wrong_price", "Gói giá 200.000đ, hạt Robusta Cầu Đất, rang thứ Ba."),
    ("multi", "F", "wrong_price", "Mộc Brew rang từng mẻ nhỏ vào thứ Ba hằng tuần, hạt Arabica từ Cầu Đất và Robusta từ Đắk Lắk được phối theo tỉ lệ nhà rang, đóng gói 250g hút chân không ngay sau khi nguội, giao hàng trong 2-3 ngày cho khách ở xa, giá một gói là 210.000đ."),
    ("multi", "F", "wrong_price", "Giá một gói là 200.\n000đ."),
    ("multi", "F", "unsupported_statistic", "Giảm 50% hôm nay: giá còn 185.000đ."),
    ("multi", "F", "wrong_delivery", "Gói 250g giá 185.000đ rang thứ Ba giao 4-5 ngày."),
    ("multi", "F", "wrong_price", "Hạt Robusta Đắk Lắk giá 190.000đ một gói 250g."),
    ("multi", "F", "wrong_delivery", "Arabica Cầu Đất, Robusta Đắk Lắk, rang thứ Ba, giá 185.000đ, giao trong 5 ngày."),
    ("multi", "T", None, "185.000đ, tức 185k, hay 185 nghìn đồng — cùng một gói 250g."),
    ("multi", "T", None, "Đơn đặt ngày 21/11, giao trước 30/11 theo lịch 2-3 ngày."),
    ("multi", "T", None, "Gói 250g giá 185.000đ, rang thứ Ba, giao 2-3 ngày, Arabica Cầu Đất và Robusta Đắk Lắk."),
]


def main() -> None:
    facts, brand = load_facts(), load_brand()
    cats: dict[str, dict] = {}
    missed, caught_other, false_flags, marked_bad = [], [], [], []
    for cat, kind, expected, text in CASES:
        c = cats.setdefault(cat, {"F": 0, "caught": 0, "other": 0, "missed": 0,
                                  "T": 0, "ff": 0, "M": 0, "m_ok": 0})
        issues, flagged = check_section(text, facts, brand)
        types = {i["type"] for i in issues}
        if kind == "F":
            c["F"] += 1
            want = CLAIM_TYPES if expected == "CLAIM" else STAT_TYPES if expected == "STAT" else {expected}
            if types & want:
                c["caught"] += 1
            elif types:
                c["other"] += 1
                caught_other.append((cat, text, sorted(types)))
            else:
                c["missed"] += 1
                missed.append((cat, text))
        elif kind == "T":
            c["T"] += 1
            if issues:
                c["ff"] += 1
                false_flags.append((cat, text, sorted(types)))
        else:
            c["M"] += 1
            if not issues and flagged:
                c["m_ok"] += 1
            else:
                marked_bad.append((cat, text, sorted(types), len(flagged)))

    tot_f = sum(c["F"] for c in cats.values())
    tot_caught = sum(c["caught"] for c in cats.values())
    tot_t = sum(c["T"] for c in cats.values())
    tot_ff = sum(c["ff"] for c in cats.values())
    tot_m = sum(c["M"] for c in cats.values())
    tot_m_ok = sum(c["m_ok"] for c in cats.values())
    print(f"cases={len(CASES)} false={tot_f} true={tot_t} marked={tot_m}")
    print(f"recall(expected type) = {tot_caught}/{tot_f} = {tot_caught / tot_f:.1%}")
    print(f"false-flag rate       = {tot_ff}/{tot_t} = {tot_ff / tot_t:.1%}")
    print(f"marked handled        = {tot_m_ok}/{tot_m}")
    print(f"{'category':<10} {'recall':>14} {'FF':>8}")
    for cat, c in cats.items():
        rec = f"{c['caught']}/{c['F']}" if c["F"] else "-"
        ff = f"{c['ff']}/{c['T']}" if c["T"] else "-"
        print(f"{cat:<10} {rec:>14} {ff:>8}   other={c['other']} marked_ok={c['m_ok']}/{c['M']}")
    if missed:
        print("\nMISSED (false, no issue):")
        for cat, text in missed:
            print(f"  [{cat}] {text!r}")
    if caught_other:
        print("\nCAUGHT-OTHER:")
        for cat, text, types in caught_other:
            print(f"  [{cat}] {text!r} -> {types}")
    if false_flags:
        print("\nFALSE FLAGS (true, flagged):")
        for cat, text, types in false_flags:
            print(f"  [{cat}] {text!r} -> {types}")
    if marked_bad:
        print("\nMARKED MISHANDLED:")
        for cat, text, types, n_flagged in marked_bad:
            print(f"  [{cat}] {text!r} -> issues={types} flagged={n_flagged}")
    # Regression thresholds (review L17): exit non-zero on regression
    # so a suite runner can gate on the probe, not just eyeball it.
    ok = (tot_caught / tot_f >= 0.90) and (tot_ff / tot_t <= 0.10)
    print(f"thresholds: recall>=90%, false-flag<=10% -> {'OK' if ok else 'REGRESSED'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
