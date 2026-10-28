"""Run a test file for the GitHub workflow: its output is shown as it comes, it can't hang the job,
and if it doesn't finish, the processes still running are listed so the cause is visible.

Usage: python .github/scripts/run_tests.py tests/test_review.py [timeout_minutes]
"""
import os
import subprocess
import sys
import tempfile
import time


def processes() -> str:
    try:
        if sys.platform == "win32":
            cmd = ["powershell", "-NoProfile", "-Command",
                   "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine "
                   "| Format-Table -AutoSize -Wrap | Out-String -Width 300"]
        else:
            cmd = ["ps", "-eo", "pid,ppid,etime,command"]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    except Exception as e:  # pragma: no cover - diagnostics only
        return f"(couldn't list processes: {e})"


def main() -> int:
    test = sys.argv[1]
    minutes = float(sys.argv[2]) if len(sys.argv) > 2 else 25
    log = tempfile.NamedTemporaryFile("w+", suffix=".log", delete=False, encoding="utf-8")
    p = subprocess.Popen([sys.executable, "-u", test], stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL)
    shown = 0
    deadline = time.time() + minutes * 60
    last_output = time.time()

    def flush():
        nonlocal shown, last_output
        with open(log.name, "rb") as fh:
            fh.seek(shown)
            new = fh.read()
        if new:
            sys.stdout.write(new.decode("utf-8", errors="replace"))
            sys.stdout.flush()
            shown += len(new)
            last_output = time.time()

    while p.poll() is None and time.time() < deadline:
        time.sleep(2)
        flush()
    flush()
    if p.poll() is None:
        print(f"\n!!! {test} still running after {minutes:g} minutes "
              f"(last output {time.time() - last_output:.0f} s ago). Processes:\n{processes()}", flush=True)
        p.kill()
        return 1
    print(f"\n{test} exited with {p.returncode}", flush=True)
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
