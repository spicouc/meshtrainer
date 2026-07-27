#!/usr/bin/env python3
"""Run rc3 integration tests via subprocess."""
import subprocess
import sys
import os

os.chdir("/home/vinegart/qwen3_v9_2/_delivery/rc3")

# Try pytest via subprocess
result = subprocess.run(
    [sys.executable, "-m", "pytest", 
     "rc3_integration_tests.py", "-v", "--tb=short"],
    capture_output=True, text=True, timeout=180
)
stdout = result.stdout
stderr = result.stderr

# Print last 200 lines
lines = stdout.splitlines()
if len(lines) > 200:
    print("... (truncated)", file=sys.stderr)
    print("\n".join(lines[-200:]))
else:
    print(stdout)

if stderr:
    print("\n=== STDERR ===", file=sys.stderr)
    print(stderr[-3000:] if len(stderr) > 3000 else stderr)

print(f"\n=== Exit code: {result.returncode} ===")
