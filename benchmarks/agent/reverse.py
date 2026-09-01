import sys


def main() -> None:
    text = sys.argv[1]
    print(text[::-1])


if __name__ == "__main__":
    main()
