#!/usr/bin/env python3
"""Run the Texas Hold'em Arena server. Open http://127.0.0.1:8000 in your browser."""
import os
import sys
import subprocess

def main():
    root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root)
    env = os.environ.copy()
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    print("Starting server at http://127.0.0.1:8000")
    print("Open that URL in your browser and click 'New game' to play.")
    print()
    sys.exit(subprocess.call([
        sys.executable, "-m", "uvicorn", "server.app:app",
        "--host", "0.0.0.0", "--port", "8000",
    ], env=env, cwd=root))

if __name__ == "__main__":
    main()
