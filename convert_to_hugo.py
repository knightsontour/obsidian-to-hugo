#!/usr/bin/env python3
"""
convert_to_hugo.py
==================
Converts publishable Obsidian notes to Hugo-ready content.

What it does:
  1. Renames files: "13 - Yosemite a Paradise on Earth.md"
                 -> "yosemite-a-paradise-on-earth.md"
  2. Routes files into Hugo sections based on `hugo_section` front matter field:
       hugo_section: travel/knights-around-the-world-2017-18
       -> content/travel/knights-around-the-world-2017-18/yosemite-a-paradise-on-earth.md
       (no field -> content/posts/)
  3. Converts image embeds to standard markdown:
       ![[filename.jpg]] -> ![](/assets/filename.jpg)
  4. Converts wikilinks to absolute Hugo URLs:
       [[13 - Yosemite]] -> [Yosemite](/travel/knights-around-the-world-2017-18/yosemite/)
  5. Cleans up front matter (removes Obsidian-specific fields, adds slug/title).
  6. Copies all assets.
  7. Detects wikilinks to unpublished notes (single-file mode).

Usage:
  # Full vault
  python3 convert_to_hugo.py --source "/path/to/vault" --output "./hugo-output"
  # Single file (used by Obsidian Shell Commands hotkey)
  python3 convert_to_hugo.py --source "/path/to/vault" --output "./hugo-output" --file "/path/to/note.md"
"""

