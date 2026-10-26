"""R-AI-002 for this app's UI: model text cannot become HTML.

The rule is that LLM output is validated before it is used downstream. Here the downstream is the dashboard:
`app/static/app.js` builds its HTML with `innerHTML` and template literals, so any caption, message or title
that is interpolated raw becomes markup — and because captions are stored, a model that emits an `onerror`
handler creates a page that runs it again on every future load, for the operator, in his browser.

The code already escapes (`esc(p.caption)`, `esc(m.content)` throughout). What did not exist is anything that
MEASURES it: the next interpolation somebody adds can skip `esc()`, and nothing would notice until it mattered.
So this has two cases, and the first one executes the real helper rather than a copy of it — a re-implementation
tests the re-implementation.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import unittest

APP_JS = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"

#: Fields that carry text a model or a user wrote. An interpolation of one of these into an innerHTML template
#: is markup unless it goes through `esc()`. Ids and counts are deliberately not here: this is scoped to text,
#: and the limitation is stated rather than papered over.
TEXT_FIELDS = ("caption", "content", "prompt", "title", "name", "handle", "email", "text", "body",
               "description", "story", "alt", "message", "answer", "summary", "reason", "subject", "label")

INTERPOLATION = re.compile(r"\$\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}")

#: Calls that already produce safe text: the escaping helper, the i18n lookup (app-owned strings), and the
#: numeric formatters.
SAFE_CALLS = ("esc(", "t(", "perfPct(", "Number(", "Math.", "statusLabel")


def unescaped_interpolations(source: str) -> list[tuple[int, str]]:
    """Line by line: an interpolation of a text field read off an OBJECT that skips the safe calls.

    Two deliberate narrowings, both learned by running this against the real file and reading what it flagged:

    * **Property accesses only.** Model and user text arrives as a property of a record — `p.caption`,
      `m.content`, `e.title`. A bare identifier (`${title}`, `${body}`) is a local or a parameter, and in this
      file those hold app-owned i18n strings and already-composed HTML: all five findings of the first run were
      that, and a check with five false positives is a check somebody switches off.
    * **Line by line.** This file's interpolations are single-line; one that spanned lines would need a parser.

    The honest cost of the first narrowing: a local *built* from model text and then interpolated raw would be
    missed. That is the trade-off, stated here rather than discovered later.
    """
    findings = []
    for lineno, line in enumerate(source.splitlines(), 1):
        for match in INTERPOLATION.finditer(line):
            expr = match.group(1).strip()
            if "not-html:" in line:
                # A line-scoped declaration, the same shape the machine uses for destructive paths
                # (`guard-ok:`): the string does not become markup HERE, and the reason is on the line. It is
                # needed because this scan reads the interpolation, not where the string ends up — and
                # `textContent` is exactly the case where escaping would be wrong (`&amp;` on screen).
                continue
            if any(call in expr for call in SAFE_CALLS):
                continue
            if not re.search(r"\b[a-zA-Z_$][\w$]*\.[\w$]+", expr):
                continue                                    # a local or a parameter, not a record's field
            if any(re.search(rf"\b{field}\b", expr) for field in TEXT_FIELDS):
                findings.append((lineno, expr[:100]))
    return findings


def run_esc(cases: list[str]) -> dict:
    """Run the app's OWN esc() under node, extracted from the file. A copy would test the copy."""
    source = APP_JS.read_text()
    start = source.index("function esc(s) {")
    end = source.index("\n}", start) + 2
    script = (source[start:end] + "\n"
              "const input = " + json.dumps(cases) + ";\n"
              "console.log(JSON.stringify(input.map((s) => esc(s))));\n")
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("node is not installed — the helper cannot be executed here")
    proc = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError(f"node could not run the app's esc(): {proc.stderr.strip()[:200]}")
    return json.loads(proc.stdout)


class EscapingTest(unittest.TestCase):
    def test_the_helper_escapes_everything_that_can_start_markup(self):
        hostile = [
            '<img src=x onerror="alert(1)">',
            "<script>fetch('//evil')</script>",
            "\" onmouseover=\"alert(1)",
            "' onfocus='alert(1)",
            "a & b",
            "plain text",
        ]
        out = run_esc(hostile)
        for original, escaped in zip(hostile, out):
            self.assertNotIn("<", escaped, original)
            self.assertNotIn(">", escaped, original)
            self.assertNotIn('"', escaped, original)
            self.assertNotIn("'", escaped, original)
            # Every `&` must be the start of one of the five entities. Asserting "no & at all" was this
            # case's own first bug: `&lt;` begins with one, so the assertion failed on correctly escaped text.
            self.assertNotRegex(escaped, r"&(?!(?:amp|lt|gt|quot|#39);)", original)
        self.assertEqual(out[-1], "plain text", "text with nothing to escape is left alone")

    def test_nothing_that_can_be_null_throws(self):
        """The helper is fed fields that are None on a draft post; a throw there is a blank dashboard."""
        self.assertEqual(run_esc([None]), [""])

    def test_the_scan_catches_the_shape_it_exists_for(self):
        """A check nobody has seen fail is a check nobody knows works."""
        regression = '<td class="trunc">${p.caption}</td>'
        self.assertEqual(len(unescaped_interpolations(regression)), 1)
        self.assertEqual(unescaped_interpolations('<td>${esc(p.caption)}</td>'), [])

    def test_a_declared_line_is_not_a_finding(self):
        """`textContent` is where escaping would be wrong; the declaration says so, with its reason."""
        line = '<p>${e.caption}</p> // not-html: assigned to textContent'
        self.assertEqual(unescaped_interpolations(line), [])
        self.assertEqual(len(unescaped_interpolations('<p>${e.caption}</p>')), 1)

    def test_the_scan_ignores_locals_and_parameters(self):
        """The first run flagged five of these: app-owned i18n strings and already-composed HTML."""
        for safe in ('<label for="${id}">${label}</label>',
                     '<div title="${title}"></div>',
                     '<div>${body}</div>',
                     '<input type="${type || \"text\"}">'):
            self.assertEqual(unescaped_interpolations(safe), [], safe)

    def test_every_interpolation_of_a_text_field_goes_through_esc(self):
        findings = unescaped_interpolations(APP_JS.read_text())
        self.assertEqual(findings, [], "an interpolation of model/user text reaches innerHTML unescaped — "
                                      "this is the stored-XSS path R-AI-002 is about: " + str(findings[:4]))


if __name__ == "__main__":
    unittest.main()
