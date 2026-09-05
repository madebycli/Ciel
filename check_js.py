"""Catch JavaScript string literals broken across lines.

A single unescaped newline inside a '...' or "..." literal is a
SyntaxError, and it kills the ENTIRE inline script - so the HUD renders
nothing, never opens its WebSocket, and sits on "connecting..." forever.
There is no error visible anywhere in the app; the Python side logs a
healthy startup because the failure is in the page.

That shipped once. The literal was written through a bash heredoc, which
ate the backslash in an escape and left a real line break inside quotes.

A proper scanner, not a quote count: "//" inside 'ws://host' is not a
comment, and a double quote inside a single-quoted string is not a
delimiter. Counting quotes flags both and is useless.

Template literals legitimately span lines, so they are tracked but never
reported.
"""

import io
import re
import sys

BS = chr(92)
NL = chr(10)


def broken_lines(js):
    """(line_no, text) for every line ending inside a '' or "" literal."""
    bad = []
    state = None          # None | "'" | '"' | "`" | "block"
    line_no = 1
    i, n = 0, len(js)
    line_start = 0
    while i < n:
        c = js[i]
        if c == NL:
            if state in ("'", '"'):
                bad.append((line_no, js[line_start:i].strip()[:100]))
                state = None          # resync so one break is not reported twice
            line_no += 1
            line_start = i + 1
            i += 1
            continue
        if state == "block":
            if c == "*" and i + 1 < n and js[i + 1] == "/":
                state = None
                i += 2
                continue
            i += 1
            continue
        if state in ("'", '"', "`"):
            if c == BS:
                i += 2
                continue
            if c == state:
                state = None
            i += 1
            continue
        # not in a string or block comment
        if c == "/" and i + 1 < n:
            if js[i + 1] == "/":
                j = js.find(NL, i)
                i = n if j < 0 else j
                continue
            if js[i + 1] == "*":
                state = "block"
                i += 2
                continue
        if c in ("'", '"', "`"):
            state = c
        i += 1
    return bad


def check(path):
    html = io.open(path, encoding="utf-8", newline="").read()
    html = html.replace(chr(13) + NL, NL)
    problems = 0
    for m in re.finditer(r"<script([^>]*)>([\s\S]*?)</script>", html):
        if "src=" in m.group(1):
            continue
        base = html[:m.start()].count(NL)
        for ln, text in broken_lines(m.group(2)):
            print("  %s:%d: unterminated string literal" % (path, base + ln))
            print("      " + text)
            problems += 1
    return problems


if __name__ == "__main__":
    targets = sys.argv[1:] or ["hud_prototype.html"]
    total = sum(check(t) for t in targets)
    if total:
        print(NL + "FAILED - %d broken literal(s). The inline script will not "
              "parse, so the HUD renders nothing." % total)
        raise SystemExit(1)
    print("OK - no broken string literals in " + ", ".join(targets))
