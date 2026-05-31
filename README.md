# obsidian-to-hugo

Converts an Obsidian travel blog vault to a Hugo-ready content directory.

## What it does

- **Renames files** to URL-friendly slugs — `13 - Yosemite a Paradise on Earth.md` → `yosemite-a-paradise-on-earth.md`
- **Converts image embeds** — all Obsidian `![[...]]` variants become standard markdown with `/assets/` paths
- **Converts wikilinks** — `[[13 - Yosemite a Paradise on Earth|Next →]]` → `[Next →](../yosemite-a-paradise-on-earth/)`
- **Filters by publish flag** — only notes with `publish: true` in their front matter are converted; others are skipped
- **Cleans front matter** — removes Obsidian-specific fields (`prev`, `next`, `trip`, `chapter`), adds `slug` and `title`
- **Copies assets** — images copied as-is to `static/assets/`
- **Reports missing images** — lists any image references that couldn't be resolved

## Usage

```bash
python3 convert_to_hugo.py \
  --source "/path/to/your-obsidian-vault" \
  --output "./hugo-output"
```

## Output structure

```
hugo-output/
  content/
    posts/        # converted .md files + _index.md from README
  static/
    assets/       # images copied here
```

## Next steps after conversion

1. Install Hugo: https://gohugo.io/installation/
2. Create a new Hugo site and copy `content/` and `static/` into it
3. Pick a theme — [PaperMod](https://github.com/adityatelange/hugo-PaperMod) or [Blowfish](https://blowfish.page) work well
4. Run `hugo server`
