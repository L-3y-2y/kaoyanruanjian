"""One-time migration: seed the Ebbinghaus review queue from pre-existing data.

Old model: R1-R3 -> memorized, R4-R5 -> mastered; review was batch-based.
New model: R1-R5 -> memorized (enters review pool), Ebbinghaus intervals -> mastered.

Rules (idempotent, safe to re-run):
- mastered words stay mastered.
- Historical batches (study_date < today) with unfinished rounds are completed
  and enter the review pool at stage=0 / due today.
- Today's batches are left alone (still being learned).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import db  # noqa: E402


def run():
    conn = db.connect()
    try:
        conn.begin()
        with conn.cursor() as cur:
            # 1. Complete unfinished rounds in historical batches.
            cur.execute(
                """UPDATE progress p
                   JOIN daily_plan dp ON dp.id = p.daily_plan_id
                   SET p.status = 'done'
                   WHERE dp.study_date < CURDATE() AND p.status = 'pending'"""
            )
            # 2. Promote those words to 'memorized' (mastered stays untouched).
            cur.execute(
                """UPDATE progress p
                   JOIN daily_plan dp ON dp.id = p.daily_plan_id
                   SET p.stage = 'memorized'
                   WHERE dp.study_date < CURDATE() AND p.stage = 'none'"""
            )
            # 3. Seed the review queue on each word's round=5 row.
            cur.execute(
                """UPDATE progress p
                   JOIN daily_plan dp ON dp.id = p.daily_plan_id
                   SET p.review_stage = 0, p.review_round = 0,
                       p.next_due_at = CURDATE()
                   WHERE dp.study_date < CURDATE() AND p.round = 5
                     AND p.stage = 'memorized' AND p.next_due_at IS NULL"""
            )
            # 4. Bump historical 'learning' batches to 'memorized'.
            cur.execute(
                """UPDATE daily_plan SET stage = 'memorized'
                   WHERE study_date < CURDATE() AND stage = 'learning'"""
            )
        conn.commit()
        print("Ebbinghaus migration completed.")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
