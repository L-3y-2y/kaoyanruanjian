r"""End-to-end self check for stage 1-3.

Start the backend first (double-click start.bat), then run:
    D:\kaoyanruanjian\backend\.venv\Scripts\python.exe scripts\verify.py

It creates a temporary 18-word plan (5/5/5/3 cycles), walks R1->R5, checks the two-level
status and Chinese encoding, then DELETES ONLY its own test rows so the app
is left in a clean state. If today already has a real plan, it skips all
write tests and only reports health.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from db import connect, execute, one  # noqa: E402

BASE = "http://127.0.0.1:8000"


def call(path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode("utf-8") or "{}")
        raise RuntimeError(f"{path} -> HTTP {e.code}: {body.get('detail')}")


def spelling(progress_id):
    conn = connect()
    try:
        row = one(
            conn,
            "SELECT w.word FROM `progress` p JOIN `word` w ON w.id=p.word_id WHERE p.id=%s",
            (progress_id,),
        )
        return row["word"] if row else None
    finally:
        conn.close()


def cleanup(plan_id):
    conn = connect()
    try:
        conn.begin()
        execute(conn, "DELETE FROM `answer_log` WHERE daily_plan_id=%s", (plan_id,))
        execute(conn, "DELETE FROM `progress` WHERE daily_plan_id=%s", (plan_id,))
        execute(conn, "DELETE FROM `daily_plan` WHERE id=%s", (plan_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    h = call("/api/health")
    print(f"[1] health: db={h['database']} account={h['account']} "
          f"tables={h['tables']} words={h['word_count']}")

    conn = connect()
    try:
        cn = one(conn, "SELECT translation FROM `word` WHERE word='abandon'")
    finally:
        conn.close()
    assert cn and "放弃" in cn["translation"], "Chinese meaning is garbled"
    print(f"[2] utf8mb4 Chinese OK: abandon = {cn['translation']}")

    current = call("/api/state?mode=learn")
    if current["kind"] != "need_plan":
        print("Today already has a real study plan -> write tests skipped "
              "(nothing will be changed).")
        return

    plan = call("/api/plan", {"count": 18})
    plan_id = plan["plan_id"]
    print(f"[3] plan N=18 split into {plan['cycle_count']} cycles (expect 4)")
    assert plan["cycle_count"] == 4

    conn = connect()
    try:
        last = one(
            conn,
            "SELECT COUNT(*) AS c FROM `progress` "
            "WHERE daily_plan_id=%s AND cycle_no=4 AND round=1",
            (plan_id,),
        )["c"]
    finally:
        conn.close()
    assert last == 3, "last small cycle should contain 3 words"
    print(f"[3b] last small cycle has {last} words (5/5/5/3)")

    try:
        s = plan["state"]
        counts = {"r1": 0, "r2": 0, "r3": 0}
        first_r1 = True
        duplicate_checked = False
        wrong_checked = False
        while s["kind"] in ("r1", "r2", "r3"):
            kind, pid = s["kind"], s["progress_id"]
            counts[kind] += 1
            if kind == "r1":
                if first_r1:
                    r = call("/api/answer", {"progress_id": pid, "action": "unknown"})
                    assert r["reveal"] is True, "R1 unknown did not reveal meaning"
                    first_r1 = False
                r = call("/api/answer", {"progress_id": pid, "action": "known"})
                if not duplicate_checked:
                    dup = call("/api/answer", {"progress_id": pid, "action": "known"})
                    assert dup["duplicate"] is True, "duplicate submit advanced twice"
                    duplicate_checked = True
            else:
                word = spelling(pid)
                if kind == "r2" and not wrong_checked:
                    r = call("/api/answer", {"progress_id": pid, "action": "spell",
                                             "value": "__definitely_wrong__"})
                    assert r["correct"] is False and r["answer"] == word
                    wrong_checked = True
                    print("[4] wrong-answer reveal OK")
                # upper-case + trailing spaces must still pass normalisation
                r = call("/api/answer", {"progress_id": pid, "action": "spell",
                                         "value": word.upper() + "  "})
            s = r["state"]
        assert s["kind"] == "learning_done" and s["stage"] == "memorized"
        print(f"[5] R1-R3 complete {counts} -> {s['stage']}; "
              "duplicate-submit guard OK; normalisation OK")

        batches = call("/api/review-batches")["batches"]
        b = next(x for x in batches if x["plan_id"] == plan_id)
        assert b["memorized_at"] and not b["mastered_at"]
        print(f"[6] date batch archived: {b['study_date']} stage={b['stage']} "
              f"review {b['done']}/{b['total']}")

        s = call(f"/api/state?mode=review&plan_id={plan_id}")
        rc = {"r4": 0, "r5": 0}
        while s["kind"] in ("r4", "r5"):
            kind, pid = s["kind"], s["progress_id"]
            rc[kind] += 1
            if kind == "r4":
                r = call("/api/answer", {"progress_id": pid, "action": "r4_reveal",
                                         "value": "测试释义"})
                assert r["standard_answer"], "R4 standard answer missing"
                r = call("/api/answer", {"progress_id": pid, "action": "self_assess",
                                         "value": "测试释义", "assessment": "forgotten"})
            else:
                r = call("/api/answer", {"progress_id": pid, "action": "spell",
                                         "value": spelling(pid)})
            s = r["state"]
        assert s["kind"] == "review_done" and s["stage"] == "mastered"
        print(f"[7] R4-R5 complete {rc} -> {s['stage']}")

        b = next(x for x in call("/api/review-batches")["batches"]
                 if x["plan_id"] == plan_id)
        assert b["mastered_at"] and b["stage"] == "mastered"
        print("[8] two-level status stored separately: "
              f"memorized_at={b['memorized_at']} mastered_at={b['mastered_at']}")
    finally:
        cleanup(plan_id)
        print("[9] temporary test rows deleted")

    pool = call("/api/pool")
    print(f"[10] pool restored: {pool['available']}/{pool['total']}")
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
