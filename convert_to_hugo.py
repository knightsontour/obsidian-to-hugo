#!/usr/bin/env python3
"""
convert_to_hugo.py
==================
Converts the 'Knights Around the World' Obsidian folder to a Hugo-ready
content directory.

What it does:
  1. Renames files: "13 - Yosemite a Paradise on Earth.md"
                 -> "yosemite-a-paradise-on-earth.md"
  2. Converts image embeds (all variants) to standard markdown:
       ![[filename.jpg]]                        -> ![](/assets/filename.jpg)
       ![[assets/filename.jpg]]                 -> ![](/assets/filename.jpg)
       ![[04-resources/.../assets/filename.jpg]] -> ![](/assets/filename.jpg)
  3. Converts wikilinks to Hugo relative links:
       [[13 - Yosemite a Paradise on Earth]]       -> [Yosemite a Paradise on Earth](../yosemite-a-paradise-on-earth/)
       [[13 - Yosemite a Paradise on Earth|Next →]] -> [Next →](../yosemite-a-paradise-on-earth/)
       [[README]]                                   -> [Contents](../)
       [[README|Back to contents]]                  -> [Back to contents](../)
  4. Cleans up the front matter:
       - Removes Obsidian-specific fields (prev, next, trip)
       - Converts tags list to Hugo format
       - Adds 'slug' field for clean URLs
  5. Copies assets folder as-is.
  6. Generates a report of any image references it couldn't resolve.

Usage:
  python3 convert_to_hugo.py --source "/path/to/Knights Around the World 2017-18" --output "./hugo-output"

Output structure:
  hugo-output/
    content/
      posts/           <- converted .md files
    static/
      assets/          <- images copied here
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
    # Strip leading chapter number:  "13 - Yosemite..." -> "Yosemite..."
    text = re.sub(r'^\d+\s*[-–]\s*', '', text)
    text = text.lower()
    # Replace non-alphanumeric chars with hyphens
    text = re.sub(r'[^a-z0-9]+', '-', text)
    text = text.strip('-')
    return text


def filename_to_slug(filename: str) -> str:
    """Derive slug from an Obsidian filename (without extension)."""
    return slugify(filename)


def wikilink_target_to_slug(target: str) -> str:
    """
    Convert a wikilink target like '13 - Yosemite a Paradise on Earth'
    to its slug equivalent.
    """
    # Strip any leading path components (Obsidian sometimes includes full paths)
    target = target.split('/')[-1]
    # Strip .md extension if present
    target = re.sub(r'\.md$', '', target)
    return filename_to_slug(target)


# ---------------------------------------------------------------------------
# Front matter processing
# ---------------------------------------------------------------------------

REMOVE_FIELDS = {'prev', 'next', 'trip', 'chapter'}


def process_front_matter(fm_text: str, slug: str, title: str) -> str:
    """
    Clean up YAML front matter for Hugo:
    - Remove Obsidian-specific fields
    - Add slug
    - Reformat tags if needed
    Returns the cleaned front matter block (without --- delimiters).
    """
    lines = fm_text.strip().splitlines()
    out = []
    skip_next = False

    for line in lines:
        if skip_next:
            # We're inside a multi-line field we want to skip — keep skipping
            # until we hit a new key (line that doesn't start with space/-)
            if line.startswith(' ') or line.startswith('-'):
                continue
            else:
                skip_next = False

        # Check if this line is a key we want to remove
        match = re.match(r'^(\w+)\s*:', line)
        if match:
            key = match.group(1)
            if key in REMOVE_FIELDS:
                skip_next = False  # single-line fields only for these
                continue

        out.append(line)

    # Add Hugo-specific fields
    out.append(f'slug: "{slug}"')
    out.append(f'title: "{title}"')

    return '\n'.join(out)


def extract_title(content: str) -> str:
    """Extract the H1 title from the markdown body."""
    match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    if match:
        # Clean up any em-dashes and extra whitespace
        title = match.group(1).strip()
        title = re.sub(r'\s*[—–]\s*', ': ', title)
        # Escape quotes and # for YAML
        title = title.replace('"', '\\"')
        title = title.replace('#', '\\#')
        return title
    return ''


# ---------------------------------------------------------------------------
# Image embed conversion
# ---------------------------------------------------------------------------

def convert_image_embeds(content: str, available_assets: set) -> tuple[str, list]:
    """
    Convert all Obsidian image embed variants to standard markdown.
    Returns (converted_content, list_of_missing_images).
    """
    missing = []

    def replace_embed(match):
        raw = match.group(1).strip()

        # Extract just the filename — strip any path prefix
        filename = raw.split('/')[-1]

        if filename not in available_assets:
            missing.append(filename)

        # Hugo will serve static files from /assets/
        return f'![{filename}](/assets/{filename})'

    # Match ![[...]] with optional path — greedy to handle full vault paths
    pattern = r'!\[\[([^\]]+)\]\]'
    converted = re.sub(pattern, replace_embed, content)

    return converted, missing


# ---------------------------------------------------------------------------
# Wikilink conversion
# ---------------------------------------------------------------------------

def convert_wikilinks(content: str, slug_map: dict, index_mode: bool = False) -> str:
    """
    Convert [[target|display]] and [[target]] wikilinks to Hugo relative links.
    slug_map: dict of {obsidian_filename_stem -> slug}
    index_mode: True when converting _index.md (served at /posts/), so links
                use slug/ instead of ../slug/ to avoid resolving one level too high.
    """

    def replace_wikilink(match):
        inner = match.group(1)

        if '|' in inner:
            target, display = inner.split('|', 1)
        else:
            target, display = inner, None

        target = target.strip()
        display = display.strip() if display else None

        # Strip any path prefix and .md extension
        target_stem = target.split('/')[-1]
        target_stem = re.sub(r'\.md$', '', target_stem)

        # README maps to the blog index
        if target_stem.upper() == 'README':
            url = '../' if not index_mode else './'
            label = display or 'Contents'
            return f'[{label}]({url})'

        # Look up slug in our map
        slug = slug_map.get(target_stem)
        if slug is None:
            # Try slugifying directly as a fallback
            slug = filename_to_slug(target_stem)

        url = f'posts/{slug}/' if index_mode else f'../{slug}/'
        label = display or slugify(target_stem).replace('-', ' ').title()
        return f'[{label}]({url})'

    pattern = r'\[\[([^\]]+)\]\]'
    return re.sub(pattern, replace_wikilink, content)


# ---------------------------------------------------------------------------
# Per-file conversion
# ---------------------------------------------------------------------------

def convert_file(
    src_path: Path,
    dest_path: Path,
    slug_map: dict,
    available_assets: set,
    report: dict,
):
    raw = src_path.read_text(encoding='utf-8')

    # Split front matter from body
    fm_match = re.match(r'^---\n(.*?)\n---\n(.*)', raw, re.DOTALL)
    if fm_match:
        fm_text = fm_match.group(1)
        body = fm_match.group(2)
    else:
        fm_text = ''
        body = raw

    # Derive slug and title
    slug = filename_to_slug(src_path.stem)
    title = extract_title(body)

    # Process front matter
    if fm_text:
        clean_fm = process_front_matter(fm_text, slug, title)
        fm_block = f'---\n{clean_fm}\n---\n'
    else:
        fm_block = f'---\nslug: "{slug}"\ntitle: "{title}"\n---\n'

    # Convert image embeds
    body, missing = convert_image_embeds(body, available_assets)
    if missing:
        report['missing_images'][src_path.name] = missing

    # Convert wikilinks
    body = convert_wikilinks(body, slug_map)

    dest_path.write_text(fm_block + body, encoding='utf-8')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Convert Obsidian travel blog to Hugo format.')
    parser.add_argument('--source', required=True, help='Path to the Obsidian folder')
    parser.add_argument('--output', default='./hugo-output', help='Output directory (default: ./hugo-output)')
    args = parser.parse_args()

    src_dir = Path(args.source).expanduser().resolve()
    out_dir = Path(args.output).resolve()

    if not src_dir.exists():
        print(f'ERROR: Source directory not found: {src_dir}')
        sys.exit(1)

    posts_dir = out_dir / 'content' / 'posts'
    assets_out = out_dir / 'static' / 'assets'
    posts_dir.mkdir(parents=True, exist_ok=True)
    assets_out.mkdir(parents=True, exist_ok=True)

    # Build slug map from all .md files (excluding README)
    md_files = [f for f in src_dir.glob('*.md') if f.stem.upper() != 'README']
    slug_map = {f.stem: filename_to_slug(f.stem) for f in md_files}

    # Build set of available asset filenames
    assets_src = src_dir / 'assets'
    available_assets = set()
    if assets_src.exists():
        available_assets = {f.name for f in assets_src.iterdir() if f.is_file()}
        print(f'Found {len(available_assets)} assets.')

    report = {'missing_images': {}, 'converted': [], 'skipped': []}

    # Convert each .md file
    for src_path in sorted(md_files):
        slug = filename_to_slug(src_path.stem)
        dest_filename = f'{slug}.md'
        dest_path = posts_dir / dest_filename

        try:
            convert_file(src_path, dest_path, slug_map, available_assets, report)
            report['converted'].append(f'{src_path.name} -> {dest_filename}')
            print(f'  ✓  {src_path.name}')
        except Exception as e:
            report['skipped'].append(f'{src_path.name}: {e}')
            print(f'  ✗  {src_path.name}: {e}')

    # Handle README -> content/_index.md (site homepage)
    readme = src_dir / 'README.md'
    if readme.exists():
        raw = readme.read_text(encoding='utf-8')
        raw, _ = convert_image_embeds(raw, available_assets)
        raw = convert_wikilinks(raw, slug_map, index_mode=True)
        content_dir = out_dir / 'content'
        index_path = content_dir / '_index.md'
        index_path.write_text(raw, encoding='utf-8')
        # Minimal section index so Hugo can list posts at /posts/
        (posts_dir / '_index.md').write_text('---\ntitle: Posts\n---\n', encoding='utf-8')
        print(f'  ✓  README.md -> _index.md')

    # Copy assets
    if assets_src.exists():
        for asset in assets_src.iterdir():
            if asset.is_file():
                shutil.copy2(asset, assets_out / asset.name)
        print(f'\nCopied {len(available_assets)} assets to {assets_out}')

    # Print report
    print(f'\n{"="*60}')
    print(f'CONVERSION COMPLETE')
    print(f'{"="*60}')
    print(f'Posts converted : {len(report["converted"])}')
    print(f'Posts skipped   : {len(report["skipped"])}')

    if report['missing_images']:
        print(f'\n⚠️  MISSING IMAGES (referenced in posts but not in assets/):')
        for post, images in report['missing_images'].items():
            print(f'  {post}:')
            for img in images:
                print(f'    - {img}')
    else:
        print('\n✅ All image references resolved — no missing assets.')

    if report['skipped']:
        print(f'\n⚠️  SKIPPED FILES:')
        for s in report['skipped']:
            print(f'  {s}')

    print(f'\nOutput written to: {out_dir}')
    print(f'\nNext steps:')
    print(f'  1. Install Hugo: https://gohugo.io/installation/')
    print(f'  2. Create a new Hugo site and copy content/ and static/ into it')
    print(f'  3. Pick a theme from https://themes.gohugo.io (suggest: PaperMod or Blowfish)')
    print(f'  4. Run: hugo server')


if __name__ == '__main__':
    main()
