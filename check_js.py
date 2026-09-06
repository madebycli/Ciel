"""Catch inline-script damage that makes the HUD render nothing.

Two failures, both of which have actually shipped, and both of which look
identical from outside: the page is black or blank, the WebSocket never
opens, and the Python side logs a completely healthy startup because the
fault is entirely in the browser.

1. A STRING LITERAL BROKEN ACROSS LINES. A single unescaped newline inside
   a '...' or "..." literal is a SyntaxError, and it kills the whole
   inline script. That shipped once: the literal was written through a
   bash heredoc, which ate the backslash in an escape and left a real line
   break inside quotes.

2. UNBALANCED BRACES. An edit that removes a block but leaves one "}"
   behind is the same failure with a different cause. That shipped too - a
   region was cut by index arithmetic that landed inside a function body,
   and the string-literal check passed it because every literal was fine.

Both need a real scanner rather than a character count: "//" inside
'ws://host' is not a comment, a double quote inside a single-quoted string
is not a delimiter, and - the one that actually bit this file - the "\\//"
at the end of /^image\\// is not a comment either. Treating it as one
swallowed the rest of the line, including an opening brace, and reported a
perfectly good file as broken.

So regex literals are parsed properly, using the standard rule: a "/" is a
regex when the last significant thing before it cannot end an expression,
and division when it can.

Template literals legitimately span lines, so they are tracked but never
reported as broken.
"""

import io
import re
import sys

BS = chr(92)
NL = chr(10)

# Characters that cannot END an expression. A "/" following one of these
# starts a regex literal; a "/" following an identifier, a number, ")" or
# "]" is division.
REGEX_PREV_CHARS = set("(,=:[!&|?{};+-*%~^<>" + NL)
REGEX_PREV_WORDS = {"return", "typeof", "instanceof", "in", "of", "new",
                    "delete", "void", "do", "else", "case", "yield", "await"}


def _prev_significant(js, i):
    """(last non-space char before i, the word ending there)."""
    j = i - 1
    while j >= 0 and js[j] in " \t\r\n":
        j -= 1
    if j < 0:
        return "", ""
    c = js[j]
    word = ""
    if c.isalnum() or c == "_":
        k = j
        while k >= 0 and (js[k].isalnum() or js[k] == "_"):
            k -= 1
        word = js[k + 1:j + 1]
    return c, word


def _skip_regex(js, i):
    """i is at the opening '/'. Index just past the closing '/', or None.

    None means this was not a regex after all (it hit a line break, which
    a regex literal cannot contain), so the caller should treat it as
    division and carry on.
    """
    j, n = i + 1, len(js)
    in_class = False
    while j < n:
        c = js[j]
        if c == BS:
            j += 2
            continue
        if c == NL:
            return None
        if in_class:
            if c == "]":
                in_class = False
        elif c == "[":
            in_class = True
        elif c == "/":
            return j + 1
        j += 1
    return None


def scan(js):
    """Walk the script once.

    Returns (broken_literal_lines, end_depth, first_negative_line).
    """
    bad = []
    state = None              # None | "'" | '"' | "`" | "block"
    depth = 0
    negative_at = None
    line_no = 1
    line_start = 0
    i, n = 0, len(js)
    while i < n:
        c = js[i]
        if c == NL:
            if state in ("'", '"'):
                bad.append((line_no, js[line_start:i].strip()[:100]))
                state = None      # resync so one break is not reported twice
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
        # Not inside a string or a block comment.
        if c == "/" and i + 1 < n:
            if js[i + 1] == "/":
                j = js.find(NL, i)
                i = n if j < 0 else j
                continue
            if js[i + 1] == "*":
                state = "block"
                i += 2
                continue
            prev_char, prev_word = _prev_significant(js, i)
            if prev_char in REGEX_PREV_CHARS or prev_word in REGEX_PREV_WORDS \
                    or prev_char == "":
                end = _skip_regex(js, i)
                if end is not None:
                    line_no += js.count(NL, i, end)
                    i = end
                    continue
        if c in ("'", '"', "`"):
            state = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0 and negative_at is None:
                negative_at = line_no
        i += 1
    return bad, depth, negative_at


def check(path):
    html = io.open(path, encoding="utf-8", newline="").read()
    html = html.replace(chr(13) + NL, NL)
    problems = 0
    for m in re.finditer(r"<script([^>]*)>([\s\S]*?)</script>", html):
        if "src=" in m.group(1):
            continue
        base = html[:m.start()].count(NL)
        bad, depth, negative_at = scan(m.group(2))
        for ln, text in bad:
            print("  %s:%d: unterminated string literal" % (path, base + ln))
            print("      " + text)
            problems += 1
        if negative_at is not None:
            print("  %s:%d: a closing brace with nothing open"
                  % (path, base + negative_at))
            problems += 1
        elif depth != 0:
            print("  %s: script ends %d brace(s) %s"
                  % (path, abs(depth), "open" if depth > 0 else "over-closed"))
            problems += 1
    return problems


if __name__ == "__main__":
    targets = sys.argv[1:] or ["hud_prototype.html"]
    total = sum(check(t) for t in targets)
    if total:
        print(NL + "FAILED - %d problem(s). The inline script will not "
              "parse, so the HUD renders nothing." % total)
        raise SystemExit(1)
    print("OK - literals terminated and braces balanced in "
          + ", ".join(targets))
