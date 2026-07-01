from pathlib import Path


_SRC_APP_PATH = Path(__file__).resolve().parent.parent / "src" / "app"
if _SRC_APP_PATH.is_dir():
    __path__.append(str(_SRC_APP_PATH))
