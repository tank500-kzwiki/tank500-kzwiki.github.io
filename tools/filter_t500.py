#!/usr/bin/env python3
"""
filter_t500.py — Extract Tank 500 relevant threads from mixed tech-questions archives.

A "thread" = everything from ### HH:MM to just before the next ### or ##.
A thread is kept if it contains any T500 marker (case-insensitive).
Date headers (## YYYY-MM-DD) are emitted only when ≥1 thread under them passes the filter.

Usage:
    python filter_t500.py <input_dir> --out <output_dir> [--loose]

--loose additionally keeps threads with a standalone "500" (no leading "T").
Use it only for curated topics (e.g. questions-and-answers); on noisy
general/tech-questions chats it would match "500 км", "500 тыс." and similar.
"""

import re
import sys
import argparse
from pathlib import Path

# Markers that identify Tank 500 content (case-insensitive — see re.I below).
# Танк 500 / Tank 500 / Т500 / T500: direct model name.
# Hi4 / Hi4-Z / Hi4-T / хай-4: exclusively Tank 500 (PHEV variants).
# PHEV: plug-in hybrid — only the Hi4 line in this community.
# 3.0T / 3.0Т: engine specific to Tank 500 3.0T variant. 9AT: its gearbox.
# E30Z: factory code for the 3.0T V6 engine.
# Black Edition / Black Trail (+ BT, рус. транслит): T500-specific trims.
# пятисот*: colloquial Russian ("пятисотый" and all declensions).
CORE_MARKERS = (
    r'[тt]анк\s*500'
    r'|[тt]500'
    r'|tank\s*500'
    r'|hi4(?:-[zt])?'
    r'|хай[-\s]?4'
    r'|phev'
    r'|3[.,]0\s*[тt]'
    r'|9at'
    r'|e30z'
    r'|black\s*edition|блэк\s*эдишн'
    r'|black\s*trail|блэк\s*тр[ие]й?[лн]|(?-i:\bBT\b)'  # BT stays case-sensitive: lowercase "bt" = Bluetooth
    r'|пятисот\w+'
)

# Loose marker: a standalone "500" not surrounded by digits ("у 500", "500-ку").
# Catches model mentions without an explicit "T", but on noisy general chats it
# also matches "500 км" / "500 тыс." — enable only via --loose for curated topics.
LOOSE_MARKERS = r'(?<!\d)500(?!\d)'


def build_t500_re(loose: bool):
    pattern = CORE_MARKERS + ('|' + LOOSE_MARKERS if loose else '')
    return re.compile(pattern, re.IGNORECASE)

FRONTMATTER_RE = re.compile(r'\A---\n.*?\n---\n', re.DOTALL)
DATE_LINE_RE = re.compile(r'^## \d{4}-\d{2}-\d{2}')
THREAD_LINE_RE = re.compile(r'^### \d{2}:\d{2}')


def split_segments(body: str):
    """
    Split body into segments: ('preamble'|'date'|'thread', text).
    Text includes the header line and all following lines up to the next header.
    Newlines are preserved as-is.
    """
    segments = []
    seg_type = 'preamble'
    buf = []

    for line in body.splitlines(keepends=True):
        stripped = line.rstrip('\n').rstrip('\r')
        if DATE_LINE_RE.match(stripped):
            if buf:
                segments.append((seg_type, ''.join(buf)))
            seg_type = 'date'
            buf = [line]
        elif THREAD_LINE_RE.match(stripped):
            if buf:
                segments.append((seg_type, ''.join(buf)))
            seg_type = 'thread'
            buf = [line]
        else:
            buf.append(line)

    if buf:
        segments.append((seg_type, ''.join(buf)))

    return segments


def filter_and_reconstruct(frontmatter: str, body: str, t500_re):
    segments = split_segments(body)

    total_threads = sum(1 for t, _ in segments if t == 'thread')
    kept_threads = 0

    parts = [frontmatter]
    pending_date: str | None = None

    for seg_type, content in segments:
        if seg_type == 'preamble':
            parts.append(content)
        elif seg_type == 'date':
            pending_date = content  # hold — emit only if a thread below passes
        else:  # thread
            if t500_re.search(content):
                kept_threads += 1
                if pending_date is not None:
                    parts.append(pending_date)
                    pending_date = None
                parts.append(content)

    return ''.join(parts), total_threads, kept_threads


def process_file(src: Path, dst: Path, t500_re):
    text = src.read_text(encoding='utf-8')
    orig_bytes = len(text.encode('utf-8'))

    m = FRONTMATTER_RE.match(text)
    if m:
        frontmatter, body = m.group(0), text[m.end():]
    else:
        frontmatter, body = '', text

    result, total, kept = filter_and_reconstruct(frontmatter, body, t500_re)

    dst.write_text(result, encoding='utf-8')
    final_bytes = len(result.encode('utf-8'))

    return total, kept, orig_bytes, final_bytes


def main():
    parser = argparse.ArgumentParser(
        description='Filter Tank 500 threads from tech-questions monthly archives'
    )
    parser.add_argument('input_dir', type=Path, help='Directory with YYYY-MM.md source files')
    parser.add_argument('--out', type=Path, required=True, help='Output directory for filtered files')
    parser.add_argument(
        '--loose', action='store_true',
        help='Also keep threads with a standalone "500" (no leading "T"). '
             'Use only for curated topics (e.g. questions-and-answers); '
             'on noisy general/tech-questions it matches "500 км" etc.',
    )
    args = parser.parse_args()

    t500_re = build_t500_re(args.loose)

    if not args.input_dir.is_dir():
        print(f'Error: {args.input_dir} is not a directory', file=sys.stderr)
        sys.exit(1)

    args.out.mkdir(parents=True, exist_ok=True)

    files = sorted(args.input_dir.glob('*.md'))
    if not files:
        print('No .md files found', file=sys.stderr)
        sys.exit(1)

    col = '{:<12} {:>8} {:>10} {:>7}  {:>8} → {:>8}'
    print(col.format('Файл', 'Тредов', 'Оставлено', '%', 'До', 'После'))
    print('─' * 65)

    total_all = kept_all = orig_all = final_all = 0

    for src in files:
        dst = args.out / src.name
        total, kept, orig, final = process_file(src, dst, t500_re)
        pct = kept / total * 100 if total else 0
        print(col.format(
            src.name,
            total,
            kept,
            f'{pct:.0f}%',
            f'{orig / 1024:.0f}K',
            f'{final / 1024:.0f}K',
        ))
        total_all += total
        kept_all += kept
        orig_all += orig
        final_all += final

    print('─' * 65)
    pct_all = kept_all / total_all * 100 if total_all else 0
    print(col.format(
        'ИТОГО',
        total_all,
        kept_all,
        f'{pct_all:.0f}%',
        f'{orig_all / 1024:.0f}K',
        f'{final_all / 1024:.0f}K',
    ))
    print(f'\nФайлы сохранены в: {args.out}')


if __name__ == '__main__':
    main()
