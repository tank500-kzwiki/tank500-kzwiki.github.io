#!/usr/bin/env python3
"""
sync_from_app.py — Pull Telegram exports from the tankz-tg-knowledge app into raw/.

The app (/Users/Artyom_Vetlugin/AVPetProjects/tankz-tg-knowledge) incrementally
exports forum topics to data/<topic>/YYYY-MM.md (+ media/). Its format is byte-for-byte
the same layout raw/tankz-club/ expects, so syncing is a smart mirror + filter, no
conversion.

This script is the ONLY sanctioned writer of raw/tankz-club/ (like filter_t500.py is the
writer of the *-t500/ dirs). The wiki author / ingest still treat raw/ as read-only.

Three steps:
  1. Mirror  — copy new/changed YYYY-MM.md (+ media/ for dedicated topics)
               data/<topic>/ → raw/tankz-club/<topic>/
  2. Filter  — for MIXED topics only, regenerate raw/tankz-club/<topic>-t500/ via
               tools/filter_t500.py (--loose for questions-and-answers)
  3. Manifest — write the list of changed ingest targets (NEW / GREW) to
               tools/.changed-months, so the wiki-ingest skill knows what to разобрать.

State (per-file sha256) lives in tools/.sync-state.json → distinguishes NEW vs GREW and
makes re-runs idempotent (no new data → empty manifest, nothing copied).

Usage:
    python3 tools/sync_from_app.py [--app-dir DIR] [--dry-run] [--pull-app]

    --dry-run   show what would happen, write nothing
    --pull-app  first run `python sync.py --run-once` in the app dir, then sync
    --baseline  seed state from what's already in raw/ (everything currently ingested),
                write an empty manifest, copy/filter nothing. Run ONCE at rollout so the
                first real sync flags only true deltas — not the whole back-catalogue.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
TOOLS_DIR = Path(__file__).resolve().parent
WIKI_ROOT = TOOLS_DIR.parent
RAW_TANKZ = WIKI_ROOT / "raw" / "tankz-club"
FILTER_SCRIPT = TOOLS_DIR / "filter_t500.py"
STATE_FILE = TOOLS_DIR / ".sync-state.json"
MANIFEST_FILE = TOOLS_DIR / ".changed-months"
DEFAULT_APP_DIR = Path("/Users/Artyom_Vetlugin/AVPetProjects/tankz-tg-knowledge")

# ── Topic policy ─────────────────────────────────────────────────────────────
# class:
#   dedicated — topic is entirely about Tank 500: mirror as-is, ingest the raw month
#               (no filter). Carries media.
#   mixed     — topic blends models: mirror raw, then filter_t500.py → <topic>-t500/,
#               ingest the *-t500 month. loose=True relaxes the filter (standalone "500")
#               for curated topics only.
# A source present in the app's config.yaml but absent here is warned about and SKIPPED
# (must be classified explicitly — never pull an unfiltered mixed topic as "про T500").
TOPIC_POLICY = {
    "tank-500":                      {"class": "dedicated", "media": True,  "loose": False},
    "service-campaign-tank-300-500": {"class": "dedicated", "media": True,  "loose": False},
    "general":                       {"class": "mixed",     "media": False, "loose": False},
    "tech-questions":                {"class": "mixed",     "media": False, "loose": False},
    "wheels-discs-tyres":            {"class": "mixed",     "media": False, "loose": False},
    "suspension-chassis":            {"class": "mixed",     "media": False, "loose": False},
    "questions-and-answers":         {"class": "mixed",     "media": False, "loose": True},
}

MONTH_RE = re.compile(r"^\d{4}-\d{2}\.md$")


# ── Helpers ──────────────────────────────────────────────────────────────────
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_app_sources(app_dir: Path) -> list[str] | None:
    """Source names from the app's config.yaml (for cross-check). None if unreadable."""
    cfg = app_dir / "config.yaml"
    if not cfg.is_file():
        return None
    text = cfg.read_text(encoding="utf-8")
    try:
        import yaml  # optional; app env has it, wiki env may not
        data = yaml.safe_load(text)
        return [s["name"] for s in data.get("sources", []) if "name" in s]
    except Exception:
        # stdlib fallback: grab `name:` lines under the sources: block
        names, in_sources = [], False
        for line in text.splitlines():
            if re.match(r"^sources\s*:", line):
                in_sources = True
                continue
            if in_sources:
                if line and not line[0].isspace() and not line.lstrip().startswith("#"):
                    break  # dedented out of sources:
                m = re.match(r"\s*-\s*name\s*:\s*(\S+)", line)
                if m:
                    names.append(m.group(1).strip().strip('"').strip("'"))
        return names or None


