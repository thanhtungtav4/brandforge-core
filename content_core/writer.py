"""Writer interface + deterministic MockWriter.

A real LLM writer plugs in by implementing the same two methods and either
passing it to build_graph(writer=...) or setting CONTENT_WRITER to a
module:Class path (see get_writer). No API keys are needed for MockWriter.
"""
from __future__ import annotations

import importlib
import math
import os
from typing import Any, Protocol


class Writer(Protocol):
    def write_section(self, section: dict, ctx: dict[str, Any]) -> str: ...
    def rewrite_section(self, section: dict, ctx: dict[str, Any], issues: list[dict]) -> str: ...


# Every pool sentence is 12-13 words by construction, so the median stays
# inside the brand target (11-14) no matter how many are cycled.
POOL = [
    "Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn.",
    "Một gói 250g có giá 185.000đ trước khi nói tới hương vị.",
    "Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ.",
    "Hạt Robusta Đắk Lắk cho vị đậm và lớp crema dày hơn.",
    "Bạn pha phin chậm rãi vào buổi sáng trước giờ làm việc.",
    "Bạn dùng V60 khi muốn ly cà phê sáng và nhẹ nhàng hơn.",
    "Mình ghi rõ ngày rang trên từng gói gửi tới tay bạn.",
    "Đơn hàng được giao trong 2-3 ngày sau khi bạn xác nhận.",
    "Mẻ rang nhỏ giúp mình kiểm tra từng mẻ trước khi đóng gói.",
    "Bạn nên dùng nước vừa sôi và chờ một phút trước khi rót.",
    "Cà phê mới rang cần nghỉ vài ngày trước khi pha uống.",
    "Mình không trộn hạt cũ vào mẻ mới để giữ vị ổn định.",
    "Văn phòng nhỏ chỉ cần phin và ấm nước là pha được.",
    "Bạn bảo quản hạt trong hộp kín và tránh ánh nắng trực tiếp.",
    "Mỗi mẻ rang đều được nếm thử trước khi giao cho khách hàng.",
    "Mình trả lời email trong ngày làm việc khi bạn cần hỗ trợ.",
]

NEEDS_SOURCE_SENTENCE = "Nhiều người nói hạt nhập khẩu luôn thơm hơn hạt Việt [NEEDS SOURCE]."

SOCIAL_TEXT = (
    "Hạt mới rang sáng nay bạn nhé. "
    "Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn. "
    "Một gói 250g giá 185.000đ. "
    "Bạn pha phin tại bàn làm việc vẫn gọn và nhanh."
)

EMAIL_TEXTS = {
    "greeting": "Chào bạn, mình là Linh từ Mộc Brew đây. Đơn hàng của bạn đã được xác nhận sáng nay rồi.",
    "update": "Đơn hàng được giao trong 2-3 ngày sau khi bạn xác nhận. Mình ghi rõ ngày rang trên từng gói gửi tới tay bạn.",
    "next-step": "Bạn kiểm tra hộp thư giúp mình khi shipper gọi tới nhé. Mình trả lời email trong ngày làm việc khi bạn cần hỗ trợ.",
}

BAD_TEXT = (
    "Mộc Brew mang đến trải nghiệm cà phê đẳng cấp thế giới cho mọi khách hàng yêu cà phê thật sự! "
    "Sản phẩm được curated kỹ lưỡng để elevate buổi sáng và leverage năng lượng ngày mới của bạn! "
    "Giá chỉ 200.000đ cho một gói nhỏ xinh hôm nay!"
)


class MockWriter:
    """Deterministic writer for tests/golden set. writer_mode='bad' writes a
    deliberately violating draft; rewrite_section always returns clean text,
    modelling an LLM that fixes only what the checkers flagged."""

    name = "mock"

    def _good_text(self, section: dict, ctx: dict[str, Any]) -> str:
        fmt = ctx["brief"].get("format", "blog")
        intent = section.get("intent", "")
        if fmt == "social":
            return SOCIAL_TEXT
        if fmt == "email":
            return EMAIL_TEXTS.get(intent, EMAIL_TEXTS["update"])
        target = int(section.get("target_words", 120))
        n = max(3, math.ceil(target / 12.5))
        # Deterministic rotation: s1 starts at POOL[0], s2 at POOL[1], ... so
        # fact sentences (price, roast day, delivery) appear across the draft.
        sid = section.get("id", "s1")
        offset = (int(sid[1:]) - 1) % len(POOL) if sid[1:].isdigit() else 0
        sentences = [POOL[(offset + i) % len(POOL)] for i in range(n)]
        if intent == "comparison-claims":
            sentences.insert(1, NEEDS_SOURCE_SENTENCE)
        return " ".join(sentences)

    def write_section(self, section: dict, ctx: dict[str, Any]) -> str:
        if ctx["brief"].get("writer_mode") == "bad":
            return BAD_TEXT
        return self._good_text(section, ctx)

    def rewrite_section(self, section: dict, ctx: dict[str, Any], issues: list[dict]) -> str:
        return self._good_text(section, ctx)


def get_writer(name: str | None = None) -> Writer:
    """CONTENT_WRITER=mock (default) or CONTENT_WRITER=module:ClassName for a real LLM writer."""
    name = name or os.environ.get("CONTENT_WRITER", "mock")
    if name == "mock":
        return MockWriter()
    module_name, _, class_name = name.partition(":")
    if not class_name:
        raise ValueError("CONTENT_WRITER must be 'mock' or 'module:ClassName'")
    cls = getattr(importlib.import_module(module_name), class_name)
    return cls()
