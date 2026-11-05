"""Sandbox entrypoint. Reads one JSON request on stdin and runs it.

This code runs inside the container, so it trusts nothing it receives and holds nothing worth
stealing. The container, not this file, is the security boundary.
"""

import json
import signal
import sys
import traceback


def main() -> int:
    request = json.loads(sys.stdin.read())
    # The default SIGALRM action ends the process, and user code cannot catch it as an exception.
    signal.alarm(int(request["timeout_seconds"]))
    if request.get("kind") != "python":
        print(f"unknown request kind {request.get('kind')!r}", file=sys.stderr)
        return 2
    try:
        # Running untrusted code is this container's job. The container flags contain it.
        exec(compile(request["code"], "<run>", "exec"), {"__name__": "__main__"})  # noqa: S102
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 1
    except BaseException:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=False)
    code = main()
    sys.stdout.flush()
    sys.exit(code)
