"""Point d'entrée (double-clic, ou utilisé par PyInstaller pour créer le .exe)."""

import sys

from capture_reunion.__main__ import run

if __name__ == "__main__":
    sys.exit(run())
