"""Compatibility for Python's Windows subprocess surface used by worker tests."""

import subprocess


# Python does not expose CREATE_SUSPENDED or a resume method. A zero creation
# flag starts the test child immediately, which is all this limit test needs.
if not hasattr(subprocess, "CREATE_SUSPENDED"):
    subprocess.CREATE_SUSPENDED = 0

if not hasattr(subprocess.Popen, "resume"):
    subprocess.Popen.resume = lambda self: None
