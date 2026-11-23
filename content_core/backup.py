"""Local data backup — one timestamped archive of every local store.

The stores hold the accumulated client asset base (brand drafts,
facts, style/quality profiles, research packs, SEO profiles,
corpus registry and choices, taste verdicts, image libraries,
drafts, personas). Until
this module they had NO backup path: everything lived as loose
files in data/ and drafts/. export_core_data snapshots them into a
single zip under the backups dir with a manifest (per-store counts,
per-file sha256) so an archive can be verified without trusting it,
and restore_core_data brings files back from such an archive under
a validate -> plan -> snapshot-the-present -> write discipline
(see the restore section below).

Secrets discipline: the stores contain no credentials BY DESIGN
(credentials live in the Secure Vault, never in brand files or
stores). As a belt-and-braces rule the exporter still EXCLUDES any
file whose name matches a credential pattern (dotfiles like .env,
*credential*, *secret*, *token*, key material) and records every
exclusion in the manifest — a planted secret is reported, never
silently archived and never silently dropped.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import loaders

PROJECT_DIR = Path(__file__).resolve().parent.parent

_SECRET_NAME_RE = re.compile(r"credential|secret|token", re.IGNORECASE)
_SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
_TRANSIENT_SUFFIXES = {".lock", ".tmp"}


def backups_dir() -> Path:
    override = os.environ.get("CONTENT_CORE_BACKUPS_DIR")
    return Path(override) if override else PROJECT_DIR / "backups"


def _drafts_dir() -> Path:
    # Mirrors mcp_server.drafts_dir's rule (env override, else the
    # project drafts/ dir) — duplicated deliberately: backup must not
    # import the server module.
    return Path(os.environ.get("CONTENT_CORE_DRAFTS_DIR",
                               PROJECT_DIR / "drafts"))


def _store_sources() -> list[tuple[str, Path, bool]]:
    """(store name, directory or file, recursive) for every local
    store the core accumulates data in."""
    data = loaders.DATA_DIR
    return [
        ("personas", loaders.personas_dir(), False),
        ("brand_files", data, False),          # brand_*.yaml (globbed)
        ("core_data_files", data, False),      # shipped JSON (globbed)
        ("brand_drafts", loaders.brand_drafts_dir(), False),
        ("facts_profiles", loaders.facts_profiles_dir(), False),
        ("style_profiles", loaders.style_profiles_dir(), False),
        ("quality_profiles", loaders.quality_profiles_dir(), False),
        ("image_library", loaders.image_library_dir(), True),
        ("research_packs", loaders.research_packs_dir(), False),
        ("seo_profiles", loaders.seo_profiles_dir(), False),
        ("corpus_registry", loaders.corpus_registry_dir(), False),
        ("corpus_choices", loaders.corpus_choices_dir(), False),
        ("wordpress_targets", loaders.wordpress_targets_dir(), False),
        ("brand_setups", loaders.brand_setups_dir(), False),
        ("taste", loaders.taste_dir(), False),
        ("drafts", _drafts_dir(), False),
    ]


def _all_files(root: Path, recursive: bool) -> list[Path]:
    if not root.exists():
        return []
    pattern = "**/*" if recursive else "*"
    return sorted(p for p in root.glob(pattern) if p.is_file())


def _is_data_file(name: str, path: Path) -> bool:
    """Whether `path` is one of the store's data files (the files
    the backup exists to preserve). Everything else in a store dir
    is either excluded-with-a-record (credential patterns, transient
    lock/temp files) or simply not store data and ignored."""
    if name == "personas":
        return path.name.startswith("persona_") and path.suffix == ".json"
    if name == "brand_files":
        return path.name.startswith("brand_") and path.suffix == ".yaml"
    if name == "core_data_files":
        return path.name in {"facts_lorebook.json", "craft_library.json",
                             "exemplars.json", "domain_glossary.json"}
    return path.suffix == ".json"


def _exclusion_reason(path: Path) -> str | None:
    name = path.name
    if name.startswith("."):
        return "dotfile (possible env/secret file)"
    if _SECRET_NAME_RE.search(name):
        return "credential-pattern filename"
    if path.suffix.lower() in _SECRET_SUFFIXES:
        return "key material suffix"
    if path.suffix.lower() in _TRANSIENT_SUFFIXES:
        return "transient lock/temp file, not data"
    return None


def export_core_data() -> dict:
    """Snapshot every local store into one timestamped zip in the
    backups dir. Returns {archive, total_files, total_bytes, stores,
    excluded} — the full manifest lives inside the archive as
    manifest.json."""
    return _export_archive("core-data")


def _export_archive(prefix: str) -> dict:
    """The export machinery, parameterised by archive-name prefix:
    `core-data` for a user-requested snapshot, `pre-restore` for the
    automatic safety snapshot restore_core_data takes of the CURRENT
    live stores before it writes anything (so a restore is itself
    reversible)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if prefix != "core-data":
        stamp += datetime.now(timezone.utc).strftime("-%f")[:4]
    out_dir = backups_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"{prefix}-{stamp}.zip"
    archive_path = out_dir / archive_name
    files: list[dict] = []
    excluded: list[dict] = []
    stores: dict[str, dict] = {}
    payload: list[tuple[str, bytes]] = []
    seen: set[Path] = set()
    for name, root, recursive in _store_sources():
        count, total = 0, 0
        for path in _all_files(root, recursive):
            if path in seen:
                continue  # already classified by a store sharing the dir
            arcname = f"{name}/{path.relative_to(root).as_posix()}"
            reason = _exclusion_reason(path)
            if reason is not None:
                seen.add(path)
                excluded.append({"path": arcname, "reason": reason})
                continue
            if not _is_data_file(name, path):
                continue  # may be a sibling store's data — leave unclaimed
            seen.add(path)
            data = path.read_bytes()
            payload.append((arcname, data))
            files.append({"path": arcname, "size": len(data),
                          "sha256": hashlib.sha256(data).hexdigest()})
            count += 1
            total += len(data)
        stores[name] = {"files": count, "bytes": total}
    manifest = {"archive": archive_name,
                "created_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"),
                "stores": stores, "files": files, "excluded": excluded,
                "total_files": len(files),
                "total_bytes": sum(f["size"] for f in files)}
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, data in payload:
            zf.writestr(arcname, data)
        zf.writestr("manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2))
    return {"archive": archive_name, "total_files": manifest["total_files"],
            "total_bytes": manifest["total_bytes"], "stores": stores,
            "excluded": excluded}


def list_backups() -> list[dict]:
    """Every archive in the backups dir, newest first, with the
    manifest summary read back from inside the zip (an archive whose
    manifest cannot be read is listed as corrupt, never skipped).
    Both archive families are listed: `core-data-*` snapshots and
    the `pre-restore-*` safety snapshots restore takes of the live
    stores before writing."""
    d = backups_dir()
    if not d.exists():
        return []
    paths = [p for pattern in ("core-data-*.zip", "pre-restore-*.zip")
             for p in d.glob(pattern)]
    paths.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    out = []
    for path in paths:
        entry = {"name": path.name, "size": path.stat().st_size}
        try:
            with zipfile.ZipFile(path) as zf:
                manifest = json.loads(zf.read("manifest.json"))
            entry.update({"created_at": manifest.get("created_at"),
                          "total_files": manifest.get("total_files"),
                          "total_bytes": manifest.get("total_bytes"),
                          "corrupt": False})
        except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, OSError):
            entry["corrupt"] = True
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Restore — the return path. An archive is untrusted input: it is
# validated before anything is planned, planned before anything is
# written, and the live stores are snapshotted (pre-restore-*) before
# the first write lands, so a restore is itself reversible.
# ---------------------------------------------------------------------------

