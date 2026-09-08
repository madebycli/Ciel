"""Package dist/GreatSage into files a GitHub release can actually hold.

GitHub refuses any release asset over 2 GB. The build is about 5.2 GB and
compresses to roughly half that, which is still too big for one file - so
this writes a SPLIT 7-Zip archive in volumes under the limit.

For whoever downloads it that means: grab every part into one folder,
right-click the .001, "Extract Here", run GreatSage.exe. 7-Zip pulls the
other volumes in on its own; there is nothing to join by hand.

    py package_release.py

Leaves release/ containing the parts and a short README the release page
can quote verbatim.
"""

import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist", "GreatSage")
OUT = os.path.join(HERE, "release")
ARCHIVE = os.path.join(OUT, "GreatSage.7z")

# 1900 MB, not 2000. The limit is on the file, and leaving margin is
# cheaper than discovering the last volume is 2.01 GB after an hour of
# compression and an upload.
VOLUME = "1900m"

SEVENZIP = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
]

READ_ME = """Great Sage - install
====================

1. Put every part (GreatSage.7z.001, .002, ...) in the SAME folder.
   You need all of them; the parts are one archive, not alternatives.

2. Right-click GreatSage.7z.001 -> 7-Zip -> "Extract Here".
   Only the .001. The rest are pulled in automatically.
   No 7-Zip? https://www.7-zip.org

3. Open the GreatSage folder and run GreatSage.exe.

The first time it runs it checks for the three things that cannot be
bundled - Ollama, the chat model, and Microsoft WebView2 - and offers to
install what is missing. Let it, then press Launch.

The first reply is slow. It downloads the voice model (about 3.6 GB)
the first time it speaks. It is not frozen.

If it answers in text but never SPEAKS, that is the common one and it has
its own section in INSTALL.md on the repo - usually a CPU-only PyTorch,
or antivirus HTTPS scanning blocking the voice download.
"""


def _find_7z():
    for p in SEVENZIP:
        if os.path.exists(p):
            return p
    found = shutil.which("7z")
    if found:
        return found
    raise SystemExit("7-Zip not found. Install it from https://www.7-zip.org")


def main():
    if not os.path.isdir(DIST):
        raise SystemExit("No build at %s - run build.py first." % DIST)

    sevenzip = _find_7z()
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)

    total = sum(
        os.path.getsize(os.path.join(r, f))
        for r, _, fs in os.walk(DIST) for f in fs
        if os.path.exists(os.path.join(r, f)))
    print("  packing %.2f GB from %s" % (total / 1073741824, DIST))
    print("  this takes a while - it is 5 GB of DLLs")

    started = time.time()
    # -mx=5 rather than 9: the extra hour of CPU buys a few percent on
    # binaries that are already mostly incompressible, and the volume
    # count is what actually matters here.
    cmd = [sevenzip, "a", "-t7z", "-mx=5", "-mmt=on",
           "-v" + VOLUME, ARCHIVE, DIST]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        raise SystemExit("7-Zip failed (%d)" % r.returncode)

    with open(os.path.join(OUT, "READ ME FIRST.txt"), "w",
              encoding="utf-8") as f:
        f.write(READ_ME)

    parts = sorted(p for p in os.listdir(OUT) if ".7z." in p)
    packed = sum(os.path.getsize(os.path.join(OUT, p)) for p in parts)
    print()
    print("  done in %.0f min" % ((time.time() - started) / 60))
    print("  %.2f GB -> %.2f GB in %d part(s)"
          % (total / 1073741824, packed / 1073741824, len(parts)))
    for p in parts:
        size = os.path.getsize(os.path.join(OUT, p)) / 1073741824
        flag = "  OK" if size < 2.0 else "  TOO BIG FOR GITHUB"
        print("     %-22s %5.2f GB%s" % (p, size, flag))
    print()
    print("  upload everything in release/ to one GitHub release.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
