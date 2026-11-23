"""Fail-first tests for `create_corpus_profile` — the extraction half
of corpus registration (app gap #8, FEATURE_GAP_AUDIT 22 Nov 2026).

The admin's "Kho văn phong" page can register a corpus against an
existing style profile through `register_corpus`, but building a NEW
profile from pasted samples went through `save_style_profile`, which
lets the CALLER choose `store_excerpts`. Corpus profiles are under
the copyright discipline — statistics + distilled traits only, never
stored passages (the taste layer is the only store_excerpts=True
path in the system) — so the corpus path gets its own thin tool:

- excerpts are forced OFF server-side; the tool does not even take
  a store_excerpts parameter;
- at least 3 DISTINCT non-empty samples are required (duplicates
  collapse to one sample of evidence, as analyze_samples rules);
- the returned word/sample counts are the measured ones, ready to
  hand to register_corpus verbatim.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402
from content_core import loaders  # noqa: E402
from content_core.style import analyze_samples  # noqa: E402

STORE_ENVS = (
    ("CONTENT_CORE_STYLE_PROFILES_DIR", "style_profiles"),
    ("CONTENT_CORE_CORPUS_REGISTRY_DIR", "corpus_registry"),
)


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    for var, sub in STORE_ENVS:
        monkeypatch.setenv(var, str(tmp_path / sub))
    return tmp_path


SAMPLE_1 = (
    "Con sông Hậu chảy qua Châu Đốc hiền hòa quanh năm. Mùa nước nổi, "
    "nước dâng lên ngập đồng, mang theo cá linh và bông điên điển vàng. "
    "Người dân chèo xuồng ra đồng từ sớm, giăng lưới, đặt lợp, tiếng nói "
    "cười vang trên mặt nước mênh mông như biển nhỏ giữa đất liền."
)
SAMPLE_2 = (
    "Chợ nổi Cái Răng họp từ khi trời còn tối. Ghe bán khóm, ghe bán "
    "khoai, mỗi ghe treo một cây bẹo cao để người mua nhìn xa đã biết "
    "ghe bán món gì. Tiếng máy nổ, tiếng gọi nhau í ới và mùi cà phê "
    "pha phin thơm lừng quyện vào nhau trong sương sớm trên sông."
)
SAMPLE_3 = (
    "Rừng tràm Trà Sư xanh thẳm một màu bèo tấm. Xuồng ba lá lướt nhẹ "
    "dưới tán tràm, chim cò bay lên trắng cả một góc trời khi có tiếng "
    "động. Người chèo xuồng kể chuyện mùa nước, chuyện cá, chuyện những "
    "năm lụt lớn mà dân xứ này ai cũng nhớ như nhớ chuyện nhà mình."
)
SAMPLES = [SAMPLE_1, SAMPLE_2, SAMPLE_3]


def test_create_corpus_profile_builds_and_persists(stores):
    out = srv.create_corpus_profile("Văn phong sông nước thử", SAMPLES)
    assert "error" not in out, out
    assert out["sample_count"] == 3
    expected_words = analyze_samples(SAMPLES)["fingerprint"]["word_count"]
    assert out["word_count"] == expected_words

    profile = loaders.load_style_profile(out["profile_id"])
    assert profile is not None
    assert profile["id"] == out["profile_id"]
    assert profile["created_from"]["total_words"] == expected_words
    # Reachable through the normal read path too.
    fetched = srv.get_style_profile(out["profile_id"])
    assert fetched["id"] == out["profile_id"]


def test_create_corpus_profile_never_stores_excerpts(stores):
    out = srv.create_corpus_profile("Kho không lưu đoạn văn", SAMPLES)
    assert "error" not in out, out
    profile = loaders.load_style_profile(out["profile_id"])
    assert profile["exemplars"] == []
    assert profile["excerpts_note"].startswith("excerpts not stored")
    # The forcing is structural: the tool takes no store_excerpts
    # parameter a caller could flip.
    params = inspect.signature(srv.create_corpus_profile).parameters
    assert "store_excerpts" not in params


def test_create_corpus_profile_refuses_too_few_samples(stores):
    out = srv.create_corpus_profile("Kho thiếu mẫu", SAMPLES[:2])
    assert "error" in out
    assert "3" in out["error"]
    assert loaders.list_style_profiles() == []


def test_create_corpus_profile_counts_duplicates_as_one(stores):
    out = srv.create_corpus_profile(
        "Kho trùng mẫu", [SAMPLE_1, SAMPLE_1, SAMPLE_1])
    assert "error" in out
    assert loaders.list_style_profiles() == []


def test_create_corpus_profile_refuses_empty_samples(stores):
    assert "error" in srv.create_corpus_profile("Kho rỗng", [])
    assert "error" in srv.create_corpus_profile(
        "Kho toàn khoảng trắng", ["", "  ", "\n"])
    assert loaders.list_style_profiles() == []


def test_created_profile_registers_as_corpus(stores):
    out = srv.create_corpus_profile("Văn phong sông nước thử", SAMPLES)
    assert "error" not in out, out
    reg = srv.register_corpus(
        "kho-thu-nghiem", version=1, title="Kho Thử Nghiệm",
        kind="genre", profile_id=out["profile_id"],
        legitimacy_basis="Bài mẫu tự viết cho kiểm thử",
        word_count=out["word_count"], sample_count=out["sample_count"])
    assert "error" not in reg, reg
    entry = srv.get_corpus("kho-thu-nghiem")
    assert entry["profile_id"] == out["profile_id"]
    assert entry["word_count"] == out["word_count"]
    assert entry["sample_count"] == 3
    listed = srv.list_corpora()
    assert [c["id"] for c in listed["corpora"]] == ["kho-thu-nghiem"]
