"""A real child with a real descendant, for termination tests."""

import json
import subprocess
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    import os

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    Path(sys.argv[1]).write_text(json.dumps([os.getpid(), child.pid]))
    time.sleep(60)
