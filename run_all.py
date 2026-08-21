import asyncio
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


async def run(name):
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(BASE_DIR / name),
        cwd=str(BASE_DIR),
    )
    await proc.communicate()

async def main():
    await asyncio.gather(run("main.py"), run("userbot.py"))

if __name__ == "__main__":
    asyncio.run(main())
