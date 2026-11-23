"""Fail-first tests for backup RESTORE (app gap #7, 23 Nov 2026):
restore_core_data brings files back from an export_core_data archive
into the live stores — but only under the restore discipline:

- the archive must live in the backups dir and carry a readable
  manifest; a corrupt zip or a missing manifest is refused whole;
- ANY traversal/absolute entry path (in the zip namelist or the
  manifest) refuses the whole restore before a single byte is
  written;
- a credential-pattern entry inside a forged archive is refused and
  named, never landed;
- an entry whose bytes do not match the manifest sha256, or whose
  JSON does not parse, is refused and named, never landed corrupt;
- missing live file -> restored; identical -> skipped; differing ->
  only with confirm=True, else skipped and reported;
- dry_run=True reports the full plan and writes NOTHING (and makes
  no pre-restore backup);
- before any real write the CURRENT live stores are exported as a
  pre-restore-* archive, so a restore is itself reversible; if that
  pre-backup fails the restore aborts before writing.

All tests run against temp store roots (every CONTENT_CORE_*_DIR
pointed at tmp_path) — a real restore against the live stores is
forbidden; the only live paths an export can carry are the shipped
data/ files, which are byte-identical to the live ones and therefore
only ever skipped in these tests.
"""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402

STORE_ENVS = (
    ("CONTENT_CORE_DRAFTS_DIR", "drafts"),
    ("CONTENT_CORE_BRAND_DRAFTS_DIR", "brand_drafts"),
    ("CONTENT_CORE_FACTS_PROFILES_DIR", "facts"),
    ("CONTENT_CORE_STYLE_PROFILES_DIR", "style_profiles"),
    ("CONTENT_CORE_QUALITY_PROFILES_DIR", "quality_profiles"),
    ("CONTENT_CORE_IMAGE_LIBRARY_DIR", "image_library"),
    ("CONTENT_CORE_PERSONAS_DIR", "personas"),
    ("CONTENT_CORE_RESEARCH_PACKS_DIR", "research_packs"),
    ("CONTENT_CORE_CORPUS_REGISTRY_DIR", "corpus_registry"),
    ("CONTENT_CORE_CORPUS_CHOICES_DIR", "corpus_choices"),
    ("CONTENT_CORE_TASTE_DIR", "taste"),
    ("CONTENT_CORE_WP_TARGETS_DIR", "wordpress_targets"),
    ("CONTENT_CORE_BRAND_SETUPS_DIR", "brand_setups"),
    ("CONTENT_CORE_BACKUPS_DIR", "backups"),
)


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    for var, sub in STORE_ENVS:
        monkeypatch.setenv(var, str(tmp_path / sub))
    pdir = tmp_path / "personas"
    pdir.mkdir(parents=True, exist_ok=True)
    for name in ("persona_linh.json", "persona_an.json"):
        (pdir / name).write_text(
            (PROJECT_DIR / "data" / name).read_text(encoding="utf-8"),
            encoding="utf-8")
    return tmp_path


ARTICLE_1 = ("Bếp nhà Châu Đốc buổi sớm thơm mùi mắm chưng. " * 6
             + "Nồi đất đặt trên bếp củi, hơi bốc lên nghi ngút. " * 6)
ARTICLE_2 = ("Con nước nổi về mang theo cá linh đầy đồng. " * 8
             + "Người xứ này làm mắm để giữ cá lại cho mùa khô. " * 8)
ARTICLE_3 = ("Thúng cá lóc đồng phơi nắng trên sân gạch tàu. " * 8
             + "Mùi khô cá quyện mùi rơm mới gặt ngoài đồng. " * 8)


def _seed(stores):
    """Seed two temp-store files and return {arcname: live Path}."""
    srv.save_facts("brand-x", {"facts": {
        "price_vnd_per_250g": 50000, "pack_weight_g": 250,
        "origins": ["Bến Tre"]}})
    srv.save_style_profile("Giọng Khôi Phục",
                           [ARTICLE_1, ARTICLE_2, ARTICLE_3])
    facts = Path(stores / "facts" / "brand-x.json")
    styles = sorted(Path(stores / "style_profiles").glob("*.json"))
    assert facts.exists() and len(styles) == 1
    return {"facts_profiles/brand-x.json": facts,
            f"style_profiles/{styles[0].name}": styles[0]}


def _forge(stores, entries: dict[str, bytes], manifest_files=None,
           name="core-data-19990101-000000.zip"):
    """Write a hand-made archive into the temp backups dir. The
    manifest's sha256 values are computed over the given bytes (a
    forger can do that too — refusals must come from the path/name/
    content rules, not from trusting the manifest)."""
    bdir = Path(stores / "backups")
    bdir.mkdir(parents=True, exist_ok=True)
    files = manifest_files
    if files is None:
        files = [{"path": p, "size": len(b),
                  "sha256": hashlib.sha256(b).hexdigest()}
                 for p, b in entries.items()]
    manifest = {"archive": name, "created_at": "1999-01-01T00:00:00+00:00",
                "stores": {}, "files": files, "excluded": [],
                "total_files": len(files),
                "total_bytes": sum(f["size"] for f in files)}
    with zipfile.ZipFile(bdir / name, "w") as zf:
        for p, b in entries.items():
            zf.writestr(p, b)
        zf.writestr("manifest.json",
                    json.dumps(manifest, ensure_ascii=False))
    return name


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------

