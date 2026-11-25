"""Fast in-memory English -> Chinese dictionary backed by ECDICT.

data/ecdict.csv is a 770k-row ECDICT export. Loading it eagerly would slow every
backend start, so we lazy-load it on first use and keep only the minimal fields
(word -> (first translation line, phonetic)) to bound memory.

`warm()` is called from a daemon thread at startup so the first "show every
word" click is fast, but nothing breaks if the CSV is missing — lookups simply
return None and the UI shows 暂无释义 for those tokens.
"""

import csv
import threading
from pathlib import Path

_DATA = None
_LOCK = threading.Lock()
_CSV = Path(__file__).resolve().parent.parent / "data" / "ecdict.csv"


def _load():
    idx = {}
    if not _CSV.is_file():
        return idx
    with _CSV.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            return idx
        try:
            wi = header.index("word")
            pi = header.index("phonetic")
            ti = header.index("translation")
        except ValueError:
            return idx
        for row in reader:
            if len(row) <= ti:
                continue
            word = (row[wi] or "").strip().lower()
            if not word:
                continue
            translation = (row[ti] or "").strip()
            if not translation:
                continue
            phonetic = (row[pi] or "").strip() if len(row) > pi else ""
            # ECDICT stores line breaks as the literal characters \n.
            first = translation.replace("\\n", " ").split("\n")[0].strip()
            idx[word] = (first, phonetic)
    return idx


def warm():
    global _DATA
    if _DATA is None:
        with _LOCK:
            if _DATA is None:
                _DATA = _load()


def lookup(word):
    global _DATA
    if _DATA is None:
        with _LOCK:
            if _DATA is None:
                _DATA = _load()
    return _DATA.get((word or "").strip().lower())
