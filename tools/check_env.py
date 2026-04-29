import os
from pathlib import Path
from dotenv import load_dotenv

# .env лежит в корне проекта; этот скрипт — в tools/.
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
loaded = load_dotenv(ENV_PATH)

print(f".env path:    {ENV_PATH}")
print(f".env exists:  {ENV_PATH.exists()}")
print(f".env loaded:  {loaded}")
print()


def describe(name: str) -> None:
    raw = os.getenv(name)
    print(f"--- {name} ---")
    if raw is None:
        print("  state: NOT SET (переменная отсутствует в окружении)")
        return
    if raw == "":
        print("  state: EMPTY (переменная задана, но пустая)")
        return

    print(f"  state:   SET")
    print(f"  length:  {len(raw)}")
    print(f"  repr:    {raw!r}")
    print(f"  first:   {raw[:1]!r} (код {ord(raw[0])})")
    print(f"  last:    {raw[-1:]!r} (код {ord(raw[-1])})")
    print(f"  space:   {' ' in raw}")
    print(f"  CR/LF/TAB: {any(c in raw for c in chr(13) + chr(10) + chr(9))}")
    print(f"  quotes:  {chr(34) in raw or chr(39) in raw}")


describe("DSS_USER")
print()
describe("DSS_PASS")
