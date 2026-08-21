"""Process supervisor for main.py (Admin Bot) and userbot.py (Personal
Account) — Feature 3 fix: Auto-Restart on Crash.

Railway's own restart policy (see railway.json -> restartPolicyType:
ON_FAILURE) restarts the WHOLE container if this process exits non-zero.
That alone is slow (a full container rebuild/boot each time). This
supervisor adds a faster, in-process layer: if either main.py or userbot.py
crashes for any reason, it is restarted on its own within a few seconds --
no full container restart needed. Railway's restart policy stays as the
outer safety net for anything this supervisor itself can't recover from
(e.g. this very process dying, or the container running out of memory).
"""
import asyncio
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Crash-loop protection: if a script crashes this many times within
# CRASH_WINDOW_SECONDS, back off for a while instead of restarting it in a
# tight loop (protects against a persistently broken script spinning the
# CPU/log forever).
MAX_RESTARTS_IN_WINDOW = 8
CRASH_WINDOW_SECONDS = 300
BACKOFF_SECONDS = 30
RESTART_DELAY_SECONDS = 3


async def run_forever(name: str):
    crash_times = []
    while True:
        started_at = time.monotonic()
        print(f"Starting {name} ...", flush=True)
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(BASE_DIR / name),
            cwd=str(BASE_DIR),
        )
        exit_code = await proc.wait()
        ran_for = time.monotonic() - started_at
        print(f"{name} exited with code {exit_code} after {ran_for:.0f}s -- auto-restarting...", flush=True)

        now = time.monotonic()
        crash_times = [t for t in crash_times if now - t < CRASH_WINDOW_SECONDS] + [now]
        if len(crash_times) >= MAX_RESTARTS_IN_WINDOW:
            print(
                f"{name} crashed {len(crash_times)} times within {CRASH_WINDOW_SECONDS}s -- "
                f"backing off {BACKOFF_SECONDS}s before retrying (crash-loop protection).",
                flush=True,
            )
            await asyncio.sleep(BACKOFF_SECONDS)
            crash_times = []
        else:
            await asyncio.sleep(RESTART_DELAY_SECONDS)


async def main():
    await asyncio.gather(run_forever("main.py"), run_forever("userbot.py"))


if __name__ == "__main__":
    asyncio.run(main())
