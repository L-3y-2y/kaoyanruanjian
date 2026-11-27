"""Word-bank import helpers.

The built-in CSV is a small demo sample. For the real 5500-word list use
scripts/import_ecdict.py with ECDICT's CSV and the `ky` (kaoyan) tag.
"""

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "data" / "kaoyan_core_words.csv"

INSERT_WORD = """
INSERT IGNORE INTO `word`
  (`word`, `phonetic`, `translation`, `pos`, `tag`, `exchange`, `source`, `verified`)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""


def _clean(value):
    return (value or "").strip() or None


def ensure_words(conn, csv_path=None):
    """Import the built-in sample. Idempotent thanks to the unique word index."""
    path = Path(csv_path) if csv_path else DEFAULT_CSV
    if not path.is_file():
        raise FileNotFoundError(f"word bank CSV not found: {path}")
    inserted = 0
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            word = _clean(row.get("word"))
            if not word:
                continue
            with conn.cursor() as cur:
                inserted += cur.execute(
                    INSERT_WORD,
                    (
                        word,
                        _clean(row.get("phonetic")),
                        _clean(row.get("translation")),
                        _clean(row.get("pos")),
                        _clean(row.get("tag")) or "ky",
                        _clean(row.get("exchange")),
                        _clean(row.get("source")) or "built-in-sample",
                        1 if str(row.get("verified", "1")).strip() == "1" else 0,
                    ),
                )
    return inserted


def import_ecdict(conn, csv_path, limit=None, tags=None, source=None):
    """Import ECDICT rows whose tag contains any of `tags`. Idempotent.

    `tags` defaults to the single `ky` tag so the original call site keeps
    its old behaviour. Multi-book imports pass all exam tags; INSERT IGNORE
    leaves already-imported words untouched (their full ECDICT tag string is
    preserved, which is what lets one word belong to several books).
    """
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"ECDICT CSV not found: {path}")
    wanted = set(tags) if tags else {"ky"}
    label = source or ("ECDICT-" + "-".join(sorted(wanted)))
    inserted = skipped = 0
    with path.open("r", encoding="utf-8-sig", newline="", errors="replace") as fh:
        for row in csv.DictReader(fh):
            word = _clean(row.get("word"))
            tag = _clean(row.get("tag")) or ""
            tags_of_word = set(tag.split())
            if not word or not wanted.intersection(tags_of_word):
                skipped += 1
                continue
            translation = _clean(row.get("translation")) or _clean(row.get("definition"))
            # ECDICT stores line breaks as the literal characters \n
            if translation:
                translation = translation.replace("\\n", "; ").strip()
            with conn.cursor() as cur:
                inserted += cur.execute(
                    INSERT_WORD,
                    (
                        word,
                        _clean(row.get("phonetic")),
                        translation,
                        _clean(row.get("pos")),
                        tag,
                        _clean(row.get("exchange")),
                        label,
                        0,
                    ),
                )
            if limit and inserted >= limit:
                break
    return {"inserted": inserted, "skipped": skipped}