def test_restore_round_trip_byte_identical(stores):
    seeded = _seed(stores)
    before = {arc: p.read_bytes() for arc, p in seeded.items()}
    out = srv.export_core_data()
    assert "error" not in out
    # Mutate one file, delete the other.
    seeded["facts_profiles/brand-x.json"].write_bytes(b'{"facts": {}}')
    style_arc = [k for k in seeded if k.startswith("style_profiles/")][0]
    seeded[style_arc].unlink()
    rep = srv.restore_core_data(out["archive"], confirm=True)
    assert "error" not in rep, rep
    for arc, path in seeded.items():
        assert path.read_bytes() == before[arc], arc
        assert arc in rep["restored"], rep
    # A pre-restore backup was taken BEFORE the writes and is listed.
    assert rep["pre_restore_backup"]
    assert rep["pre_restore_backup"].startswith("pre-restore-")
    assert (Path(stores / "backups") / rep["pre_restore_backup"]).exists()
    listed = {b["name"] for b in srv.list_backups()["backups"]}
    assert rep["pre_restore_backup"] in listed
    assert rep["archive"] == out["archive"]
    assert rep["dry_run"] is False


def test_restore_identical_skips_everything_and_takes_no_backup(stores):
    _seed(stores)
    out = srv.export_core_data()
    rep = srv.restore_core_data(out["archive"], confirm=True)
    assert "error" not in rep, rep
    assert rep["restored"] == []
    assert rep["pre_restore_backup"] is None
    seeded_arcs = {"facts_profiles/brand-x.json"}
    assert seeded_arcs <= set(rep["skipped_identical"])
    # No pre-restore archive appeared in the backups dir.
    names = [p.name for p in Path(stores / "backups").glob("*.zip")]
    assert names == [out["archive"]]


def test_restore_differing_without_confirm_writes_nothing(stores):
    seeded = _seed(stores)
    out = srv.export_core_data()
    facts = seeded["facts_profiles/brand-x.json"]
    original = facts.read_bytes()
    facts.write_bytes(b'{"facts": {"price_vnd_per_250g": 1}}')
    rep = srv.restore_core_data(out["archive"])  # confirm defaults False
    assert "error" not in rep, rep
    assert facts.read_bytes() == b'{"facts": {"price_vnd_per_250g": 1}}'
    assert "facts_profiles/brand-x.json" in rep["skipped_needs_confirm"]
    assert "facts_profiles/brand-x.json" not in rep["restored"]
    assert rep["pre_restore_backup"] is None  # nothing written at all
    assert original != facts.read_bytes()


def test_restore_missing_file_needs_no_confirm(stores):
    seeded = _seed(stores)
    out = srv.export_core_data()
    style_arc = [k for k in seeded if k.startswith("style_profiles/")][0]
    seeded[style_arc].unlink()
    rep = srv.restore_core_data(out["archive"])  # no confirm
    assert "error" not in rep, rep
    assert style_arc in rep["restored"]
    assert seeded[style_arc].exists()
    # A write happened, so a pre-restore backup must exist.
    assert rep["pre_restore_backup"]


def test_restore_dry_run_writes_nothing(stores):
    seeded = _seed(stores)
    out = srv.export_core_data()
    facts = seeded["facts_profiles/brand-x.json"]
    facts.write_bytes(b'{"facts": {}}')
    style_arc = [k for k in seeded if k.startswith("style_profiles/")][0]
    seeded[style_arc].unlink()
    backups_before = sorted(p.name for p in
                            Path(stores / "backups").glob("*.zip"))
    rep = srv.restore_core_data(out["archive"], confirm=True, dry_run=True)
    assert "error" not in rep, rep
    assert rep["dry_run"] is True
    assert rep["pre_restore_backup"] is None
    assert "facts_profiles/brand-x.json" in rep["restored"]
    assert style_arc in rep["restored"]
    # Disk untouched: mutation still there, deletion still deleted,
    # and no pre-restore archive was created.
    assert facts.read_bytes() == b'{"facts": {}}'
    assert not seeded[style_arc].exists()
    assert sorted(p.name for p in
                  Path(stores / "backups").glob("*.zip")) == backups_before