import argparse
import re
import shutil
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Convert a filename or title to a Hugo-friendly slug."""
    text = re.sub(r'^\d+\s*[-–]\s*', '', text)
    text = text.lower()
    # Drop apostrophes entirely so "What's" -> "whats" (matches the live
    # knights-site slugs) rather than "what-s".
    text = text.replace('’', '').replace("'", '')
    text = re.sub(r'[^a-z0-9]+', '-', text)
    text = text.strip('-')
    return text


def filename_to_slug(filename: str) -> str:
    return slugify(filename)


def section_to_title(section: str) -> str:
    """Convert a section path to a human-readable title.
    'travel/knights-around-the-world-2017-18' -> 'Knights Around the World 2017-18'
    """
    last = section.split('/')[-1]
    return last.replace('-', ' ').title()


# ---------------------------------------------------------------------------
# Front matter helpers
# ---------------------------------------------------------------------------

REMOVE_FIELDS = {'prev', 'next', 'trip', 'chapter', 'hugo_section', 'publish'}


def read_front_matter(src_path: Path) -> tuple[str, str]:
    """Return (fm_text, body) from a note, or ('', full_text) if no front matter."""
    raw = src_path.read_text(encoding='utf-8')
    fm_match = re.match(r'^---\n(.*?)\n---\n(.*)', raw, re.DOTALL)
    if fm_match:
        return fm_match.group(1), fm_match.group(2)
    return '', raw


def get_fm_field(fm_text: str, field: str) -> str:
    """Extract a scalar front matter field value, or ''."""
    for line in fm_text.splitlines():
        m = re.match(rf'^{field}\s*:\s*(.+)$', line.strip())
        if m:
            return m.group(1).strip().strip('"\'')
    return ''


def is_publishable(src_path: Path) -> bool:
    try:
        fm_text, _ = read_front_matter(src_path)
        return bool(re.search(r'^publish\s*:\s*true\s*$', fm_text, re.MULTILINE | re.IGNORECASE))
    except Exception:
        return False


def get_note_info(src_path: Path):
    """Return {slug, section, path, weight} for a publishable note, or None."""
    if not is_publishable(src_path):
        return None
    fm_text, _ = read_front_matter(src_path)
    slug = filename_to_slug(src_path.stem)
    section = get_fm_field(fm_text, 'hugo_section') or 'posts'
    m = re.match(r'^(\d+)\s*[-–]', src_path.stem)
    weight = int(m.group(1)) if m else 10**9
    return {'slug': slug, 'section': section, 'path': src_path, 'weight': weight}


def dedupe_slugs(slug_map: dict) -> None:
    """Resolve duplicate slugs within a section in place.

    Two notes that slugify to the same value (e.g. "73 - Turning for home" and
    "90 - Turning for home") would collide. The lowest-weight note keeps the base
    slug; later ones get "-2", "-3", … — matching the live knights-site slugs.
    """
    by_section: dict[str, list] = {}
    for info in slug_map.values():
        by_section.setdefault(info['section'], []).append(info)

    for infos in by_section.values():
        seen: dict[str, int] = {}
        for info in sorted(infos, key=lambda i: (i['weight'], i['slug'])):
            base = info['slug']
            n = seen.get(base, 0) + 1
            seen[base] = n
            if n > 1:
                info['slug'] = f'{base}-{n}'


def process_front_matter(fm_text: str, slug: str, title: str) -> str:
    """Strip Obsidian-specific fields, add Hugo slug/title."""
    lines = fm_text.strip().splitlines()
    out = []
    skip_block = False

    for line in lines:
        if skip_block:
            if line.startswith(' ') or line.startswith('-'):
                continue
            else:
                skip_block = False

        m = re.match(r'^(\w+)\s*:', line)
        if m and m.group(1) in REMOVE_FIELDS:
            skip_block = True
            continue

        # Normalise bare tags (e.g. "tags: moc, area" -> "tags: [moc, area]")
        if re.match(r'^tags\s*:', line) and '[' not in line and line.strip() != 'tags:':
            tag_val = re.sub(r'^tags\s*:\s*', '', line).strip()
            tags = [t.strip() for t in tag_val.split(',') if t.strip()]
            line = f'tags: [{", ".join(tags)}]'

        out.append(line)

    out.append(f'slug: "{slug}"')
    if title:
        out.append(f'title: "{title}"')

    return '\n'.join(out)


def extract_title(body: str) -> str:
    match = re.search(r'^#\s+(.+)$', body, re.MULTILINE)
    if match:
        title = match.group(1).strip()
        title = re.sub(r'\s*[—–]\s*', ': ', title)
        title = title.replace('\\#', '#').replace('"', '\\"')
        return title
    return ''


# ---------------------------------------------------------------------------
# Image embed conversion
# ---------------------------------------------------------------------------

def convert_image_embeds(content: str, available_assets: set) -> tuple[str, list]:
    missing = []

    def replace_embed(match):
        filename = match.group(1).strip().split('/')[-1]
        if filename not in available_assets:
            missing.append(filename)
        return f'![{filename}](/assets/{filename})'

    converted = re.sub(r'!\[\[([^\]]+)\]\]', replace_embed, content)
    return converted, missing


# ---------------------------------------------------------------------------
# Wikilink conversion
# ---------------------------------------------------------------------------

def convert_wikilinks(content: str, slug_map: dict) -> str:
    """
    Convert [[target|display]] wikilinks to absolute Hugo URLs.
    slug_map: {stem -> {slug, section, path}}
    Unresolved links are kept as plain text (no broken links).
    """
    def replace_wikilink(match):
        inner = match.group(1)
        # Handle both [[target|display]] and [[target\|display]] (table cell escaping)
        inner = inner.replace('\\|', '|')
        target, display = (inner.split('|', 1) + [None])[:2]
        target = target.strip()
        display = display.strip() if display else None

        target_stem = re.sub(r'\.md$', '', target.split('/')[-1])

        info = slug_map.get(target_stem)
        if info:
            url = f'/{info["section"]}/{info["slug"]}/'
            label = display or section_to_title(info['slug'])
        else:
            # Unpublished or unknown note — render as plain text
            label = display or slugify(target_stem).replace('-', ' ').title()
            return label  # no link

        return f'[{label}]({url})'

    return re.sub(r'\[\[([^\]]+)\]\]', replace_wikilink, content)


def find_unpublished_wikilinks(src_path: Path, slug_map: dict) -> list[dict]:
    """
    Return list of {stem, path} for wikilinks in src_path that point to
    unpublished notes (i.e. notes not in slug_map).
    """
    fm_text, body = read_front_matter(src_path)
    unpublished = []
    seen = set()

    # Build stem->path map for all vault notes
    vault_root = src_path
    while vault_root.parent != vault_root:
        if (vault_root / '.obsidian').exists():
            break
        vault_root = vault_root.parent

    # (?<!\!) excludes image embeds (![[file.webp]]) — only real note links count.
    for match in re.finditer(r'(?<!\!)\[\[([^\]]+?)(?:\\?\|[^\]]*)?\]\]', body):
        raw = match.group(1).strip()
        target_stem = re.sub(r'\.md$', '', raw.split('/')[-1])
        if target_stem in slug_map or target_stem in seen:
            continue
        if target_stem.upper() == 'README':
            continue  # trip README is the "back to contents" link, not a broken link
        seen.add(target_stem)

        # Try to find the source file
        candidates = list(vault_root.rglob(f'{target_stem}.md')) if vault_root else []
        unpublished.append({
            'stem': target_stem,
            'path': str(candidates[0]) if candidates else '',
        })

    return unpublished


# ---------------------------------------------------------------------------
# Web-mode (knights-site / Beautiful Hugo) helpers
# ---------------------------------------------------------------------------

def first_embed_filename(body: str) -> str:
    """Return the filename of the first ![[embed]] in the body, or ''."""
    m = re.search(r'!\[\[([^\]]+?)\]\]', body)
    if m:
        return m.group(1).strip().split('/')[-1]
    return ''


def build_footer_nav(fm_text: str, section: str, slug_map: dict) -> str:
    """Build the Beautiful Hugo body footer nav line from prev/next front matter.

    Matches the live knights-site format exactly:
      middle: ← [← Previous](url) · Contents · [Next →](url)
      first : ← Back to contents · [Next →](url)
      last  : ← [← Previous](url) · Back to contents
    A prev/next that is absent or points to README counts as no link.
    Resolves the target through slug_map so de-duplicated slugs (…-2) are honoured.
    """
    def link_url(raw: str):
        m = re.search(r'\[\[([^\]]+?)\]\]', raw or '')
        if not m:
            return None
        target = m.group(1).split('|')[0].strip()
        stem = re.sub(r'\.md$', '', target.split('/')[-1])
        if stem.upper() == 'README':
            return None
        info = slug_map.get(stem)
        slug = info['slug'] if info else slugify(stem)
        return f'/{section}/{slug}/'

    prev_url = link_url(get_fm_field(fm_text, 'prev'))
    next_url = link_url(get_fm_field(fm_text, 'next'))
    contents = f'[Back to contents](/{section}/)'

    if not prev_url and not next_url:
        return f'← {contents}'

    left = f'← [← Previous]({prev_url})' if prev_url else f'← {contents}'
    right = f'[Next →]({next_url})' if next_url else contents

    if prev_url and next_url:
        return f'{left} · [Contents](/{section}/) · {right}'
    return f'{left} · {right}'


# ---------------------------------------------------------------------------
# Per-file conversion
# ---------------------------------------------------------------------------

def convert_file(
    src_path: Path,
    dest_path: Path,
    slug_map: dict,
    available_assets: set,
    report: dict,
    web: bool = False,
    section: str = '',
    slug: str = '',
):
    fm_text, body = read_front_matter(src_path)

    if not slug:
        slug = filename_to_slug(src_path.stem)
    title = extract_title(body)

    # Cover image (web mode only) — first embed, captured before conversion.
    cover = first_embed_filename(body) if web else ''

    # Record which assets this note references so web mode copies only those.
    for emb in re.findall(r'!\[\[([^\]]+?)\]\]', body):
        report.setdefault('assets_used', set()).add(emb.strip().split('/')[-1])

    # Web mode zero-pads a leading chapter number in the title to two digits
    # ("1: Welcome" -> "01: Welcome") to match the live knights-site titles.
    if web and title:
        title = re.sub(r'^(\d+):', lambda m: f'{int(m.group(1)):02d}:', title)

    # Extract leading chapter number for chronological ordering (e.g. "01 - Yosemite" → weight 1)
    chapter_match = re.match(r'^(\d+)\s*[-–]', src_path.stem)
    weight = int(chapter_match.group(1)) if chapter_match else None

    if fm_text:
        clean_fm = process_front_matter(fm_text, slug, title)
        if weight is not None:
            clean_fm += f'\nweight: {weight}'
        if web and cover:
            clean_fm += f'\nimage: "/assets/{cover}"'
        fm_block = f'---\n{clean_fm}\n---\n'
    else:
        weight_line = f'\nweight: {weight}' if weight is not None else ''
        cover_line = f'\nimage: "/assets/{cover}"' if (web and cover) else ''
        fm_block = f'---\nslug: "{slug}"\ntitle: "{title}"{weight_line}{cover_line}\n---\n'

    # Remove the H1 from the body — Hugo renders the title from front matter
    body = re.sub(r'^#[^#].*\n?', '', body, count=1, flags=re.MULTILINE)

    body, missing = convert_image_embeds(body, available_assets)
    if missing:
        report['missing_images'][src_path.name] = missing

    if web:
        # A [[README]] link inside a trip is the "back to contents" link. Point
        # it at the section index — convert_wikilinks would otherwise drop it to
        # plain text, since the trip README itself is never published.
        body = re.sub(
            r'\[\[README(?:\\?\|([^\]]*))?\]\]',
            lambda m: f'[{(m.group(1) or "Back to contents").strip()}](/{section}/)',
            body,
        )

    body = convert_wikilinks(body, slug_map)

    if web:
        # Some trips (e.g. 2017-18) already carry a wikilink footer nav in the
        # note body, which convert_wikilinks has just turned into proper links.
        # Others (e.g. big-lap) don't — synthesise one from prev/next only when
        # the body doesn't already end with a nav line.
        last_line = body.rstrip('\n').rsplit('\n', 1)[-1] if body.strip() else ''
        if 'Next →' not in last_line and 'Back to contents' not in last_line:
            nav = build_footer_nav(fm_text, section, slug_map)
            body = body.rstrip('\n') + f'\n\n---\n{nav}\n'

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(fm_block + body, encoding='utf-8')


# ---------------------------------------------------------------------------
# Section index helpers
# ---------------------------------------------------------------------------

VAULT_SECTIONS_MD = Path('/Users/Steve/Library/Mobile Documents/iCloud~md~obsidian/Documents/Journal + PKM/04-resources/Hugo/sections.md')


def load_section_titles(out_dir: Path) -> dict:
    """Load title overrides from sections.md in the vault (YAML inside a code block).
    Also syncs the extracted YAML to hugo-output/data/sections.yaml for Hugo to use.
    """
    import yaml
    if VAULT_SECTIONS_MD.exists():
        raw = VAULT_SECTIONS_MD.read_text(encoding='utf-8')
        match = re.search(r'```yaml\n(.*?)```', raw, re.DOTALL)
        if match:
            try:
                data = yaml.safe_load(match.group(1)) or {}
                sections_file = out_dir / 'data' / 'sections.yaml'
                sections_file.parent.mkdir(parents=True, exist_ok=True)
                sections_file.write_text(match.group(1), encoding='utf-8')
                return {k: v.get('title', '') for k, v in data.items() if isinstance(v, dict)}
            except Exception:
                pass
        return {}

    # Fallback to yaml file
    import yaml
    sections_file = out_dir / 'data' / 'sections.yaml'
    if sections_file.exists():
        try:
            data = yaml.safe_load(sections_file.read_text(encoding='utf-8')) or {}
            return {k: v.get('title', '') for k, v in data.items() if isinstance(v, dict)}
        except Exception:
            pass
    return {}


def auto_add_section(section: str):
    """Append a skeleton entry to sections.md if this section isn't already there."""
    import yaml
    if not VAULT_SECTIONS_MD.exists():
        return

    raw = VAULT_SECTIONS_MD.read_text(encoding='utf-8')
    match = re.search(r'```yaml\n(.*?)```', raw, re.DOTALL)
    if not match:
        return

    try:
        data = yaml.safe_load(match.group(1)) or {}
    except Exception:
        return

    # Check all parent paths too
    parts = section.split('/')
    paths_to_check = ['/'.join(parts[:i+1]) for i in range(len(parts))]
    new_entries = [p for p in paths_to_check if p not in data]

    if not new_entries:
        return

    # Build skeleton YAML lines to append
    new_yaml = ''
    for path in new_entries:
        title = section_to_title(path)
        new_yaml += f'\n{path}:\n  title: "{title}"\n  icon: "📄"\n  description: ""\n'

    # Insert before closing ```
    updated = raw[:match.end(1)] + new_yaml + raw[match.end(1):]
    VAULT_SECTIONS_MD.write_text(updated, encoding='utf-8')
    print(f'  ✚  Auto-added to sections.md: {", ".join(new_entries)}')