def month_files(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and MONTH_RE.match(p.name))


def needs_copy(src: Path, dst: Path) -> bool:
    if not dst.exists():
        return True
    s, d = src.stat(), dst.stat()
    return s.st_size != d.st_size or s.st_mtime > d.st_mtime


def is_substantive(path: Path) -> bool:
    """A month file worth ingesting has ≥1 thread (### HH:MM). filter_t500.py emits a
    frontmatter-only stub for months with no T500 content — those carry nothing to разобрать."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if re.match(r"^### \d{2}:\d{2}", line):
            return True
    return False


# ── Core ─────────────────────────────────────────────────────────────────────
def mirror_months(src_dir: Path, dst_dir: Path, dry: bool) -> int:
    """Copy changed YYYY-MM.md files. Returns count copied."""
    copied = 0
    for src in month_files(src_dir):
        dst = dst_dir / src.name
        if needs_copy(src, dst):
            copied += 1
            if not dry:
                dst_dir.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
    return copied


def mirror_media(src_dir: Path, dst_dir: Path, dry: bool) -> int:
    """Copy new/changed media files (size/mtime heuristic). Returns count copied."""
    src_media = src_dir / "media"
    if not src_media.is_dir():
        return 0
    dst_media = dst_dir / "media"
    copied = 0
    for src in sorted(src_media.iterdir()):
        if not src.is_file() or src.name == ".DS_Store":
            continue
        dst = dst_media / src.name
        if needs_copy(src, dst):
            copied += 1
            if not dry:
                dst_media.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
    return copied


def run_filter(raw_dir: Path, out_dir: Path, loose: bool) -> None:
    cmd = [sys.executable, str(FILTER_SCRIPT), str(raw_dir), "--out", str(out_dir)]
    if loose:
        cmd.append("--loose")
    subprocess.run(cmd, check=True, cwd=str(WIKI_ROOT))


def classify_target(rel: str, path: Path, state: dict) -> str | None:
    """NEW if unseen, GREW if hash changed, None if unchanged. Updates state."""
    new_hash = sha256_file(path)
    old_hash = state.get(rel)
    state[rel] = new_hash
    if old_hash is None:
        return "NEW"
    if old_hash != new_hash:
        return "GREW"
    return None


def load_manifest() -> dict:
    """Existing manifest as {reltarget: status}. Accumulates across syncs until ingest."""
    out = {}
    if MANIFEST_FILE.exists():
        for line in MANIFEST_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                out[parts[0]] = parts[1]
    return out


def baseline(dry: bool) -> int:
    """Seed state from current on-disk ingest targets; empty the manifest. No copy/filter."""
    state = {}
    seeded = 0
    for name, policy in TOPIC_POLICY.items():
        sub = name if policy["class"] == "dedicated" else f"{name}-t500"
        for path in month_files(RAW_TANKZ / sub):
            state[f"{sub}/{path.name}"] = sha256_file(path)
            seeded += 1
    tag = "[dry-run] " if dry else ""
    print(f"{tag}Baseline: засеяно состояние по {seeded} месячным файлам из raw/.")
    if not dry:
        save_state(state)
        write_manifest({}, dry=False)
    print(f"{tag}Готово. Манифест пуст — первый обычный sync пометит только реальные дельты.")
    return 0


def write_manifest(entries: dict, dry: bool) -> None:
    lines = [
        "# Изменённые цели ingest со времени последнего разбора.",
        "# Формат: <dir>/<YYYY-MM>.md  NEW|GREW  — заполняет sync_from_app.py, чистит wiki-ingest.",
    ]
    for target in sorted(entries):
        lines.append(f"{target}  {entries[target]}")
    text = "\n".join(lines) + "\n"
    if dry:
        print("\n--- .changed-months (предпросмотр) ---")
        print(text, end="")
    else:
        MANIFEST_FILE.write_text(text, encoding="utf-8")


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Sync app data/ → wiki raw/tankz-club/")
    ap.add_argument("--app-dir", type=Path, default=DEFAULT_APP_DIR,
                    help=f"App root with data/ + config.yaml (default: {DEFAULT_APP_DIR})")
    ap.add_argument("--dry-run", action="store_true", help="show plan, write nothing")
    ap.add_argument("--pull-app", action="store_true",
                    help="first run `python sync.py --run-once` in the app dir")
    ap.add_argument("--baseline", action="store_true",
                    help="seed state from current raw/, write empty manifest, copy/filter nothing")
    args = ap.parse_args()

    if args.baseline:
        return baseline(args.dry_run)

    data_dir = args.app_dir / "data"
    if not data_dir.is_dir():
        print(f"Ошибка: нет директории {data_dir}", file=sys.stderr)
        return 1

    if args.pull_app:
        if args.dry_run:
            print(f"[dry-run] пропускаю `python sync.py --run-once` в {args.app_dir}")
        else:
            print(f"→ Тяну свежие сообщения: python sync.py --run-once в {args.app_dir}")
            subprocess.run([sys.executable, "sync.py", "--run-once"],
                           check=True, cwd=str(args.app_dir))

    # Cross-check the app's source list against our policy.
    app_sources = read_app_sources(args.app_dir)
    if app_sources is not None:
        for name in app_sources:
            if name not in TOPIC_POLICY and (data_dir / name).is_dir():
                print(f"⚠️  Источник '{name}' есть в config.yaml, но не классифицирован "
                      f"в TOPIC_POLICY — пропускаю (добавь класс вручную).")

    state = load_state()
    manifest = load_manifest()
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}Синхронизация {data_dir} → {RAW_TANKZ}\n")

    for name, policy in TOPIC_POLICY.items():
        src_dir = data_dir / name
        if not src_dir.is_dir():
            continue  # topic not exported (e.g. disabled) — silently skip
        raw_dir = RAW_TANKZ / name

        md_copied = mirror_months(src_dir, raw_dir, args.dry_run)
        media_copied = mirror_media(src_dir, raw_dir, args.dry_run) if policy["media"] else 0

        line = f"  {name:<32} {policy['class']:<9} md+{md_copied}"
        if policy["media"]:
            line += f" media+{media_copied}"

        if policy["class"] == "dedicated":
            # Ingest target = the raw month file itself.
            if not args.dry_run:
                for path in month_files(raw_dir):
                    rel = f"{name}/{path.name}"
                    status = classify_target(rel, path, state)
                    if status and rel not in manifest and is_substantive(path):
                        manifest[rel] = status
            print(line)
        else:
            # Mixed: regenerate <topic>-t500/, ingest target = the -t500 month file.
            t500_dir = RAW_TANKZ / f"{name}-t500"
            loose = " --loose" if policy["loose"] else ""
            if args.dry_run:
                print(line + f"  → filter_t500.py{loose} (цели -t500 после фильтра)")
            else:
                run_filter(raw_dir, t500_dir, policy["loose"])
                changed = 0
                for path in month_files(t500_dir):
                    rel = f"{name}-t500/{path.name}"
                    status = classify_target(rel, path, state)
                    if status and rel not in manifest and is_substantive(path):
                        manifest[rel] = status
                        changed += 1
                print(line + f"  → -t500 changed:{changed}")

    if not args.dry_run:
        save_state(state)
    write_manifest(manifest, args.dry_run)

    n = len(manifest)
    print(f"\n{tag}Готово. Целей в манифесте для ingest: {n}"
          + ("" if args.dry_run else f"  ({MANIFEST_FILE})"))
    if n and not args.dry_run:
        print("Дальше: в сессии Claude вызови skill wiki-ingest («разбери изменения»).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
