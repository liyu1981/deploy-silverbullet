# logseq2sb

Convert a **Logseq Markdown graph** into a **SilverBullet v2 space**.

Single-file Python 3 tool, standard library only. It never modifies the source
graph and writes into a fresh output folder.

```bash
./logseq2sb.py ~/notes/logseq ~/sb-space
./logseq2sb.py ~/notes/logseq ~/sb-space --dry-run   # report only, writes nothing
```

Then point SilverBullet at the output folder (or copy it into `~/.silverbullet`):

```bash
./logseq2sb.py /path/to/graph ~/.silverbullet --force
```

## What it does

| Logseq | SilverBullet |
| --- | --- |
| `pages/Foo.md` | `Foo.md` (space root) |
| `journals/2022_07_19.md` / `2022-07-19.md` | `Journal/2022-07-19.md` (prefix configurable with `--journal-prefix`) |
| `pages/a%2Fb.md` (namespace) | `a/b.md` |
| `title::`, `alias::`, `tags::`, … page properties | YAML frontmatter (`title` dropped when it equals the file name) |
| `- TODO text` / `DONE` / `LATER` / `CANCELED` / `DOING` / `NOW` | `- [ ] text`, `- [x] text`, `- [LATER] text`, `- [CANCELED] text`, `- [DOING] text`, `- [NOW] text` |
| `[#A]` priority | `[priority: A]` attribute |
| `SCHEDULED: <2023-02-01 Wed>` / `DEADLINE: <…>` | `[scheduled: 2023-02-01]` / `[due: 2023-02-01]` |
| block with `id:: <uuid>` | block line gets a `$logseq-<uuid>` anchor |
| `((uuid))` block reference | `[[$logseq-<uuid>]]` (left as-is + reported when the target is missing) |
| `{{embed [[Page]]}}` | `![[Page]]` |
| `{{embed ((uuid))}}` | `![[$logseq-<uuid>]]` |
| `{{video URL}}` / `{{youtube URL}}` | `[video](URL)` |
| `{{renderer …}}` | removed (reported) |
| `{{query …}}` | left as an HTML comment (reported) |
| `{{unknown …}}` | kept as-is (reported) |
| `[[May 3rd, 2023]]` date link | `[[Journal/2023-05-03]]` (alias preserved) |
| `#[[Multi word tag]]` | `#<Multi word tag>` (SilverBullet's syntax) |
| `#+BEGIN_QUOTE … #+END_QUOTE` | Markdown blockquote (`> …`), keeps list nesting |
| `:LOGBOOK:` / `CLOCK: …` / `:END:` | nested `- CLOCK: …` bullets |
| `![alt](../assets/x.png)` | `![alt](assets/x.png)` (or `../assets/…` on journal pages) |
| `collapsed::`, `background-color::`, `id::`, … | dropped |
| Tab indentation | two spaces per level |

Logseq asset file names occasionally differ from the links (e.g. iCloud/Dropbox
replaces `:` with `_`); the converter falls back to a normalised name match and
reports how many links it repaired that way.

`assets/` and `draws/` are copied verbatim. `logseq/` and `whiteboards/` are
skipped (listed in the report).

## What needs a human afterwards

Everything that has no mechanical equivalent is **reported** in
`conversion-report.md` (also written into the output folder) with
`file:line`:

* Logseq queries (none in a typical outliner export, but supported as comments)
* `{{renderer …}}` widgets (todo master, kanban, …)
* unknown macros
* block references whose `id::` block is not in the graph
* assets referenced but missing
* sync-conflict journal copies (`2022_07_19(1).md`) — converted to
  `Journal/2022-07-19 (copy 1)`, merge them by hand
* things SilverBullet simply does not have: Logseq whiteboards, SRS cards,
  embedded PDFs with page numbers, graph view settings

## Options

```
positional:
  graph                 Logseq graph folder (contains pages/ and journals/)
  out                   output folder for the SilverBullet space

--journal-prefix P      daily note prefix (default: Journal/)
--no-assets             do not copy assets/ and draws/
--legacy-namespaces     treat '___' in file names as '/' (old Logseq naming)
--index-from PAGE       also write converted PAGE as index.md (e.g. --index-from contents)
--force                 write into a non-empty output folder
--dry-run               analyse and print the report, write nothing
```

## After converting

* SilverBullet's journal commands expect the `Journal/YYYY-MM-DD` convention
  (Workbench → Configuration → Journal if you want another prefix).
* Wiki links, tags, tasks and frontmatter are all native SilverBullet syntax;
  page names come from file names, so no `title` is needed.
* Open a random page or two and check the report before you start editing; the
  conversion is one-shot, so keep the original graph around.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests build a small fixture graph and cover task markers, anchors/block
refs, date links, page properties, assets, macros, quotes, logbooks, journals
and dry-run behaviour.