def ensure_section_indexes(out_dir: Path, sections: set[str]):
    """Create _index.md for every section and its parent paths.
    Uses title from data/sections.yaml if available, otherwise auto-derives it.
    Always overwrites so title changes in sections.yaml take effect on next run.
    """
    content_dir = out_dir / 'content'
    title_overrides = load_section_titles(out_dir)


    paths_needed = set()
    for section in sections:
        parts = section.split('/')
        for i in range(len(parts)):
            paths_needed.add('/'.join(parts[:i+1]))

    for path in sorted(paths_needed):
        index = content_dir / path / '_index.md'
        title = title_overrides.get(path) or section_to_title(path)
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(f'---\ntitle: "{title}"\n---\n')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Convert Obsidian vault notes to Hugo format.')
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', default='./hugo-output')
    parser.add_argument('--file', help='Single file mode — convert one note only')
    parser.add_argument('--web', action='store_true',
                        help='Emit Beautiful Hugo (knights-site) format: travel/* sections only, '
                             'with cover image + body footer nav. Skips sections.yaml/_index sync.')
    args = parser.parse_args()

    src_dir = Path(args.source).expanduser().resolve()
    out_dir = Path(args.output).resolve()

    if not src_dir.exists():
        print(f'ERROR: Source not found: {src_dir}')
        sys.exit(1)

    (out_dir / 'static' / 'assets').mkdir(parents=True, exist_ok=True)

    # Sync sections.yaml from vault so Hugo data is current.
    # Skipped in web mode — knights-site manages its own _index.md files and layout.
    if not args.web:
        load_section_titles(out_dir)

    # Build slug map — {stem -> {slug, section, path}}
    all_md = [f for f in src_dir.rglob('*.md') if f.stem.upper() != 'README']
    slug_map: dict[str, dict] = {}
    for f in all_md:
        info = get_note_info(f)
        if info:
            slug_map[f.stem] = info
    dedupe_slugs(slug_map)

    # Build asset map — filename -> source path
    available_assets: dict[str, Path] = {}
    for asset_file in src_dir.rglob('*'):
        if asset_file.is_file() and asset_file.suffix.lower() in {
            '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.mp4', '.pdf'
        }:
            available_assets[asset_file.name] = asset_file

    report = {'missing_images': {}, 'converted': [], 'skipped': [], 'assets_used': set()}

    if args.file:
        # ── Single-file mode ───────────────────────────────────────────────────
        src_path = Path(args.file).expanduser().resolve()

        if not src_path.exists():
            print(f'DELETED or not found: {src_path.name}')
            sys.exit(0)

        if not is_publishable(src_path):
            # Unpublish — find and delete the output file in any section
            slug = filename_to_slug(src_path.stem)
            deleted = []
            for candidate in (out_dir / 'content').rglob(f'{slug}.md'):
                candidate.unlink()
                deleted.append(candidate)
            if deleted:
                section = deleted[0].parent.relative_to(out_dir / 'content')
                print(f'  ✗  {src_path.name} -> unpublished ({section}/{slug}.md deleted)')
            else:
                print(f'  –  {src_path.name} (skipped — no publish: true flag)')
            sys.exit(0)

        # Determine section and output path
        fm_text, body_for_assets = read_front_matter(src_path)
        section = get_fm_field(fm_text, 'hugo_section') or 'posts'
        # Prefer the de-duplicated slug from the global map (handles title collisions).
        info = slug_map.get(src_path.stem)
        slug = info['slug'] if info else filename_to_slug(src_path.stem)
        dest_path = out_dir / 'content' / section / f'{slug}.md'

        # Web mode is travel-only — knights-site only carries trip sections.
        if args.web and not section.startswith('travel/'):
            print(f'  –  {src_path.name} (skipped — web mode is travel/* only)')
            sys.exit(0)

        if args.web:
            # Copy this note's referenced images into knights-site/static/assets.
            for fn in re.findall(r'!\[\[([^\]]+?)\]\]', body_for_assets):
                name = fn.strip().split('/')[-1]
                src_asset = available_assets.get(name)
                if src_asset:
                    shutil.copy2(src_asset, out_dir / 'static' / 'assets' / name)
        else:
            # Auto-add skeleton entry to sections.md if this section is new
            auto_add_section(section)
            # Ensure _index.md exists for this section (and all parent sections)
            ensure_section_indexes(out_dir, {section})

        try:
            convert_file(src_path, dest_path, slug_map, set(available_assets.keys()), report,
                         web=args.web, section=section, slug=slug)
            print(f'  ✓  {src_path.name} -> {section}/{slug}.md')
        except Exception as e:
            print(f'  ✗  {src_path.name}: {e}')
            sys.exit(1)

        if report['missing_images']:
            for post, images in report['missing_images'].items():
                print(f'  ⚠️  missing images: {", ".join(images)}')

        # Detect unpublished wikilinks
        unpublished = find_unpublished_wikilinks(src_path, slug_map)
        for item in unpublished:
            print(f'UNPUBLISHED_LINK\t{item["stem"]}\t{item["path"]}')

    else:
        # ── Full-vault mode ────────────────────────────────────────────────────
        print(f'Found {len(available_assets)} assets, {len(slug_map)} publishable notes.')
        sections_used = set()

        for src_path in sorted(all_md):
            info = slug_map.get(src_path.stem)  # deduped slugs
            if not info:
                report['skipped'].append(src_path.name)
                continue

            section = info['section']
            # Web mode is travel-only — knights-site only carries trip sections.
            if args.web and not section.startswith('travel/'):
                report['skipped'].append(src_path.name)
                continue
            slug = info['slug']
            dest_path = out_dir / 'content' / section / f'{slug}.md'
            sections_used.add(section)

            try:
                convert_file(src_path, dest_path, slug_map, set(available_assets.keys()), report,
                             web=args.web, section=section, slug=slug)
                report['converted'].append(f'{src_path.name} -> {section}/{slug}.md')
                print(f'  ✓  {src_path.name}')
            except Exception as e:
                report['skipped'].append(f'{src_path.name}: {e}')
                print(f'  ✗  {src_path.name}: {e}')

        # Auto-add skeleton entries for new sections + create _index.md files.
        # Skipped in web mode — knights-site has its own _index.md / layout.
        if not args.web:
            for s in sections_used:
                auto_add_section(s)
            ensure_section_indexes(out_dir, sections_used)

        # Copy assets. In web mode copy only assets referenced by travel notes —
        # the vault has many unrelated images (tutorials, photography) that must
        # not leak onto the public site.
        copied = 0
        for name, src_asset in available_assets.items():
            if args.web and name not in report['assets_used']:
                continue
            shutil.copy2(src_asset, out_dir / 'static' / 'assets' / name)
            copied += 1
        print(f'\nCopied {copied} assets.')

        print(f'\n{"="*60}')
        print(f'CONVERSION COMPLETE')
        print(f'{"="*60}')
        print(f'Converted : {len(report["converted"])}')
        print(f'Skipped   : {len(report["skipped"])}')
        print(f'Sections  : {", ".join(sorted(sections_used))}')

        if report['missing_images']:
            print(f'\n⚠️  MISSING IMAGES:')
            for post, images in report['missing_images'].items():
                print(f'  {post}: {", ".join(images)}')
        else:
            print('\n✅ All image references resolved.')

        print(f'\nOutput: {out_dir}')


if __name__ == '__main__':
    main()
