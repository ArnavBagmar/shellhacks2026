"""Log files a teammate (or an assistant) can read after a walk.

``configure_file_logging(Path("logs"))`` adds, beside the console output:

- ``logs/laptop-YYYYMMDD.log`` -- every INFO+ record of this process;
- ``logs/phone-YYYYMMDD.jsonl`` -- only the phone's ``diag`` lines (logger
  ``cane.phone``), one JSON object per line.

Both append, so several runs in a day land in one file, separated by a
start-up banner.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

LAPTOP_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_file_logging(log_dir: Path) -> tuple[Path, Path]:
    log_dir.mkdir(parents=True, exist_ok=True)
    day = time.strftime("%Y%m%d")
    laptop_path = log_dir / f"laptop-{day}.log"
    phone_path = log_dir / f"phone-{day}.jsonl"

    laptop = logging.FileHandler(laptop_path, encoding="utf-8")
    laptop.setLevel(logging.INFO)
    laptop.setFormatter(logging.Formatter(LAPTOP_FORMAT))
    root = logging.getLogger()
    root.addHandler(laptop)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)

    phone = logging.FileHandler(phone_path, encoding="utf-8")
    phone.setLevel(logging.INFO)
    phone.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger("cane.phone").addHandler(phone)

    logging.getLogger(__name__).info("---- run started; phone log: %s ----", phone_path)
    return laptop_path, phone_path
