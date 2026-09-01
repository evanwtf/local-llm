from __future__ import annotations

import sys


def reverse(text: str) -> str:
    return text[::-1]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 reverse.py <string>", file=sys.stderr)
        sys.exit(1)
    print(reverse(sys.argv[1]))
