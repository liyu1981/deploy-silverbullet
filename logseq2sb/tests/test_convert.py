#!/usr/bin/env python3
"""Tests for logseq2sb.py (stdlib unittest, no dependencies).

Run with:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logseq2sb import Converter  # noqa: E402


class ConverterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.graph = root / "graph"
        self.out = root / "out"
        (self.graph / "pages").mkdir(parents=True)
        (self.graph / "journals").mkdir()
        (self.graph / "assets").mkdir()
        (self.graph / "assets" / "pic.png").write_bytes(b"png")
        (self.graph / "assets" / "clip_audio_1.mp3").write_bytes(b"mp3")
        (self.graph / "logseq").mkdir()
        (self.graph / "logseq" / "config.edn").write_text("{}")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def convert(self, *args, **kwargs) -> Path:
        Converter(self.graph, self.out, *args, **kwargs).run()
        return self.out

    # -- pages ------------------------------------------------------------- #

    def test_page_properties_become_frontmatter(self) -> None:
        (self.graph / "pages" / "Foo.md").write_text(
            "alias:: fb, Facebook\ntags:: #one #two\ncreated:: 2022-01-01\n"
            "collapsed:: true\n\n- body\n"
        )
        out = self.convert()
        text = (out / "Foo.md").read_text()
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("aliases:\n- fb\n- Facebook\n", text)
        self.assertIn("tags:\n- one\n- two\n", text)
        self.assertIn("created: 2022-01-01", text)
        self.assertNotIn("collapsed", text)
        self.assertTrue(text.rstrip().endswith("- body"))

    def test_title_matching_page_name_is_dropped(self) -> None:
        (self.graph / "pages" / "Some Page.md").write_text("title:: Some Page\n\n- x\n")
        out = self.convert()
        self.assertFalse((out / "Some Page.md").read_text().startswith("---"))

    # -- tasks ------------------------------------------------------------- #

    def test_task_markers_priorities_and_dates(self) -> None:
        (self.graph / "pages" / "Tasks.md").write_text(
            "- TODO do thing [#A] SCHEDULED: <2023-02-01 Wed>\n"
            "- DONE finished\n"
            "- LATER maybe\n"
            "- CANCELED nope\n"
            "- DOING in flight\n"
        )
        text = (self.convert() / "Tasks.md").read_text()
        self.assertIn("- [ ] do thing [priority: A] [scheduled: 2023-02-01]", text)
        self.assertIn("- [x] finished", text)
        self.assertIn("- [LATER] maybe", text)
        self.assertIn("- [CANCELED] nope", text)
        self.assertIn("- [DOING] in flight", text)
        self.assertNotIn("TODO", text)

    # -- references -------------------------------------------------------- #

    def test_block_ids_become_anchors_and_refs_resolve(self) -> None:
        (self.graph / "pages" / "Target.md").write_text(
            "- referenced block\n  id:: 642a60c2-f7e1-4d2b-ab45-23f1e2ca96e2\n- other\n"
        )
        (self.graph / "pages" / "Source.md").write_text(
            "- ((642a60c2-f7e1-4d2b-ab45-23f1e2ca96e2))\n"
            "- ((11111111-1111-1111-1111-111111111111))\n"
        )
        converter = Converter(self.graph, self.out)
        converter.run()
        target = (self.out / "Target.md").read_text()
        source = (self.out / "Source.md").read_text()
        self.assertIn("$logseq-642a60c2-f7e1-4d2b-ab45-23f1e2ca96e2", target)
        self.assertIn("[[$logseq-642a60c2-f7e1-4d2b-ab45-23f1e2ca96e2]]", source)
        # Unknown uuid is left untouched and reported.
        self.assertIn("((11111111-1111-1111-1111-111111111111))", source)
        self.assertEqual(converter.report.counters["block-refs:unresolved"], 1)

    def test_date_links_point_at_journal_pages(self) -> None:
        (self.graph / "pages" / "Diary.md").write_text(
            "- [[May 3rd, 2023]] and [[May 3rd, 2023|that day]]\n"
        )
        text = (self.convert() / "Diary.md").read_text()
        self.assertIn("[[Journal/2023-05-03]]", text)
        self.assertIn("[[Journal/2023-05-03|that day]]", text)

    def test_multiword_hashtag(self) -> None:
        (self.graph / "pages" / "Tag.md").write_text("- hi #[[Some Project]]\n")
        text = (self.convert() / "Tag.md").read_text()
        self.assertIn("#<Some Project>", text)

    # -- journals ---------------------------------------------------------- #

    def test_journals_get_prefixed_and_renamed(self) -> None:
        (self.graph / "journals" / "2023_01_02.md").write_text("- entry\n")
        (self.graph / "journals" / "2023_01_02(1).md").write_text("- conflict entry\n")
        out = self.convert()
        self.assertTrue((out / "Journal" / "2023-01-02.md").exists())
        journal = (out / "Journal" / "2023-01-02.md").read_text()
        self.assertIn("tags:", journal)
        self.assertIn("- journal", journal)
        self.assertIn('date: "2023-01-02"', journal)
        self.assertTrue((out / "Journal" / "2023-01-02 (copy 1).md").exists())

    def test_journal_tag_merges_with_existing_tags(self) -> None:
        (self.graph / "journals" / "2023-01-02.md").write_text(
            "tags:: work\n\n- entry\n"
        )
        out = self.convert()
        text = (out / "Journal" / "2023-01-02.md").read_text()
        self.assertIn("- work", text)
        self.assertIn("- journal", text)

    def test_custom_journal_prefix(self) -> None:
        (self.graph / "journals" / "2023-01-02.md").write_text("- entry\n")
        out = self.convert(journal_prefix="Daily")
        self.assertTrue((out / "Daily" / "2023-01-02.md").exists())

    # -- assets ------------------------------------------------------------ #

    def test_asset_links_are_relative_to_page_depth(self) -> None:
        (self.graph / "pages" / "Root.md").write_text("- ![p](../assets/pic.png)\n")
        (self.graph / "journals" / "2023_01_02.md").write_text("- ![p](../assets/pic.png)\n")
        out = self.convert()
        self.assertIn("![p](assets/pic.png)", (out / "Root.md").read_text())
        self.assertIn(
            "![p](../assets/pic.png)", (out / "Journal" / "2023-01-02.md").read_text()
        )
        self.assertTrue((out / "assets" / "pic.png").exists())

    def test_asset_name_mismatch_is_fuzzy_matched(self) -> None:
        (self.graph / "pages" / "Fuzzy.md").write_text(
            "- ![a](../assets/clip.audio:1.mp3)\n"
        )
        out = self.convert()
        self.assertIn("assets/clip_audio_1.mp3", (out / "Fuzzy.md").read_text())

    # -- macros / blocks --------------------------------------------------- #

    def test_macros(self) -> None:
        (self.graph / "pages" / "Macros.md").write_text(
            "- {{video https://youtu.be/abc}}\n"
            "- {{embed [[Other Page]]}}\n"
            "- {{renderer :todomaster}}\n"
            "- {{mystery thing}}\n"
        )
        converter = Converter(self.graph, self.out)
        converter.run()
        text = (self.out / "Macros.md").read_text()
        self.assertIn("[video](https://youtu.be/abc)", text)
        self.assertIn("![[Other Page]]", text)
        self.assertNotIn("todomaster", text)
        self.assertIn("{{mystery thing}}", text)
        self.assertEqual(converter.report.counters["macros:kept"], 1)

    def test_quote_block(self) -> None:
        (self.graph / "pages" / "Quote.md").write_text(
            "- before\n"
            "\t- #+BEGIN_QUOTE\n"
            "\t  quoted line\n"
            "\t  second line\n"
            "\t  #+END_QUOTE\n"
        )
        text = (self.convert() / "Quote.md").read_text()
        self.assertIn("  - > quoted line", text)
        self.assertIn("    > second line", text)
        self.assertNotIn("#+BEGIN_QUOTE", text)

    def test_logbook_becomes_nested_bullets(self) -> None:
        (self.graph / "pages" / "Log.md").write_text(
            "- task\n"
            "\t:LOGBOOK:\n"
            "\tCLOCK: [2022-07-25 Mon 22:41:03]\n"
            "\t:END:\n"
        )
        text = (self.convert() / "Log.md").read_text()
        self.assertIn("  - CLOCK: [2022-07-25 Mon 22:41:03]", text)
        self.assertNotIn(":LOGBOOK:", text)
        self.assertNotIn(":END:", text)

    def test_code_fences_are_not_rewritten(self) -> None:
        (self.graph / "pages" / "Code.md").write_text(
            "- ```\n  DONE not a task [[May 3rd, 2023]]\n  ```\n"
        )
        text = (self.convert() / "Code.md").read_text()
        self.assertIn("DONE not a task [[May 3rd, 2023]]", text)

    # -- output ------------------------------------------------------------ #

    def test_report_is_written(self) -> None:
        (self.graph / "pages" / "A.md").write_text("- x\n")
        out = self.convert()
        self.assertTrue((out / "conversion-report.md").exists())
        self.assertIn("Pages converted: 1", (out / "conversion-report.md").read_text())

    def test_dry_run_writes_nothing(self) -> None:
        (self.graph / "pages" / "A.md").write_text("- x\n")
        converter = Converter(self.graph, self.out, dry_run=True)
        converter.run()
        self.assertFalse(self.out.exists())

    def test_index_from(self) -> None:
        (self.graph / "pages" / "contents.md").write_text("- hello\n")
        out = self.convert(index_from="contents")
        self.assertEqual((out / "index.md").read_text(), (out / "contents.md").read_text())


if __name__ == "__main__":
    unittest.main()
