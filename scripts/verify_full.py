r"""End-to-end self-check for the FULL application (stage 1-5).

Run while the backend is up:

    D:\kaoyanruanjian\backend\.venv\Scripts\python.exe scripts\verify_full.py

It exercises: legacy-data preservation, registration, session isolation,
R1-R5 with N=18 (5/5/5/3), daily articles (idempotent, reveal persistence,
word-form snapshots), weekly reading (2 passages, objective + AI-graded
subjective questions, duplicate guard, score), admin word-bank access
(loopback/local shortcut plus a real 403 check for ordinary accounts), and
admin-only user enumeration.

Every row it creates belongs to a throw-away test account and is deleted at
the end. It NEVER touches the pre-existing local plan (user_id=1): a guard row
is inserted first so the "first registration inherits local progress" logic
stays inactive during the test.
"""

import http.cookiejar
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from db import connect, execute, init_schema, one, query, transaction  # noqa: E402

# Allow a non-default port so the self-check can run beside a live server.
BASE = os.getenv("VERIFY_BASE", "http://127.0.0.1:8000")
GUARD = "__verify_guard__"
TEST_NAME = "__verify_" + secrets.token_hex(4)
TEST_PWD = "VerifyPass2026!"
TEST2_NAME = TEST_NAME + "_b"
cleanup_ids = []


