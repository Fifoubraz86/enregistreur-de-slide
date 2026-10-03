import sys


def run() -> int:
    if len(sys.argv) > 1:
        from .cli import main

        return main()
    from .gui import main

    return main()


if __name__ == "__main__":
    sys.exit(run())
