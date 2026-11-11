#!/usr/bin/env python3
"""Convert a Logseq Markdown graph into a SilverBullet space.

Usage:
    ./logseq2sb.py ~/path/to/logseq-graph ~/path/to/silverbullet-space

The converter is intentionally conservative: anything it cannot translate with
confidence is left as-is and listed in ``conversion-report.md`` (inside the
output folder) together with the file and line number.

Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

# --------------------------------------------------------------------------- #
# Mapping tables
# --------------------------------------------------------------------------- #

MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may", "may"),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for name in names
}

# Logseq marker -> SilverBullet task state inside "- [ ]".
TASK_STATES = {
    "TODO": " ",
    "DOING": "DOING",
    "NOW": "NOW",
    "LATER": "LATER",
    "WAITING": "WAITING",
    "WAIT": "WAITING",
    "DONE": "x",
    "CANCELED": "CANCELED",
    "CANCELLED": "CANCELED",
    "IN-PROGRESS": "IN PROGRESS",
}

# Page/block properties that only make sense inside Logseq.
DROPPED_PROPERTIES = {
    "id",
    "collapsed",
    "background-color",
    "icon",
    "created-at",
    "updated-at",
    "template",
    "template-including-parent",
    "exclude-from-graph-view",
    "public",
    "hl-color",
    "query-table",
    "query-properties",
    "query-sort-by",
    "query-sort-desc",
}

PROPERTY_ALIASES = {
    "alias": "aliases",
    "aliases": "aliases",
    "tag": "tags",
    "tags": "tags",
}

UUID_RE = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
BLOCK_REF_RE = re.compile(r"\(\((" + UUID_RE + r")\)\)")
BLOCK_ID_RE = re.compile(r"^\s*id::\s*(" + UUID_RE + r")\s*$")
DATE_LINK_RE = re.compile(
    r"\[\[([A-Za-z]{3,9}) (\d{1,2})(?:st|nd|rd|th)?, (\d{4})(\|[^\]]*)?\]\]"
)
PROPERTY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_/-]*)::\s*(.*)$")
MACRO_RE = re.compile(r"\{\{([^{}]*)\}\}")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})\s*(\S*)\s*$")
BULLET_RE = re.compile(r"^- (.*)$|^-$")


def anchor_name(uuid: str) -> str:
    """SilverBullet anchor for a Logseq block UUID.

    Anchors must start with a letter/underscore, UUIDs often start with a
    digit, hence the prefix.
    """
    return "logseq-" + uuid


def is_true(value: str) -> bool:
    return value.strip().lower() in {"true", "yes", "1"}


def expand_leading_ws(line: str) -> str:
    """Turn tab indentation into two spaces per tab (Logseq's default depth)."""
    i = 0
    while i < len(line) and line[i] in " \t":
        i += 1
    head = line[:i].replace("\t", "  ")
    return head + line[i:]


def yaml_scalar(value: str) -> str:
    """Render a string as a YAML scalar (JSON quoting is valid YAML)."""
    plain = re.fullmatch(r"[A-Za-z0-9_@][A-Za-z0-9_@./ +()-]*", value)
    reserved = value.strip().lower() in {
        "true", "false", "null", "yes", "no", "on", "off", "~", "",
    }
    numeric = re.fullmatch(r"-?\d+(?:\.\d+)?", value.strip())
    if plain and not reserved and not numeric:
        return value
    return json.dumps(value, ensure_ascii=False)


def split_list(value: str) -> list[str]:
    """Logseq accepts 'a, b' as well as '#a #b'."""
    out: list[str] = []
    for part in re.split(r"[,\s]+", value):
        part = part.strip().strip("#").strip()
        if part:
            out.append(part)
    return out


def cut(text: str, start: int, end: int) -> str:
    """Remove text[start:end] and keep the surrounding words single-spaced."""
    left, right = text[:start].rstrip(), text[end:].lstrip()
    if left and right:
        return f"{left} {right}"
    return left or right


def extract_url(text: str) -> str | None:
    # {{video [(70) foo](https://youtu.be/x)}}
    md = re.search(r"\]\((https?://[^)\s]+)\)", text)
    if md:
        return md.group(1)
    plain = re.search(r"https?://\S+", text)
    if plain:
        return plain.group(0).rstrip(")" ).rstrip("}")
    return None


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


class Report:
    def __init__(self) -> None:
        self.counters: Counter[str] = Counter()
        self.warnings: list[tuple[str, int, str]] = []
        self.skipped: list[tuple[str, str]] = []

    def count(self, key: str, amount: int = 1) -> None:
        self.counters[key] += amount

    def warn(self, path: str, line: int, message: str) -> None:
        self.warnings.append((path, line, message))


# --------------------------------------------------------------------------- #
# Converter
# --------------------------------------------------------------------------- #


class Converter:
    def __init__(
        self,
        graph: Path,
        out: Path,
        *,
        journal_prefix: str = "Journal/",
        copy_assets: bool = True,
        legacy_namespaces: bool = False,
        index_from: str | None = None,
        journal_tag: str = "journal",
        dry_run: bool = False,
    ) -> None:
        self.graph = graph
        self.out = out
        self.journal_prefix = journal_prefix if not journal_prefix or journal_prefix.endswith("/") else journal_prefix + "/"
        self.copy_assets = copy_assets
        self.legacy_namespaces = legacy_namespaces
        self.index_from = index_from
        self.journal_tag = journal_tag
        self.dry_run = dry_run
        self.report = Report()
        self.ids: dict[str, str] = {}  # logseq uuid -> anchor name
        self.asset_refs: set[str] = set()
        self.asset_index: dict[str, str] = {}  # normalised name -> real name
        self.written: list[Path] = []

    # -- pages ------------------------------------------------------------- #

    def page_files(self) -> list[Path]:
        root = self.graph / "pages"
        if not root.is_dir():
            return []
        return sorted(p for p in root.rglob("*.md") if p.is_file())

    def journal_files(self) -> list[Path]:
        root = self.graph / "journals"
        if not root.is_dir():
            return []
        return sorted(p for p in root.glob("*.md") if p.is_file())

    def page_name(self, rel: Path) -> str:
        name = rel.with_suffix("").as_posix()
        # Logseq URL-encodes namespaces in file names.
        name = name.replace("%2F", "/").replace("%2f", "/")
        if self.legacy_namespaces:
            name = name.replace("___", "/")
        return name

    @staticmethod
    def journal_date(stem: str) -> str | None:
        for fmt in ("%Y_%m_%d", "%Y-%m-%d"):
            try:
                return dt.datetime.strptime(stem, fmt).date().isoformat()
            except ValueError:
                continue
        return None

    @staticmethod
    def journal_duplicate(stem: str) -> tuple[str, str] | None:
        """Sync conflicts: '2023_04_12(1)' -> ('2023-04-12', '1')."""
        match = re.fullmatch(r"(\d{4})[_-](\d{2})[_-](\d{2})\s*\((\d+)\)", stem)
        if not match:
            return None
        year, month, day, copy = match.groups()
        try:
            date = dt.date(int(year), int(month), int(day)).isoformat()
        except ValueError:
            return None
        return date, copy

    # -- scanning ---------------------------------------------------------- #

    @staticmethod
    def normalise(name: str) -> str:
        """Loose file name comparison (iCloud/Dropbox renames ':' to '_')."""
        return re.sub(r"[^0-9a-z]+", "", name.lower())

    def scan_assets(self) -> None:
        root = self.graph / "assets"
        if not root.is_dir():
            return
        seen: dict[str, str | None] = {}
        for item in sorted(root.rglob("*")):
            if not item.is_file():
                continue
            key = self.normalise(item.name)
            seen[key] = None if key in seen else item.relative_to(root).as_posix()
        self.asset_index = {k: v for k, v in seen.items() if v is not None}

    def resolve_asset(self, rel: str) -> str | None:
        root = self.graph / "assets"
        if (root / rel).exists():
            return rel
        return self.asset_index.get(self.normalise(rel.split("/")[-1]))

    def scan_ids(self) -> None:
        for path in (*self.page_files(), *self.journal_files()):
            text = path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                match = BLOCK_ID_RE.match(line)
                if match:
                    uuid = match.group(1).lower()
                    self.ids.setdefault(uuid, anchor_name(uuid))

    # -- inline transforms ------------------------------------------------- #

    def inline(self, text: str, path: str, lineno: int, depth: int) -> str:
        text = self.macros(text, path, lineno)
        text = self.block_refs(text, path, lineno)
        text = self.hashtags(text)
        text = self.date_links(text)
        text = self.assets(text, depth)
        return text

    def macros(self, text: str, path: str, lineno: int) -> str:
        def replace(match: re.Match[str]) -> str:
            body = match.group(1).strip()
            if not body:
                return match.group(0)
            name, _, arg = body.partition(" ")
            name = name.lower()
            arg = arg.strip()

            if name == "embed":
                inner = arg.strip()
                ref = BLOCK_REF_RE.search(inner)
                if ref:
                    uuid = ref.group(1).lower()
                    if uuid in self.ids:
                        self.report.count("macros:embed-block")
                        return "![[$" + self.ids[uuid] + "]]"
                page = re.fullmatch(r"\[\[([^\]]+)\]\]", inner)
                if page:
                    self.report.count("macros:embed-page")
                    return "![[" + page.group(1) + "]]"
                self.report.warn(path, lineno, f"could not convert embed macro: {body}")
                return match.group(0)

            if name in {"video", "youtube"}:
                url = extract_url(arg)
                if url:
                    self.report.count("macros:video")
                    return f"[video]({url})"
                self.report.warn(path, lineno, f"video macro without URL: {body}")
                return match.group(0)

            if name == "renderer":
                self.report.count("macros:renderer-removed")
                self.report.warn(path, lineno, f"Logseq renderer removed: {body}")
                return ""

            if name in {"query", "advanced-query", "query-table"}:
                self.report.count("macros:query-not-converted")
                self.report.warn(path, lineno, f"Logseq query left as comment: {body[:60]}")
                return f"<!-- logseq {name} not converted: {body} -->"

            if name == "img":
                url = extract_url(arg) or arg.strip()
                self.report.count("macros:img")
                return f"![image]({url})"

            if name in {"pdf", "tweet", "cloze"}:
                url = extract_url(arg) or arg.strip()
                self.report.count(f"macros:{name}")
                label = {"pdf": "pdf", "tweet": "tweet", "cloze": "cloze"}[name]
                return f"[{label}]({url})"

            self.report.count("macros:kept")
            self.report.warn(path, lineno, f"unknown macro kept as-is: {body[:60]}")
            return match.group(0)

        return MACRO_RE.sub(replace, text)

    def block_refs(self, text: str, path: str, lineno: int) -> str:
        def replace(match: re.Match[str]) -> str:
            uuid = match.group(1).lower()
            anchor = self.ids.get(uuid)
            if anchor:
                self.report.count("block-refs:resolved")
                return f"[[${anchor}]]"
            self.report.count("block-refs:unresolved")
            self.report.warn(
                path, lineno, f"block reference has no matching id:: block: (({uuid}))"
            )
            return match.group(0)

        return BLOCK_REF_RE.sub(replace, text)

    def hashtags(self, text: str) -> str:
        def replace(match: re.Match[str]) -> str:
            self.report.count("hashtags:rewritten")
            return "#<" + match.group(1) + ">"

        return re.sub(r"#\[\[([^\]]+)\]\]", replace, text)

    def date_links(self, text: str) -> str:
        def replace(match: re.Match[str]) -> str:
            month_name, day, year, alias = match.groups()
            month = MONTHS.get(month_name.lower())
            if not month:
                return match.group(0)
            try:
                date = dt.date(int(year), month, int(day)).isoformat()
            except ValueError:
                return match.group(0)
            self.report.count("date-links:rewritten")
            return f"[[{self.journal_prefix}{date}{alias or ''}]]"

        return DATE_LINK_RE.sub(replace, text)

    def assets(self, text: str, depth: int) -> str:
        """Rewrite Logseq asset links for the page's depth in the new space."""

        def replace(match: re.Match[str]) -> str:
            prefix, target = match.group(1), match.group(2)
            if target.startswith(("http://", "https://", "mailto:", "data:")):
                return match.group(0)
            normalised = target.replace("\\", "/")
            if "/assets/" in normalised or normalised.startswith("assets/"):
                rel = normalised.split("/assets/", 1)[-1]
                resolved = self.resolve_asset(rel)
                if resolved is None:
                    self.asset_refs.add(rel)
                    self.report.count("assets:missing")
                    return match.group(0)
                if resolved != rel:
                    self.report.count("assets:fuzzy-resolved")
                self.asset_refs.add(resolved)
                new_target = "../" * depth + "assets/" + resolved
                self.report.count("assets:rewritten")
                return f"{prefix}{new_target})"
            return match.group(0)

        return re.sub(r"(!?\[[^\]]*\]\()([^)]+)\)", replace, text)

    # -- line-level transforms --------------------------------------------- #

    def bullet(self, text: str, path: str, lineno: int) -> str:
        changed = False
        marker = re.match(r"^(TODO|DOING|NOW|LATER|WAITING|WAIT|DONE|CANCELED|CANCELLED|IN-PROGRESS)\s+(.*)$", text, re.S)
        state = None
        if marker:
            state = TASK_STATES[marker.group(1)]
            text = marker.group(2)
            changed = True

        # [#A] priority -> [priority: A]
        priority = re.search(r"\[#([A-C])\]", text)
        if priority:
            text = cut(text, priority.start(), priority.end())
            text = f"{text} [priority: {priority.group(1)}]".strip()
            changed = True
            self.report.count("tasks:priority")

        # SCHEDULED: <2023-01-01 Mon> / DEADLINE: <...>
        for key, attr in (("SCHEDULED", "scheduled"), ("DEADLINE", "due")):
            match = re.search(rf"{key}:\s*<([^>]+)>", text)
            if match:
                date = match.group(1).split()[0]
                text = cut(text, match.start(), match.end())
                text = f"{text} [{attr}: {date}]".strip()
                changed = True
                self.report.count(f"tasks:{attr}")

        if state is not None:
            self.report.count("tasks:converted")
            return f"[{state}] {text}".rstrip()
        if changed:
            return text
        return text

    # -- file conversion ---------------------------------------------------- #

    def convert_text(
        self,
        raw: str,
        page_name: str,
        path_label: str,
        depth: int,
        journal_date: str | None = None,
    ) -> str:
        lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        props, start = self.page_properties(lines, page_name)
        body = lines[start:]

        front = self.frontmatter(props, page_name, journal_date)
        converted = self.body(body, path_label, depth)
        text = front + "\n".join(converted).rstrip() + "\n"
        return text

    def page_properties(self, lines: list[str], page_name: str) -> tuple[list[tuple[str, str]], int]:
        props: list[tuple[str, str]] = []
        i = 0
        while i < len(lines):
            match = PROPERTY_RE.match(lines[i])
            if not match:
                break
            props.append((match.group(1), match.group(2).strip()))
            i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        return props, i

    def frontmatter(
        self,
        props: list[tuple[str, str]],
        page_name: str,
        journal_date: str | None = None,
    ) -> str:
        values: dict[str, object] = {}
        for key, value in props:
            key_l = key.lower()
            if key_l in DROPPED_PROPERTIES:
                continue
            if key_l == "title":
                if value.strip().lower() == page_name.strip().lower():
                    continue
                values["title"] = value
                continue
            if key_l in PROPERTY_ALIASES:
                values[PROPERTY_ALIASES[key_l]] = split_list(value)
                continue
            values[key] = value

        if journal_date:
            tags = values.get("tags")
            if not tags:
                tags = []
            elif isinstance(tags, str):
                tags = split_list(tags)
            if self.journal_tag not in tags:
                tags.append(self.journal_tag)
            values["tags"] = tags
            values["date"] = journal_date

        if not values:
            return ""

        out = ["---"]
        for key, value in values.items():
            if isinstance(value, list):
                if not value:
                    out.append(f"{key}: []")
                    continue
                out.append(f"{key}:")
                out.extend(f"- {yaml_scalar(item)}" for item in value)
            elif key == "date":
                out.append(f"date: {json.dumps(str(value))}")
            else:
                out.append(f"{key}: {yaml_scalar(str(value))}")
        out.append("---")
        return "\n".join(out) + "\n"

    def body(self, lines: list[str], path: str, depth: int) -> list[str]:
        out: list[str] = []
        stack: list[tuple[int, int]] = []  # (indent, index into out)
        fence: tuple[str, int] | None = None
        quote: dict[str, object] | None = None
        drawer: dict[str, int] | None = None

        for offset, raw_line in enumerate(lines):
            lineno = offset + 1
            line = expand_leading_ws(raw_line.rstrip())
            stripped = line.lstrip(" ")
            indent = len(line) - len(stripped)

            # --- fenced code (verbatim) ---
            if fence is not None:
                out.append(line)
                char, length = fence
                if re.match(rf"^{re.escape(char)}{{{length},}}\s*$", stripped):
                    fence = None
                continue

            # --- quote block ---
            if quote is not None:
                if stripped == "#+END_QUOTE":
                    quote = None
                    continue
                base = int(quote["base"])  # type: ignore[arg-type]
                rel = " " * max(0, indent - base) + stripped
                owner = quote["owner"]
                if owner is None:
                    out.append(" " * indent + "> " + rel if rel else ">")
                elif quote["first"]:
                    owner_indent = int(quote["indent"])  # type: ignore[arg-type]
                    out[int(owner)] = " " * owner_indent + "- > " + rel  # type: ignore[index]
                    quote["first"] = False
                else:
                    owner_indent = int(quote["indent"])  # type: ignore[arg-type]
                    out.append(" " * (owner_indent + 2) + "> " + rel if rel else " " * (owner_indent + 2) + ">")
                continue

            # --- fence opener ---
            fence_match = FENCE_RE.match(stripped)
            if fence_match and not BULLET_RE.match(stripped):
                marker = fence_match.group(1)
                fence = (marker[0], len(marker))
                out.append(line)
                continue

            # --- bullet ---
            bullet_match = BULLET_RE.match(stripped)
            if bullet_match:
                text = bullet_match.group(1) or ""
                while stack and stack[-1][0] >= indent:
                    stack.pop()

                if text.strip() == "#+BEGIN_QUOTE":
                    self.report.count("quotes:converted")
                    out.append(" " * indent + "-")
                    quote = {
                        "owner": len(out) - 1,
                        "indent": indent,
                        "base": indent + 2,
                        "first": True,
                    }
                    continue

                text = self.bullet(text, path, lineno)
                text = self.inline(text, path, lineno, depth).rstrip()
                out.append(" " * indent + "- " + text if text else " " * indent + "-")
                stack.append((indent, len(out) - 1))

                nested = FENCE_RE.match(text.strip())
                if nested:
                    marker = nested.group(1)
                    fence = (marker[0], len(marker))
                continue

            # --- property drawer / logbook ---
            if stripped in {":LOGBOOK:", ":PROPERTIES:"}:
                drawer = {"parent": stack[-1][0] if stack else max(0, indent - 2)}
                continue
            if stripped == ":END:" and drawer is not None:
                drawer = None
                continue
            if drawer is not None:
                parent = drawer["parent"]
                out.append(" " * (parent + 2) + "- " + stripped)
                self.report.count("logbook:converted")
                continue

            # --- block properties ---
            prop_match = PROPERTY_RE.match(stripped)
            if prop_match and indent > 0 and stack:
                key, value = prop_match.group(1), prop_match.group(2).strip()
                parent_index = stack[-1][1]
                if key.lower() == "id":
                    anchor = self.ids.get(value.lower())
                    if anchor:
                        out[parent_index] = out[parent_index].rstrip() + " $" + anchor
                        self.report.count("anchors:created")
                    else:
                        self.report.warn(path, lineno, f"unparsable block id: {value}")
                elif key.lower() in DROPPED_PROPERTIES:
                    continue
                else:
                    out[parent_index] = (
                        out[parent_index].rstrip() + f" [{key}:: {value}]"
                    )
                    self.report.count("properties:converted")
                continue

            # --- continuation / plain line ---
            out.append(
                line if not stripped else self.inline(line, path, lineno, depth).rstrip()
            )

        return out

    # -- driver ------------------------------------------------------------- #

    def run(self) -> Report:
        if not self.graph.is_dir():
            raise SystemExit(f"error: graph folder not found: {self.graph}")

        self.scan_ids()
        self.scan_assets()

        for path in self.page_files():
            rel = path.relative_to(self.graph / "pages")
            name = self.page_name(rel)
            label = f"pages/{rel.as_posix()}"
            text = self.convert_text(
                path.read_text(encoding="utf-8", errors="replace"),
                name,
                label,
                depth=len(name.split("/")) - 1,
            )
            target = self.out / (name + ".md")
            self.write(target, text)
            self.report.count("pages:converted")

        for path in self.journal_files():
            date = self.journal_date(path.stem)
            duplicate = self.journal_duplicate(path.stem)
            if date is None and duplicate is not None:
                date, copy = duplicate
                name = f"{self.journal_prefix}{date} (copy {copy})"
                self.report.warn(
                    f"journals/{path.name}",
                    0,
                    f"sync-conflict copy of {date}; converted to '{name}' — merge by hand",
                )
            elif date is None:
                self.report.warn(
                    f"journals/{path.name}", 0, "journal file name is not a date; copied as a page"
                )
                name = self.journal_prefix + path.stem
            else:
                name = self.journal_prefix + date
            text = self.convert_text(
                path.read_text(encoding="utf-8", errors="replace"),
                name,
                f"journals/{path.name}",
                depth=len(name.split("/")) - 1,
                journal_date=date,
            )
            self.write(self.out / (name + ".md"), text)
            self.report.count("journals:converted")

        if self.copy_assets:
            self.copy_tree("assets", required=False)
            self.copy_tree("draws", required=False)

        for name in ("whiteboards", "logseq", ".recycle"):
            if (self.graph / name).exists():
                self.report.skipped.append((name + "/", "Logseq-internal, not converted"))

        self.check_assets()
        self.write_index()

        if not self.dry_run:
            self.out.mkdir(parents=True, exist_ok=True)
            (self.out / "conversion-report.md").write_text(
                self.render_report(), encoding="utf-8", newline="\n"
            )

        return self.report

    def write(self, target: Path, text: str) -> None:
        if self.dry_run:
            self.written.append(target)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        self.written.append(target)

    def copy_tree(self, name: str, required: bool) -> None:
        source = self.graph / name
        if not source.is_dir():
            if required:
                raise SystemExit(f"error: missing {name}/ folder in {self.graph}")
            return
        target = self.out / name
        if not self.dry_run:
            target.mkdir(parents=True, exist_ok=True)
        count = 0
        for item in sorted(source.rglob("*")):
            if item.is_file():
                destination = target / item.relative_to(source)
                if not self.dry_run:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, destination)
                count += 1
        self.report.count(f"copied:{name}", count)

    def check_assets(self) -> None:
        if not self.copy_assets:
            return
        assets = self.graph / "assets"
        for rel in sorted(self.asset_refs):
            if not (assets / rel).exists():
                self.report.warn("assets", 0, f"referenced asset not found: assets/{rel}")

    def write_index(self) -> None:
        if not self.index_from:
            return
        source = self.out / (self.index_from + ".md")
        if not source.exists():
            self.report.warn("index", 0, f"index source page not found: {self.index_from}")
            return
        if not self.dry_run:
            (self.out / "index.md").write_text(
                source.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
            )
        self.report.count("index:created")

    def render_report(self) -> str:
        counters = self.report.counters
        lines = [
            "# Logseq → SilverBullet conversion report",
            "",
            f"* Graph: `{self.graph}`",
            f"* Output: `{self.out}`",
            f"* Journal prefix: `{self.journal_prefix}`",
            "",
            "## Summary",
            "",
            f"* Pages converted: {counters['pages:converted']}",
            f"* Journals converted: {counters['journals:converted']}",
            f"* Journal links rewritten: {counters['date-links:rewritten']}",
            f"* Assets copied: {counters['copied:assets']}",
            f"* Asset links rewritten: {counters['assets:rewritten']}"
            f" (fuzzy-matched to existing files: {counters['assets:fuzzy-resolved']}," 
            f" missing: {counters['assets:missing']})",
            f"* Multi-word hashtags rewritten: {counters['hashtags:rewritten']}",
            f"* Tasks converted: {counters['tasks:converted']}",
            f"* Anchors created: {counters['anchors:created']}",
            f"* Block references resolved: {counters['block-refs:resolved']}"
            f" (unresolved: {counters['block-refs:unresolved']})",
            f"* Quote blocks converted: {counters['quotes:converted']}",
            f"* Logbook entries converted: {counters['logbook:converted']}",
            f"* Macros: {counters['macros:video']} video, "
            f"{counters['macros:renderer-removed']} removed renderers, "
            f"{counters['macros:query-not-converted']} queries left as comments, "
            f"{counters['macros:kept']} kept as-is",
        ]
        if counters["copied:draws"]:
            lines.append(f"* Excalidraw drawings copied: {counters['copied:draws']}")

        lines += ["", "## Warnings", ""]
        if self.report.warnings:
            for path, lineno, message in self.report.warnings:
                location = f"{path}:{lineno}" if lineno else path
                lines.append(f"* `{location}` — {message}")
        else:
            lines.append("_None._")

        lines += ["", "## Skipped", ""]
        if self.report.skipped:
            for path, reason in self.report.skipped:
                lines.append(f"* `{path}` — {reason}")
        else:
            lines.append("_None._")

        lines.append("")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="logseq2sb.py",
        description="Convert a Logseq Markdown graph into a SilverBullet space.",
    )
    parser.add_argument("graph", type=Path, help="Logseq graph folder (contains pages/, journals/)")
    parser.add_argument("out", type=Path, help="output folder for the SilverBullet space")
    parser.add_argument(
        "--journal-prefix",
        default="Journal/",
        help="SilverBullet page prefix for daily notes (default: Journal/)",
    )
    parser.add_argument(
        "--journal-tag",
        default="journal",
        help="frontmatter tag marking journal pages (default: journal)",
    )
    parser.add_argument(
        "--no-assets", action="store_true", help="do not copy assets/ and draws/"
    )
    parser.add_argument(
        "--legacy-namespaces",
        action="store_true",
        help="treat '___' in file names as a namespace separator (/), as older Logseq versions do",
    )
    parser.add_argument(
        "--index-from",
        metavar="PAGE",
        help="also write the converted page PAGE as index.md (e.g. contents)",
    )
    parser.add_argument(
        "--force", action="store_true", help="write into a non-empty output folder"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="analyse only; do not write any files"
    )
    args = parser.parse_args(argv)

    graph = args.graph.expanduser().resolve()
    out = args.out.expanduser().resolve()

    if out.exists() and any(out.iterdir()) and not args.force and not args.dry_run:
        parser.error(f"output folder is not empty: {out} (use --force)")

    converter = Converter(
        graph,
        out,
        journal_prefix=args.journal_prefix,
        copy_assets=not args.no_assets,
        legacy_namespaces=args.legacy_namespaces,
        index_from=args.index_from,
        journal_tag=args.journal_tag,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        converter.run()
        sys.stdout.write(converter.render_report())
        return 0

    report = converter.run()
    report_path = out / "conversion-report.md"

    print(f"converted {report.counters['pages:converted']} pages, "
          f"{report.counters['journals:converted']} journals, "
          f"{report.counters['copied:assets']} assets -> {out}")
    if report.warnings:
        print(f"{len(report.warnings)} warning(s), see {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