class Client:
    def __init__(self):
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar)
        )

    def call(self, path, method="GET", body=None, headers=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        all_headers = {"Content-Type": "application/json"}
        if headers:
            all_headers.update(headers)
        req = urllib.request.Request(
            BASE + path, data=data, method=method, headers=all_headers,
        )
        try:
            # LLM generation (weekly reading = 2 passages + 10 questions) can
            # take well over 30s once a real key is configured.
            with self.opener.open(req, timeout=180) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise AssertionError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc


def snapshot():
    # A fresh connection each time: a long-lived REPEATABLE READ connection
    # would keep the old snapshot and miss rows written by HTTP requests.
    # The legacy plan id is discovered at runtime, never hard-coded, so this
    # script also works on a fresh install where no id=3 row exists.
    c = connect()
    try:
        row = one(c, "SELECT id, stage FROM `daily_plan` WHERE user_id=1 ORDER BY id DESC LIMIT 1")
        return {
            "plans": one(c, "SELECT COUNT(*) AS c FROM `daily_plan` WHERE user_id=1")["c"],
            "progress": one(c, "SELECT COUNT(*) AS c FROM `progress` WHERE user_id=1")["c"],
            "legacy_plan": (row["id"], row["stage"]) if row else None,
        }
    finally:
        c.close()


def spelling(progress_id):
    c = connect()
    try:
        return one(
            c,
            """SELECT w.word FROM `progress` p JOIN `word` w ON w.id=p.word_id
               WHERE p.id=%s""",
            (progress_id,),
        )["word"]
    finally:
        c.close()


def scalar(sql, args=()):
    c = connect()
    try:
        return one(c, sql, args)
    finally:
        c.close()


def drive_rounds(cli, mode, plan_id, expected):
    counts = {}
    for _ in range(600):
        s = cli.call(f"/api/state?mode={mode}")
        kind = s["kind"]
        if kind in ("learning_done", "review_done", "review_empty"):
            print(f"  [{mode}] finished -> {kind}")
            break
        pid = s["progress_id"]
        if mode == "learn":
            assert kind.startswith("r"), f"unexpected state {kind}"
            counts[kind] = counts.get(kind, 0) + 1
            r = s["round"]
            if r == 1:
                payload = {"action": "known"}
            elif r in (2, 3):
                payload = {"action": "spell", "value": s["speak_text"]}
            elif r == 5:
                payload = {"action": "spell", "value": spelling(pid)}
            elif r == 4:
                shown = cli.call("/api/answer", "POST", {
                    "progress_id": pid, "action": "r4_reveal", "value": "测试作答"})
                assert shown["standard_answer"] is not None, "R4 must show the standard answer"
                payload = {"action": "self_assess", "value": "测试作答",
                           "assessment": "remembered"}
            else:
                raise AssertionError(f"unexpected round {r}")
        else:  # review: Ebbinghaus three sub-steps (认识 -> 听写 -> 默写)
            assert kind == "review", f"unexpected state {kind}"
            sub = s["sub_step"]
            counts[f"step{sub}"] = counts.get(f"step{sub}", 0) + 1
            payload = ({"action": "r_known"} if sub == 1
                       else {"action": "r_spell", "value": spelling(pid)})
        result = cli.call("/api/answer", "POST", {"progress_id": pid, **payload})
        assert result["duplicate"] is False, "duplicate-submit guard tripped unexpectedly"
    for kind, want in expected.items():
        assert counts.get(kind, 0) == want, f"{kind}: {counts.get(kind, 0)} != {want}"
    print(f"  [{mode}] per-round counts: {counts}")
    return s


def cleanup(conn):
    for uid in cleanup_ids:
        for table in ("answer_log", "article_word", "question", "article",
                      "progress", "daily_plan", "user_session"):
            execute(conn, f"DELETE FROM `{table}` WHERE user_id=%s", (uid,))
        execute(conn, "DELETE FROM `user` WHERE id=%s", (uid,))
    execute(conn, "DELETE FROM `user` WHERE username IN (%s,%s)", (GUARD, TEST2_NAME))
    execute(conn, "DELETE FROM `admin` WHERE username=%s", (GUARD,))


def main():
    init_schema()
    try:
        before = snapshot()
        print(f"[0] legacy data before: {before}")
        has_legacy = before["plans"] >= 1
        if not has_legacy:
            # Clean CI / fresh database: no local plan to protect. The rest of
            # the suite is self-contained (it creates its own throw-away plan),
            # so we still run everything and just skip the legacy-guard checks.
            print("[0] no pre-existing local plan (clean environment) - "
                  "running without legacy-data assertions")

        health = Client().call("/api/health")
        print(f"[1] health: {health['database']} / {health['tables']} tables / "
              f"{health['word_count']} words / llm={health['llm_configured']}")
        assert health["ok"] and health["database"] == "kaoyanruanjian"

        # Guard prevents the first-registration inheritance during this test.
        with transaction() as c:
            execute(c, "DELETE FROM `user` WHERE username=%s", (GUARD,))
            execute(c, "INSERT INTO `user` (`username`,`password_hash`) VALUES (%s,%s)",
                    (GUARD, "guarded"))
            gid = one(c, "SELECT LAST_INSERT_ID() AS id")["id"]
        cleanup_ids.append(gid)

        cli = Client()
        reg = cli.call("/api/auth/register", "POST",
                       {"username": TEST_NAME, "password": TEST_PWD})
        uid = reg["user"]["id"]
        cleanup_ids.append(uid)
        assert reg["inherited_local_progress"] is False, "legacy data must NOT move"
        print(f"[2] registered user {uid}; legacy inheritance skipped (guard works)")

        me = cli.call("/api/me")
        assert me["user"]["id"] == uid and not me["user"]["local"]
        login = Client()
        logged = login.call("/api/auth/login", "POST",
                            {"username": TEST_NAME, "password": TEST_PWD})
        assert logged["ok"]
        print("[3] session cookie + login OK")

        # Admin word-bank access: the loopback caller (no cookie) is the local
        # account and may use the word bank by design; a real non-admin account
        # must be refused with 403. Both paths are now actually exercised.
        local_admin = Client().call("/api/admin/stats")
        assert local_admin["users"] >= 0 and "sources" in local_admin
        try:
            cli.call("/api/admin/stats")
            raise AssertionError("ordinary account must NOT reach admin stats")
        except AssertionError as exc:
            if "HTTP 403" not in str(exc):
                raise
        try:
            cli.call("/api/admin/users")
            raise AssertionError("ordinary account must NOT enumerate users")
        except AssertionError as exc:
            if "HTTP 403" not in str(exc):
                raise
        print("[3a] admin word bank: local allowed / ordinary account 403 / users 403")

        pool = cli.call("/api/pool")
        assert pool["available"] == pool["total"], "new account must see a full pool"
        plan = cli.call("/api/plan", "POST", {"count": 18})
        plan_id = plan["plan_id"]
        assert plan["cycle_count"] == 4
        print(f"[4] plan N=18 -> {plan['cycle_count']} cycles, plan_id={plan_id}")
        with transaction() as c:
            last = one(c, "SELECT COUNT(*) AS c FROM `progress` "
                       "WHERE daily_plan_id=%s AND cycle_no=4 AND round=1", (plan_id,))["c"]  # noqa: F841
        assert last == 3, "last cycle should hold 3 words"

        drive_rounds(cli, "learn", plan_id, {"r1": 18, "r2": 18, "r3": 18, "r4": 18, "r5": 18})
        stats = cli.call("/api/review/stats")
        assert stats["pool"] == 18, "学完 5 轮后 18 个词都应进入复习池"
        print("[5] R1-R5 done -> memorized + 进复习池(18 词)")

        # 新学词的首次复习排期在 +1 天；测试里把到期日改成今天，模拟「明天到了」。
        with transaction() as c:
            execute(c, "UPDATE `progress` SET next_due_at=CURDATE() WHERE daily_plan_id=%s AND round=5", (plan_id,))
        drive_rounds(cli, "review", plan_id, {"step1": 18, "step2": 18, "step3": 18})
        with transaction() as c:
            stages = one(c, "SELECT MIN(review_stage) AS mn, MAX(review_stage) AS mx "
                            "FROM `progress` WHERE daily_plan_id=%s AND round=5", (plan_id,))
        assert stages["mn"] == 1 and stages["mx"] == 1, "一轮复习三轮后 review_stage 应 0->1"
        print("[6] 复习三轮(认识/听写/默写) -> review_stage 0->1, 下次到期 +2 天")

        ab = cli.call("/api/articles/batches")["batches"]
        assert len(ab) == 1, "学完 5 轮的批次就该出现在短文列表里"
        first = cli.call(f"/api/articles/daily?plan_id={plan_id}")
        assert first["mode"] == "full" and len(first["articles"]) == 2
        a0 = first["articles"][0]
        assert a0["revealed"] is False and a0["segments"] is None, \
            "first render must be plain text with no clickable data"
        ids1 = [a["id"] for a in first["articles"]]
        print(f"[7] daily articles: {len(ids1)} passages, plain text on first render")

        rev = cli.call("/api/articles/reveal", "POST", {"article_id": a0["id"]})
        segs = rev["segments"]
        words = [s["word"]["surface"] for s in segs if s["word"]]
        assert words and any(w[0].isupper() or w.endswith(("s", "ed", "ing"))
                             for w in words), "word-form marking must work"
        second = cli.call(f"/api/articles/daily?plan_id={plan_id}")
        assert [a["id"] for a in second["articles"]] == ids1, "articles must be reused"
        # Reveal is now a per-view action: a refresh goes back to plain text,
        # while the translation snapshots remain in article_word.
        assert second["articles"][0]["revealed"] is False and \
            second["articles"][0]["segments"] is None, "refresh must start plain again"
        rev_again = cli.call("/api/articles/reveal", "POST", {"article_id": a0["id"]})
        assert [s["word"]["surface"] for s in rev_again["segments"] if s["word"]] == words, \
            "snapshots must survive a reveal/refresh cycle"
        print(f"[8] per-view reveal (refresh resets to plain), {len(words)} marked surfaces "
              f"(incl. capitals / inflections): {words[:6]}")

        pub = cli.call("/api/reading/publish-now", "POST")
        assert pub["article_count"] == 2
        reading = cli.call("/api/reading")
        assert reading["status"] == "published" and len(reading["articles"]) == 2
        questions = [q for a in reading["articles"] for q in a["questions"]]
        assert len(questions) >= 8
        print(f"[9] weekly reading: 2 passages, {len(questions)} questions, "
              f"deadline {reading['deadline']}")

        objective = [q for q in questions if q["options"]]
        subjective = [q for q in questions if q["ai_graded"]]
        assert objective and subjective
        for q in objective[:2]:
            key = scalar("SELECT correct_option FROM `question` WHERE id=%s",
                         (q["id"],))["correct_option"]
            r = cli.call("/api/reading/answer", "POST",
                         {"question_id": q["id"], "week_key": reading["week_key"], "value": key})
            assert r["correct"] is True and not r["duplicate"]
            dup = cli.call("/api/reading/answer", "POST",
                           {"question_id": q["id"], "week_key": reading["week_key"], "value": key})
            assert dup["duplicate"] is True, "exam answers must not be double counted"
        ref = scalar("SELECT reference_answer FROM `question` WHERE id=%s",
                     (subjective[0]["id"],))["reference_answer"] or ""
        sr = cli.call("/api/reading/answer", "POST",
                      {"question_id": subjective[0]["id"],
                       "week_key": reading["week_key"],
                       "value": ref[:60] + "，我根据上下文判断。"})
        assert sr["reason"], "AI/rule grading must store a reason"
        print(f"[10] objective + duplicate guard OK; subjective score={sr['score']} "
              f"reason={sr['reason'][:40]}")

        scored = cli.call("/api/reading")
        assert scored["summary"]["answered"] >= 3
        print(f"[11] score summary: {scored['summary']}")

        # Second account must see none of the first account's data.
        with transaction() as c:
            execute(c, "INSERT INTO `user` (`username`,`password_hash`) VALUES (%s,%s)",
                    (TEST2_NAME, "guarded"))
            uid2 = one(c, "SELECT LAST_INSERT_ID() AS id")["id"]
            token = secrets.token_hex(32)
            from datetime import datetime, timedelta
            execute(c, "INSERT INTO `user_session` (`token`,`user_id`,`expires_at`) "
                    "VALUES (%s,%s,%s)",
                    (token, uid2, datetime.now() + timedelta(days=1)))
        cleanup_ids.append(uid2)
        cli2 = Client()
        hdr = {"Cookie": f"ky_session={token}"}
        assert cli2.call("/api/state?mode=learn", headers=hdr)["kind"] == "need_plan"
        assert cli2.call("/api/articles/batches", headers=hdr)["batches"] == []
        # Account B has no mastered batch at all: exercises the weekly-reading
        # fallback path (MySQL DISTINCT/ORDER BY compatibility, error 3065).
        pub2 = cli2.call("/api/reading/publish-now", "POST", headers=hdr)
        assert pub2["article_count"] == 2
        print("[12] multi-user isolation OK; empty-week weekly fallback OK")

        # [12a] model allowlist + switching. Only exercised when a key is
        # configured; the active model is always restored afterwards so the
        # check never leaves the user's .env changed.
        health = cli.call("/api/health")
        if health.get("llm_configured") and health.get("llm_model"):
            original = health["llm_model"]
            # Switching/probing the model is admin-only. `cli` carries the
            # throw-away learner cookie, so it is correctly rejected; use a
            # fresh loopback client, which resolves to the local admin.
            admin_cli = Client()
            info = admin_cli.call("/api/llm/models")
            assert info["models"] and any(m["current"] for m in info["models"]), \
                "model list must mark the active model"
            assert all("key" not in m and "base_url" not in m for m in info["models"]), \
                "model list must never leak keys or endpoints"
            other = next((m["id"] for m in info["models"]
                          if m["id"] != original and not m["note"].startswith("Agnes")), None)
            try:
                if other:
                    switched = admin_cli.call("/api/llm/model", "POST",
                                              body={"model": other})
                    assert switched["model"] == other
                    assert admin_cli.call("/api/health")["llm_model"] == other
                    print(f"[12a] model switch OK: {original} -> {other}")
                probe = admin_cli.call("/api/llm/test", "POST")
                assert "ok" in probe and "model" in probe, \
                    "test endpoint must return a verdict"
                print(f"[12a] model probe returned ok={probe['ok']} "
                      f"(busy providers are reported, not hidden)")
            finally:
                # Restore in all cases, including an assertion failure above.
                admin_cli.call("/api/llm/model", "POST", body={"model": original})
                assert admin_cli.call("/api/health")["llm_model"] == original
        else:
            print("[12a] model switching skipped (no LLM key configured)")

        final = snapshot()
        assert final == before, f"legacy data changed! {before} -> {final}"
        print(f"[13] legacy local plan intact: {final}")
        print("\nALL CHECKS PASSED")
    finally:
        try:
            with transaction() as c:
                cleanup(c)
        except Exception as exc:
            print(f"cleanup warning: {exc}", file=sys.stderr)
        else:
            print("[cleanup] all test rows removed")


if __name__ == "__main__":
    main()
