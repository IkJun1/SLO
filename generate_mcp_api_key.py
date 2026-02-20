from __future__ import annotations

import argparse
import secrets
import string


def generate_key(length: int) -> str:
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--length", type=int, default=64)
    args = parser.parse_args()

    if args.length < 32:
        raise ValueError("length must be at least 32")

    key = generate_key(args.length)
    print(key)


if __name__ == "__main__":
    main()