def test_pre_restore_backup_itself_restores(stores):
    seeded = _seed(stores)
    facts = seeded["facts_profiles/brand-x.json"]
    out = srv.export_core_data()
    exported_bytes = facts.read_bytes()
    facts.write_bytes(b'{"facts": {"stage": "mutated-1"}}')
    rep = srv.restore_core_data(out["archive"], confirm=True)
    assert facts.read_bytes() == exported_bytes
    pre = rep["pre_restore_backup"]
    # Mutate again, then restore FROM the pre-restore archive: the
    # file must come back to the mutated-1 state the pre-backup saw.
    facts.write_bytes(b'{"facts": {"stage": "mutated-2"}}')
    rep2 = srv.restore_core_data(pre, confirm=True)
    assert "error" not in rep2, rep2
    assert facts.read_bytes() == b'{"facts": {"stage": "mutated-1"}}'


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

def test_restore_missing_archive_is_an_error(stores):
    rep = srv.restore_core_data("core-data-19990101-000000.zip")
    assert "error" in rep
    rep = srv.restore_core_data("../outside.zip")
    assert "error" in rep


def test_restore_corrupt_zip_is_an_error(stores):
    bdir = Path(stores / "backups")
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "core-data-19990101-000000.zip").write_bytes(b"not a zip")
    rep = srv.restore_core_data("core-data-19990101-000000.zip")
    assert "error" in rep


def test_restore_zip_without_manifest_is_an_error(stores):
    bdir = Path(stores / "backups")
    bdir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bdir / "core-data-19990101-000000.zip", "w") as zf:
        zf.writestr("facts_profiles/brand-x.json", b"{}")
    rep = srv.restore_core_data("core-data-19990101-000000.zip")
    assert "error" in rep


def test_restore_traversal_entry_refuses_whole_restore(stores):
    seeded = _seed(stores)
    facts = seeded["facts_profiles/brand-x.json"]
    good = facts.read_bytes()
    name = _forge(stores, {
        "facts_profiles/brand-x.json": b'{"facts": {"forged": true}}',
        "drafts/../../evil.json": b'{"evil": true}',
    })
    rep = srv.restore_core_data(name, confirm=True)
    assert "error" in rep, rep
    # Nothing was written: the good entry did not land either, and no
    # file escaped the store roots.
    assert facts.read_bytes() == good
    assert not (Path(stores) / "evil.json").exists()
    assert not (Path(stores).parent / "evil.json").exists()


def test_restore_absolute_entry_refuses_whole_restore(stores):
    _seed(stores)
    name = _forge(stores, {"/tmp/evil-abs.json": b"{}"})
    rep = srv.restore_core_data(name, confirm=True)
    assert "error" in rep, rep
    assert not Path("/tmp/evil-abs.json").exists()


def test_restore_credential_entry_refused_and_named(stores):
    seeded = _seed(stores)
    facts = seeded["facts_profiles/brand-x.json"]
    forged_facts = b'{"facts": {"price_vnd_per_250g": 99999}}'
    name = _forge(stores, {
        "facts_profiles/brand-x.json": forged_facts,
        "drafts/credentials.json": b'{"user": "x", "pass": "y"}',
    })
    rep = srv.restore_core_data(name, confirm=True)
    assert "error" not in rep, rep
    refused = {r["path"] for r in rep["refused"]}
    assert "drafts/credentials.json" in refused
    assert not (Path(stores / "drafts") / "credentials.json").exists()
    # The legitimate entry in the same archive still restored.
    assert facts.read_bytes() == forged_facts
    assert "facts_profiles/brand-x.json" in rep["restored"]


def test_restore_invalid_json_refused_and_named(stores):
    seeded = _seed(stores)
    facts = seeded["facts_profiles/brand-x.json"]
    good = facts.read_bytes()
    name = _forge(stores, {
        "facts_profiles/brand-x.json": b"this is not json{"})
    rep = srv.restore_core_data(name, confirm=True)
    assert "error" not in rep, rep
    refused = {r["path"]: r["reason"] for r in rep["refused"]}
    assert "facts_profiles/brand-x.json" in refused
    assert facts.read_bytes() == good  # never landed corrupt


def test_restore_hash_mismatch_refused_and_named(stores):
    seeded = _seed(stores)
    facts = seeded["facts_profiles/brand-x.json"]
    good = facts.read_bytes()
    payload = b'{"facts": {"tampered": true}}'
    files = [{"path": "facts_profiles/brand-x.json", "size": len(payload),
              "sha256": hashlib.sha256(b"different bytes").hexdigest()}]
    name = _forge(stores, {"facts_profiles/brand-x.json": payload},
                  manifest_files=files)
    rep = srv.restore_core_data(name, confirm=True)
    assert "error" not in rep, rep
    refused = {r["path"] for r in rep["refused"]}
    assert "facts_profiles/brand-x.json" in refused
    assert facts.read_bytes() == good


def test_restore_unknown_store_entry_refused(stores):
    _seed(stores)
    name = _forge(stores, {"mystery_store/x.json": b"{}"})
    rep = srv.restore_core_data(name, confirm=True)
    assert "error" not in rep, rep
    refused = {r["path"] for r in rep["refused"]}
    assert "mystery_store/x.json" in refused
