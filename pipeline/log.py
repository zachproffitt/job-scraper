from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


def log_error(source: str, message: str, log_file: Path | None = None) -> None:
    path = log_file or DATA_DIR / "pipeline.log"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, "a") as f:
        f.write(f"[{ts}] {source}: {message}\n")