def _unsafe_arcname(name: str) -> bool:
    """An archive entry path that could land outside its store root:
    absolute, drive-qualified, or containing a `..` component."""
    if not name or name.startswith(("/", "\\")):
        return True
    parts = name.replace("\\", "/").split("/")
    return parts[0].endswith(":") or any(part == ".." for part in parts)


def _store_roots() -> dict[str, Path]:
    """Store name -> the live directory its archived files return to
    (the export sources read in reverse)."""
    roots: dict[str, Path] = {}
    for name, root, _recursive in _store_sources():
        roots.setdefault(name, root)
    return roots


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes atomically (temp file in the SAME directory,
    fsync, os.replace, fsync the directory) — the loaders
    atomic_write_text discipline, for raw archive bytes."""
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                    prefix=path.name + ".",
                                    suffix=".tmp")
    try:
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            mode = 0o644
        os.chmod(tmp_name, mode)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        loaders._fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def restore_core_data(archive_name: str, confirm: bool = False,
                      dry_run: bool = False) -> dict:
    """Restore files from a backup archive into the live stores.

    Semantics per archived file: missing live file -> restored;
    identical -> skipped; differing -> restored only with
    confirm=True, otherwise skipped and reported. dry_run=True
    returns the full plan and writes NOTHING (no pre-restore backup
    either). Before any real write, the current live stores are
    exported as a pre-restore-* archive; if that snapshot fails the
    restore aborts before writing.

    The archive is validated before planning: it must be a bare
    filename inside the backups dir, a readable zip, and carry a
    manifest; ANY traversal/absolute entry path (zip namelist or
    manifest) refuses the whole restore. Per entry: a credential-
    pattern name, a byte/sha256 mismatch against the manifest, or
    JSON that does not parse is refused and named — never landed.

    Raises ValueError on whole-restore refusals (the MCP wrapper
    renders it as {"error": ...})."""
    if Path(archive_name).name != archive_name \
            or not archive_name.endswith(".zip"):
        raise ValueError(
            f"archive must be a bare .zip filename in the backups "
            f"dir, got: {archive_name!r}")
    archive_path = backups_dir() / archive_name
    if not archive_path.is_file():
        raise ValueError(f"backup archive not found: {archive_name}")
    try:
        zf = zipfile.ZipFile(archive_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(
            f"backup archive is not a readable zip: {archive_name} "
            f"({exc})") from exc
    with zf:
        for entry_name in zf.namelist():
            if _unsafe_arcname(entry_name):
                raise ValueError(
                    f"archive entry path escapes its store root: "
                    f"{entry_name!r} — restore refused, nothing written")
        if "manifest.json" not in zf.namelist():
            raise ValueError(
                f"backup archive has no manifest.json: {archive_name}")
        try:
            manifest = json.loads(zf.read("manifest.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"backup manifest is unreadable: {archive_name} "
                f"({exc})") from exc
        files = manifest.get("files")
        if not isinstance(files, list):
            raise ValueError(
                f"backup manifest has no file list: {archive_name}")
        roots = _store_roots()
        restored: list[str] = []
        skipped_identical: list[str] = []
        skipped_needs_confirm: list[str] = []
        refused: list[dict] = []
        plan: list[tuple[str, Path, bytes]] = []
        for entry in files:
            arcname = entry.get("path") if isinstance(entry, dict) \
                else None
            if not arcname or not isinstance(arcname, str):
                refused.append({"path": repr(entry),
                                "reason": "manifest entry has no path"})
                continue
            if _unsafe_arcname(arcname):
                raise ValueError(
                    f"manifest path escapes its store root: "
                    f"{arcname!r} — restore refused, nothing written")
            store, sep, rel = arcname.partition("/")
            if not sep or store not in roots:
                refused.append({"path": arcname,
                                "reason": "unknown store in archive"})
                continue
            reason = _exclusion_reason(Path(rel))
            if reason is not None:
                refused.append({"path": arcname, "reason":
                                f"never archived, never restored: "
                                f"{reason}"})
                continue
            try:
                data = zf.read(arcname)
            except KeyError:
                refused.append({"path": arcname, "reason":
                                "listed in the manifest but missing "
                                "from the archive"})
                continue
            if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
                refused.append({"path": arcname, "reason":
                                "sha256 does not match the manifest"})
                continue
            if rel.endswith(".json"):
                try:
                    json.loads(data)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    refused.append({"path": arcname, "reason":
                                    "content is not valid JSON"})
                    continue
            target = roots[store] / rel
            if not target.resolve().is_relative_to(
                    roots[store].resolve()):
                refused.append({"path": arcname, "reason":
                                "resolved path escapes its store root"})
                continue
            if not target.exists():
                plan.append((arcname, target, data))
                restored.append(arcname)
            elif hashlib.sha256(target.read_bytes()).hexdigest() \
                    == entry["sha256"]:
                skipped_identical.append(arcname)
            elif confirm:
                plan.append((arcname, target, data))
                restored.append(arcname)
            else:
                skipped_needs_confirm.append(arcname)
        pre_restore_backup = None
        if plan and not dry_run:
            try:
                pre_restore_backup = _export_archive(
                    "pre-restore")["archive"]
            except Exception as exc:
                raise ValueError(
                    f"pre-restore backup failed, restore aborted "
                    f"before any write: {exc}") from exc
            for arcname, target, data in plan:
                try:
                    _atomic_write_bytes(target, data)
                except OSError as exc:
                    raise ValueError(
                        f"restore failed while writing {arcname}: "
                        f"{exc} (pre-restore backup: "
                        f"{pre_restore_backup})") from exc
    return {"archive": archive_name,
            "archive_created_at": manifest.get("created_at"),
            "total_files": manifest.get("total_files", len(files)),
            "dry_run": dry_run, "confirm": confirm,
            "restored": restored,
            "skipped_identical": skipped_identical,
            "skipped_needs_confirm": skipped_needs_confirm,
            "refused": refused,
            "pre_restore_backup": pre_restore_backup}


def delete_backup(archive_name: str, confirm: bool = False) -> dict:
    """Delete ONE backup archive from the backups dir.

    The name discipline is restore_core_data's, tightened for a
    destructive op: a bare .zip filename only — any path separator
    (either OS flavour), a drive/parent reference, or a non-.zip
    name is refused before anything is looked up. Without
    confirm=True nothing is deleted: the result is the preview
    (needs_confirm + what would be deleted). Raises ValueError on
    a bad name or a missing archive (the MCP wrapper renders it as
    {"error": ...})."""
    if not archive_name or "/" in archive_name or "\\" in archive_name \
            or archive_name in (".", "..") \
            or not archive_name.endswith(".zip"):
        raise ValueError(
            f"archive must be a bare .zip filename in the backups "
            f"dir, got: {archive_name!r}")
    archive_path = backups_dir() / archive_name
    if not archive_path.is_file():
        raise ValueError(f"backup archive not found: {archive_name}")
    size = archive_path.stat().st_size
    if not confirm:
        return {"archive": archive_name, "needs_confirm": True,
                "would_delete": {"archive": archive_name,
                                 "size_bytes": size}}
    archive_path.unlink()
    return {"archive": archive_name, "deleted": True,
            "size_bytes": size}
