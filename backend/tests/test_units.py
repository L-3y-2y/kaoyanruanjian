"""Unit tests for the pure functions — no DB, no network, no LLM.

    python backend/tests/test_units.py

`scripts/verify_full.py` covers the system end to end (real HTTP + real MySQL),
but it cannot tell you *which* function broke, and it costs tokens. These tests
cover the deterministic core: exam profiles, lemma matching / coverage scan,
practice-card validation and blank placement.

Deliberately no pytest: the project ships a venv with no test runner and this
must stay runnable on a fresh clone. Plain asserts + a tiny runner is enough.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import chat  # noqa: E402
import content  # noqa: E402
from exam_profile import PROFILES, profile_for, qtypes_for, word_range  # noqa: E402

# ---------------------------------------------------------------- exam profile


def test_profile_resolution():
    assert profile_for(None)["exam"] == "考研英语"
    assert profile_for({"code": "cet4"})["exam"] == "大学英语四级"
    assert profile_for({"code": "GK"})["exam"] == "高考英语"  # code is normalised
    # unknown code falls back to the exam_type text before the default
    assert profile_for({"code": "?", "exam_type": "大学英语六级"})["exam"] == "大学英语六级"
    assert profile_for({"code": "?"}) ["exam"] == "考研英语"


def test_shelf_lengths_are_ordered():
    """A weaker exam must never be given a longer passage than a harder one."""
    gk, c4, c6, ky = (PROFILES[c] for c in ("gk", "cet4", "cet6", "ky"))
    assert gk["article"][1] < c4["article"][1] < c6["article"][1] < ky["article"][1]
    assert (gk["reading"]["words"][1] < c4["reading"]["words"][1]
            < c6["reading"]["words"][1] < ky["reading"]["words"][1])
    assert (gk["cloze"]["words"][1] < c4["cloze"]["words"][1]
            < c6["cloze"]["words"][1])
    assert word_range(gk, "daily") == gk["article"]
    assert word_range(gk, "weekly") == gk["reading"]["words"]


def test_question_counts_follow_shelf():
    assert len(qtypes_for(PROFILES["gk"])) == 4
    assert len(qtypes_for(PROFILES["cet4"])) == 4
    assert len(qtypes_for(PROFILES["cet6"])) == 5
    assert len(qtypes_for(PROFILES["ky"])) == 5
    assert "subjective" in qtypes_for(PROFILES["ky"])
    assert "subjective" not in qtypes_for(PROFILES["cet4"])


# ------------------------------------------------------- lemma / coverage scan

WORDS = [
    {"id": 1, "word": "abandon", "translation": "放弃", "phonetic": "", "pos": "v.",
     "exchange": "p:abandoned/d:abandoned/i:abandoning/3:abandons"},
    {"id": 2, "word": "study", "translation": "学习", "phonetic": "", "pos": "v.",
     "exchange": "p:studied/d:studied/i:studying/3:studies"},
]


def test_lemma_map_matches_inflections():
    lemma = content.build_lemma_map(WORDS)
    for form in ("abandon", "abandoned", "abandoning", "abandons"):
        assert form in lemma, form
    for form in ("study", "studied", "studying", "studies"):
        assert form in lemma, form
    assert lemma["abandon"]["id"] == 1  # keys are lower-cased surfaces


def test_scan_finds_inflected_and_capitalised_words():
    body = "She Abandoned the plan. He studies every night; studying helps."
    found = {m["word_id"] for m in content.scan_article_words(body, WORDS)}
    assert found == {1, 2}, found
    hits = content.scan_article_words(body, WORDS)
    assert len(hits) == 3, "one hit per occurrence, not per word"  # Abandoned/studies/studying


def test_scan_ignores_unknown_words():
    assert content.scan_article_words("nothing relevant here", WORDS) == []


# ----------------------------------------------------------- practice card text


def test_clean_para_strips_model_noise():
    assert chat._clean_para("Paragraph 2: **Hello** there.") == "Hello there."
    assert chat._clean_para('  "Quoted text."  ') == "Quoted text."
    assert chat._clean_para("") == ""


PASSAGE = (
    "Many students abandon a plan after one hard week because the first step feels "
    "too heavy, but the ones who improve are those who decide to reduce the load "
    "instead. A short session can still create real progress if you focus on one "
    "skill at a time, and teachers encourage learners to establish a routine they "
    "can maintain for a whole term without extra pressure."
)


def test_cloze_candidates_skip_stopwords_and_contractions():
    _tokens, cands = chat._cloze_candidates(
        "It's clear that students don't abandon a plan after one hard week, "
        "but they improve and decide to reduce the load instead.")
    words = [w for _i, w in cands]
    assert words, "must find some content words"
    assert not any("'" in w for w in words), words          # It's / don't
    assert not any(w.lower() in chat._STOPWORDS for w in words), words
    assert all(len(w) >= 4 for w in words)


def test_cloze_candidates_are_spaced():
    tokens, cands = chat._cloze_candidates(PASSAGE)
    idxs = [i for i, _w in cands]
    assert len(idxs) >= 10, f"a {len(tokens)}-token passage must yield 10+ candidates"
    assert all(b - a >= 4 for a, b in zip(idxs, idxs[1:])), idxs
    for idx, word in cands:
        assert tokens[idx][2] == word


def test_mark_blanks_inserts_exactly_ten_in_order():
    tokens, cands = chat._cloze_candidates(PASSAGE)
    chosen = [(cands[i][0], f"解析{i}") for i in range(10)]
    marked, options, blanks = chat._mark_blanks(PASSAGE, tokens, chosen)
    assert marked is not None
    nums = [int(n) for n in __import__("re").findall(r"__(\d+)__", marked)]
    assert nums == list(range(1, 11))
    assert set(options) == set("ABCDEFGHIJ")
    assert len(set(options.values())) == 10
    for b in blanks:
        assert b["answer"] in options
        assert b["explanation"].startswith("解析")


def test_mark_blanks_slicing_keeps_text_intact():
    tokens, cands = chat._cloze_candidates(PASSAGE)
    chosen = [(cands[i][0], "x") for i in range(10)]
    marked, options, blanks = chat._mark_blanks(PASSAGE, tokens, chosen)
    expected = len(PASSAGE) - sum(len(options[b["answer"]]) for b in blanks) + \
        sum(len(f"__{n}__") for n in range(1, 11))
    assert len(marked) == expected, (len(marked), expected)
    assert "  " not in marked, "slicing must not leave doubled spaces"


def test_mark_blanks_rejects_bad_picks():
    tokens, cands = chat._cloze_candidates(PASSAGE)
    good = [(cands[i][0], "x") for i in range(len(cands))]
    assert chat._mark_blanks(PASSAGE, tokens, good[:9]) is None          # only nine
    assert chat._mark_blanks(PASSAGE, tokens, [good[0]] * 10) is None   # duplicates
    assert chat._mark_blanks(PASSAGE, tokens, good[:10]) is not None


def test_cloze_picks_validator():
    ok = {"picks": [{"n": i, "explanation": "x"} for i in range(1, 11)]}
    assert chat._cloze_picks_valid(ok, 40)
    assert not chat._cloze_picks_valid({"picks": ok["picks"][:9]}, 40)      # nine
    assert not chat._cloze_picks_valid({"picks": ok["picks"][:9] +
                                        [{"n": 1, "explanation": "x"}]}, 40)  # dup
    assert not chat._cloze_picks_valid({"picks": [{"n": 41, "explanation": "x"}] +
                                        ok["picks"][1:]}, 40)            # out of range
    assert not chat._cloze_picks_valid({"picks": [{"n": 1, "explanation": " "}] +
                                        ok["picks"][1:]}, 40)            # no reason


def test_questions_validator_follows_shelf():
    q = {"no": 1, "stem": "What?", "options": {"A": "a", "B": "b", "C": "c", "D": "d"},
         "answer": "A", "explanation": "因为 a"}
    four = {"questions": [dict(q, no=i) for i in range(1, 5)]}
    five = {"questions": [dict(q, no=i) for i in range(1, 6)]}
    assert chat._questions_valid(four, 4)
    assert not chat._questions_valid(four, 5)
    assert chat._questions_valid(five, 5)
    bad = {"questions": [dict(q, no=i, answer="Z") for i in range(1, 5)]}
    assert not chat._questions_valid(bad, 4), "answer must be one of A-D"


def test_card_valid_rejects_short_passage():
    passage = "Too short."
    card = {"kind": "reading", "title": "T", "passage": passage,
            "questions": [{"no": i, "stem": "s", "options": {"A": "a", "B": "b",
                                                             "C": "c", "D": "d"},
                           "answer": "A", "explanation": "e"} for i in range(1, 5)]}
    assert not chat._card_valid(card, "reading", {"code": "gk"})


# ---------------------------------------------------------------------- runner

def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - report, keep running
            failed += 1
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
