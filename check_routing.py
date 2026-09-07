"""Does Great Sage actually DO what it is told, or just talk about it?

Every entry here is a phrasing Krazaa has used or would plausibly use,
paired with the tool that must run. It exists because the failure mode is
not a crash - it is a confident refusal:

    "search on youtube for X"
    -> "I cannot directly play videos or execute external software for
        you. You will need to locate the video and press play manually."

The tools were attached to that turn and the model simply declined. So
the ones that matter are pre-routed - decided by this table rather than
by a 4B model's mood - and this file is what keeps that true. Run it
before shipping:

    py check_routing.py

MUST_NOT is as important as MUST. Pre-routing something that only sounds
like an instruction is worse than missing it: "how do I open a pull
request" must not launch an application, and "search my downloads folder"
belongs to the file tool, not DuckDuckGo.
"""

import sys

from great_sage.core import tools

# (phrase, expected tool, substring that must appear in the arguments)
MUST = [
    # --- the web ---
    ("search the web for the latest RTX 5090 news", "web_search", "RTX 5090"),
    ("search for cheap flights to Osaka", "web_search", "Osaka"),
    ("look up who won the 2024 F1 championship", "web_search", "2024"),
    ("google the weather in Tokyo", "web_search", "weather"),
    ("can you search the internet for slime anime news", "web_search", "slime"),
    ("search online for a good ramen place", "web_search", "ramen"),

    # --- YouTube: the one that was refused outright ---
    ("search on youtube for lofi beats", "open_url", "lofi"),
    ("search youtube for rimuru fight scenes", "open_url", "rimuru"),
    ("play bohemian rhapsody on youtube", "open_url", "bohemian"),
    ("youtube search for cat videos", "open_url", "cat"),
    ("find gundam openings on youtube", "open_url", "gundam"),
    ("search on youtube for lofi beats", "open_url", "youtube.com"),

    # VERBATIM from great_sage.log - what Krazaa actually said out loud,
    # transcribed correctly, and refused. The word order is not the tidy
    # one; that is the point of keeping them.
    ("Could you open up YouTube and search up that time I got "
     "reincarnated as a slime season for opening and play the video",
     "open_url", "reincarnated"),
    ("I say like could you like search on YouTube for me and open up "
     "that time I got reincarnated as a slime season for opening tactic "
     "video", "open_url", "reincarnated"),
    ("Could you open YouTube and search up that time I got reincarnated "
     "as a slime season 4 opening", "open_url", "reincarnated"),
    # The title starts with "That" - stripping it as filler once turned
    # the search into "time I got reincarnated...".
    ("Could you open YouTube and search up that time I got reincarnated "
     "as a slime season 4 opening", "open_url", "that+time"),
    ("find gundam openings on youtube", "open_url", "gundam"),

    # --- opening things ---
    ("open spotify", "open_application", "spotify"),
    ("launch discord", "open_application", "discord"),
    ("open reaper", "open_application", "reaper"),
    ("open youtube", "open_url", "youtube.com"),
    ("open github", "open_url", "github.com"),
    # EXACT, not "contains". "downloads folder" contains "downloads" and
    # still failed - open_folder resolves a NAME against the home
    # directory, so the trailing noun must already be gone.
    ("open my downloads folder", "open_folder", "=downloads"),
    ("open the documents folder", "open_folder", "=documents"),

    # --- this machine, this moment ---
    ("what time is it", "get_time", ""),
    ("what day is it", "get_time", ""),
    ("how much vram is free", "get_system_status", ""),
    ("check my disk space", "get_system_status", ""),
    ("what am i using right now", "get_focused_window", ""),
    ("look at my screen", "look_at_screen", ""),
    ("what do you see", "look_at_screen", ""),
    ("what reminders do i have", "list_tasks", ""),
]

# Phrases that must be left entirely to the model.
MUST_NOT = [
    "how are you today",
    "what do you think about jazz",
    "how do i open a pull request",          # 'open' as a figure of speech
    "tell me about the great sage skill",
    "search my downloads folder for notes",  # the file tool's job
    "find my desktop folder",                # ditto
    "why did you say that",
    "is youtube down right now",       # about YouTube, not a request for it
]


def _args_text(args):
    return " ".join(str(v) for v in args.values()).lower()


def run():
    failures = []

    for phrase, want_tool, want_arg in MUST:
        routed = tools.preroute(phrase)
        names = [n for n, _ in routed]
        if want_tool not in names:
            failures.append("%-46s expected %s, got %s"
                            % (phrase[:46], want_tool, names or "nothing"))
            continue
        if want_arg:
            args = next(a for n, a in routed if n == want_tool)
            exact = want_arg.startswith("=")
            probe = want_arg[1:] if exact else want_arg
            hit = (_args_text(args) == probe.lower() if exact
                   else probe.lower() in _args_text(args))
            if not hit:
                failures.append("%-46s %s ran with the wrong argument "
                                "(wanted %r, got %s)"
                                % (phrase[:46], want_tool, probe, args))

    for phrase in MUST_NOT:
        routed = tools.preroute(phrase)
        if routed:
            failures.append("%-46s should NOT route, but ran %s"
                            % (phrase[:46], [n for n, _ in routed]))

    total = len(MUST) + len(MUST_NOT)
    if failures:
        print("FAILED - %d of %d" % (len(failures), total))
        for f in failures:
            print("   " + f)
        return 1
    print("OK - %d routing cases (%d must act, %d must not)"
          % (total, len(MUST), len(MUST_NOT)))
    return 0


if __name__ == "__main__":
    sys.exit(run())
