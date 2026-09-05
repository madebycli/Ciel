"""Guard against a mistake made three times in this file already:

A backtick inside a shader comment silently terminates the JavaScript
template literal the GLSL lives in, so the rest of the script becomes a
syntax error and the whole HUD fails to start. It is invisible in a diff
and the resulting error names a random identifier, which sends you
looking in the wrong place entirely.

Run this after ANY edit to hud_prototype.html's shaders.
"""
import sys

BT = chr(96)
path = "hud_prototype.html"
html = open(path, encoding="utf-8").read()

# Walk every template literal and flag the ones holding GLSL.
problems = []
i = 0
literal = 0
while True:
    start = html.find(BT, i)
    if start == -1:
        break
    end = html.find(BT, start + 1)
    if end == -1:
        problems.append("unterminated template literal near offset %d" % start)
        break
    body = html[start + 1 : end]
    literal += 1
    if "gl_FragColor" in body or "gl_Position" in body:
        # A GLSL literal that ended early would not contain its own
        # closing brace, so this also catches truncation.
        if body.count("{") != body.count("}"):
            problems.append(
                "shader literal #%d has unbalanced braces - it likely ends "
                "early at a stray backtick" % literal
            )
    i = end + 1

if problems:
    print("FAIL")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print("OK - %d template literals, shader literals balanced" % literal)
