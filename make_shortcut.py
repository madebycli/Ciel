"""Put a Great Sage shortcut on the Desktop, pointing at the built exe.

The packaged app lives five folders deep, under dist/GreatSage/, which is
not somewhere anyone should have to navigate to in order to start it.

Safe to run any time - it overwrites its own shortcut and touches nothing
else. build.py calls it after a successful build, so the shortcut always
points at the exe that was actually just built rather than at a stale one.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "dist", "GreatSage", "GreatSage.exe")
NAME = "Great Sage.lnk"


def make(exe=EXE, quiet=False):
    if not os.path.exists(exe):
        if not quiet:
            print("No built exe at %s - run build.py first." % exe)
        return False
    try:
        # win32com is not a dependency of this project and never will be
        # for one shortcut, so this goes through PowerShell's COM access
        # instead. Failure is not fatal: a missing shortcut is a small
        # inconvenience, not a broken build.
        import subprocess
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        lnk = os.path.join(desktop, NAME)
        ps = (
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
            "$s.TargetPath = '%s';"
            "$s.WorkingDirectory = '%s';"
            "$s.IconLocation = '%s,0';"
            "$s.Description = 'Great Sage';"
            "$s.Save()"
        ) % (lnk, exe, os.path.dirname(exe), exe)
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                        "-Command", ps],
                       check=True, capture_output=True, timeout=30)
        if not quiet:
            print("Desktop shortcut -> %s" % exe)
        return True
    except Exception as exc:
        if not quiet:
            print("Could not create the shortcut: %s" % exc)
        return False


if __name__ == "__main__":
    sys.exit(0 if make() else 1)
