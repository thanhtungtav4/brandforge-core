"""Block-extraction fidelity tests for the WP edit-feedback seam
(code review round 3, Phase 3 — awesome-test-writing).

Seam under test (the public functions the sync actually calls):
wordpress.html_to_blocks / sections_to_blocks / diff_blocks. The
existing round-trip test (test_quality_uplift.py) pins exporter HTML
only; this file pins what happens with EDITOR-shaped HTML — the
markup a human editing in the WordPress block editor actually
produces — because that is the side of the comparison that decides
whether the owner's edits are seen, and what the taste layer learns.

Two groups:
- Contract tests: behaviour the module promises today and delivers
  (measured at HEAD 1931076 before writing them). Each was flip-
  proven: a named mutation to wordpress.py turns it red.
- M1 tests: review round 3 finding M1 — list/table text was
  silently dropped, <br> glued words, unclosed/misnested blocks
  merged or lost text. They assert the CORRECT contract; they were
  written as strict-xfail while the defect stood and un-marked the
  day the fix landed (M1 fix round, 2026-11-22).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from content_core import wordpress  # noqa: E402


# ---------------------------------------------------------------------------
# Contract — what the extractor promises and delivers today
# ---------------------------------------------------------------------------

def test_gutenberg_comments_and_inline_markup_contribute_text_only():
    html = ("<!-- wp:paragraph --><p>Đoạn một có <strong>chữ đậm</strong> "
            "và <a href='https://x.vn'>liên kết</a>.</p>"
            "<!-- /wp:paragraph --><!-- wp:heading -->"
            "<h2>Tiêu đề</h2><!-- /wp:heading -->")
    assert wordpress.html_to_blocks(html) == [
        "Đoạn một có chữ đậm và liên kết.", "Tiêu đề"]


def test_figure_and_caption_never_enter_blocks():
    """Documented intent: images/captions are placed from the media
    manifest, they are not draft text."""
    html = ("<p>Ảnh minh họa:</p><figure class='wp-block-image'>"
            "<img src='a.jpg'/><figcaption>Hủ mắm 500g</figcaption>"
            "</figure><p>Tiếp.</p>")
    assert wordpress.html_to_blocks(html) == ["Ảnh minh họa:", "Tiếp."]


def test_skipped_subtree_text_never_pollutes_an_open_block():
    """The invariant the skip set exists for: text inside a skipped
    subtree (figure content here) must not leak into the block that
    surrounds it. Flip-proven by emptying _SKIP_TAGS."""
    html = ("<p>outer<figure><p>inner</p></figure>tail</p>")
    blocks = wordpress.html_to_blocks(html)
    assert all("inner" not in b for b in blocks)


def test_script_and_style_subtrees_are_skipped():
    html = ("<p>Trước</p><script>var x = 'chữ trong script';</script>"
            "<style>.a { color: red; }</style><p>Sau</p>")
    assert wordpress.html_to_blocks(html) == ["Trước", "Sau"]


def test_entities_unescaped_and_whitespace_collapsed():
    html = "<p>Mắm &amp; khô &lt;ngon&gt; &#8212;  nhiều   khoảng\n trắng</p>"
    assert wordpress.html_to_blocks(html) == [
        "Mắm & khô <ngon> — nhiều khoảng trắng"]


def test_empty_blocks_are_dropped():
    html = "<p>   </p><p>Thật</p><h3></h3>"
    assert wordpress.html_to_blocks(html) == ["Thật"]


def test_all_heading_levels_become_blocks():
    assert wordpress.html_to_blocks(
        "<h1>A</h1><h4>B</h4><h6>C</h6>") == ["A", "B", "C"]


def test_div_wrappers_are_transparent():
    html = "<div class='entry-content'><p>Trong div</p></div>"
    assert wordpress.html_to_blocks(html) == ["Trong div"]


def test_blockquote_paragraphs_become_blocks():
    html = ("<p>Trước.</p><blockquote><p>Trích dẫn quan trọng</p>"
            "</blockquote><p>Sau.</p>")
    assert wordpress.html_to_blocks(html) == [
        "Trước.", "Trích dẫn quan trọng", "Sau."]


def test_nested_inline_markup_flattens_to_text():
    html = "<p>Một <em>hai <strong>ba</strong></em> bốn</p>"
    assert wordpress.html_to_blocks(html) == ["Một hai ba bốn"]


def test_roundtrip_richer_sections_and_identity_diff():
    sections = [
        {"id": "s1", "heading": "Mở đầu",
         "text": "Đoạn một dòng một.\n\nĐoạn hai vẫn giữ nguyên văn."},
        {"id": "s2", "heading": "Giá",
         "text": "Gói 250g giá 89.000đ, gói 500g giá 179.000đ."},
    ]
    html = wordpress.build_content_html(sections, {})
    blocks = wordpress.html_to_blocks(html)
    assert blocks == wordpress.sections_to_blocks(sections)
    diff = wordpress.diff_blocks(blocks, blocks)
    assert diff == {"blocks_changed": 0, "blocks_added": 0,
                    "blocks_removed": 0, "words_added": 0,
                    "words_removed": 0}


# ---------------------------------------------------------------------------
# M1 (review round 3) — the correct contract, fixed 2026-11-22
# ---------------------------------------------------------------------------

def test_list_items_are_preserved_as_blocks():
    html = ("<p>Giới thiệu:</p><ul><li>Khô cá lóc</li>"
            "<li>Tôm khô</li></ul><p>Kết.</p>")
    blocks = wordpress.html_to_blocks(html)
    assert "Khô cá lóc" in blocks and "Tôm khô" in blocks


def test_table_cells_are_preserved():
    html = ("<p>Bảng giá:</p><table><tr><td>250g</td>"
            "<td>100.000đ</td></tr></table>")
    blocks = wordpress.html_to_blocks(html)
    assert any("100.000đ" in b for b in blocks)


def test_br_separates_words():
    assert wordpress.html_to_blocks(
        "<p>Dòng một<br>Dòng hai</p>") == ["Dòng một Dòng hai"]


def test_unclosed_paragraph_does_not_merge_with_heading():
    html = "<p>Đoạn chưa đóng<h2>Heading</h2><p>Đoạn hai</p>"
    assert wordpress.html_to_blocks(html) == [
        "Đoạn chưa đóng", "Heading", "Đoạn hai"]


def test_figure_inside_paragraph_keeps_tail_text():
    html = "<p>outer<figure><p>inner</p></figure>tail</p>"
    blocks = wordpress.html_to_blocks(html)
    assert any("tail" in b for b in blocks)
