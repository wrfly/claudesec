"""prompts/en/*.md say what prompts/*.md say: the same skeleton, rule for rule. Any rule added to
or dropped from one language and not the other fails here.

    python3 -m unittest discover -s tests

Checked per file pair: headings (level and order) and the Cursor markers among them; in each
section, the list items at each indent and the fenced blocks with their line counts; across the
file, {{PLACEHOLDERS}}, inline code without Chinese in it (commands, paths, flags) kept, the number of
inline code spans and of bold spans. The English file has no Chinese at all.
"""
import re
import unittest
from collections import Counter
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
CJK = re.compile(r"[　-〿一-鿿＀-￯]")
MARKER = re.compile(r"<!-- (if cursor|else|end cursor) -->")
ITEM = re.compile(r"^( *)(?:[-*]|\d+\.) \S")


def skeleton(text):
    """[(section head, [items per indent, fenced block line counts])] and file-wide counts."""
    sections, items, fences, in_fence, fence_lines = [], Counter(), [], False, 0
    head = "(top)"

    def close():
        sections.append((head, dict(sorted(items.items())), list(fences)))

    for line in text.splitlines():
        if line.startswith("```"):
            if in_fence:
                fences.append(fence_lines)
            in_fence, fence_lines = not in_fence, 0
            continue
        if in_fence:
            fence_lines += 1
            continue
        h = re.match(r"^(#{1,6}) ", line)
        m = MARKER.match(line)
        if h or m:
            close()
            items, fences = Counter(), []
            head = h[1] if h else m[1]
            continue
        i = ITEM.match(line)
        if i:
            items[len(i[1]) // 2] += 1
    close()
    prose = re.sub(r"```.*?```", "", text, flags=re.S)
    spans = re.findall(r"`([^`\n]+)`", prose)
    return {
        "sections": sections,
        "placeholders": Counter(re.findall(r"\{\{[A-Z_]+\}\}", text)),
        "code": Counter(s for s in spans if not CJK.search(s)),
        "spans": len(spans),
        "bold": len(re.findall(r"\*\*[^*\n]+\*\*", prose)),
    }


class PromptParity(unittest.TestCase):
    def test_every_prompt_has_an_english_twin(self):
        zh = sorted(p.name for p in PROMPTS.glob("*.md"))
        en = sorted(p.name for p in (PROMPTS / "en").glob("*.md"))
        self.assertEqual(zh, en)

    def test_same_skeleton(self):
        for zh in sorted(PROMPTS.glob("*.md")):
            en = PROMPTS / "en" / zh.name
            with self.subTest(prompt=zh.name):
                a, b = skeleton(zh.read_text()), skeleton(en.read_text())
                self.assertEqual([s[0] for s in a["sections"]], [s[0] for s in b["sections"]], "headings / markers")
                for (head, items_a, fences_a), (_, items_b, fences_b) in zip(a["sections"], b["sections"]):
                    self.assertEqual(items_a, items_b, f"list items under a {head!r} section")
                    self.assertEqual(fences_a, fences_b, f"fenced blocks under a {head!r} section")
                for key in ("placeholders", "spans", "bold"):
                    self.assertEqual(a[key], b[key], key)
                # Every command, path and flag is still there; spans with Chinese in them are translated
                # and so turn up on the English side only, which the span count keeps in check.
                self.assertEqual(a["code"] - b["code"], Counter(), "inline code missing in English")

    def test_english_has_no_chinese(self):
        for en in sorted((PROMPTS / "en").glob("*.md")):
            with self.subTest(prompt=en.name):
                bad = [(n, l) for n, l in enumerate(en.read_text().splitlines(), 1) if CJK.search(l)]
                self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
