r"""Create / reset the fixed administrator account (A5).

Run once from anywhere:

    D:\kaoyanruanjian\backend\.venv\Scripts\python.exe scripts\create_admin.py

The password is typed locally, never echoed, never printed and never stored in
plain text (pbkdf2-sha256, 200k rounds, same as normal users). Admins can only
manage the word bank; they cannot read other users' learning data.
"""

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from auth import hash_password  # noqa: E402
from db import execute, init_schema, one, transaction  # noqa: E402


def main():
    init_schema()
    username = input("管理员用户名（直接回车用 admin）: ").strip() or "admin"
    pwd = getpass.getpass("设置管理员密码（至少 8 位，不回显）: ")
    confirm = getpass.getpass("再输一次确认: ")
    if pwd != confirm:
        print("两次不一致，未做任何修改。")
        return 1
    if len(pwd) < 8:
        print("密码不足 8 位，未做任何修改。")
        return 1
    with transaction() as conn:
        execute(
            conn,
            """INSERT INTO `admin` (`username`,`password_hash`) VALUES (%s,%s)
               ON DUPLICATE KEY UPDATE password_hash=VALUES(password_hash)""",
            (username, hash_password(pwd)),
        )
        row = one(conn, "SELECT id FROM `admin` WHERE username=%s", (username,))
    print(f"OK: 管理员 {username} 已就绪（id={row['id']}）。")
    print("打开 /admin 用这个账号登录即可；管理端右上角可以切到学员端或退出登录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
