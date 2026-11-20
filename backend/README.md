# backend · Python + FastAPI（完整版）

阶段 1～5 的服务端。浏览器只负责显示与发音，**所有身份、学习状态、判分、内容生成、落盘都在这里**。

## 启动

根目录 `setup.bat`（首次装环境）、`start.bat`（日常启动）。手动启动：

```
cd D:\kaoyanruanjian\backend
.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

打开 http://localhost:8000 ；自检 http://localhost:8000/api/health 。

## 启动时自动做的事

1. `db.init_schema()` 执行 `schema.sql`（10 张表全 `IF NOT EXISTS`），再跑 `ensure_columns()` 做**幂等增量迁移**：查 `information_schema` 后只 `ADD COLUMN` / `MODIFY ... NULL`，从旧库平滑升级，从不 drop / truncate / 删行。
2. `ensure_words()` 把 `data/kaoyan_core_words.csv` 用 `INSERT IGNORE` 补齐（按唯一索引去重）。ECDICT 的 4,701 个 `ky` 词已由 `scripts/import_ecdict.py` 一次性导入，共 4,803 词。
3. 起一个 daemon 线程做周五晚周阅读定时发布（每 10 分钟检查一次），页面打开时还有兜底补生成。

**不需要手动跑建表脚本**，启动即完成初始化。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| `main.py` | 全部路由 + 五轮状态机 + 后台定时发布 + 静态托管 |
| `auth.py` | pbkdf2 密码哈希、Session-Cookie、`current_user` / `require_admin` 依赖、首个注册继承旧进度 |
| `content.py` | 每日短文 / 周阅读生成、词形还原与释义快照、题目生成、判分 |
| `llm.py` | `generate_text(prompt)` 唯一生成入口：主源 + 兜底 + 本地确定性回退 |
| `db.py` | 连接 / 事务 / 幂等建表与增量迁移 |
| `schema.sql` | 10 张表定义 |
| `seed.py` | 内置词库与 ECDICT 导入 |

## 配置（`backend/.env`，gitignore）

| 键 | 说明 |
| --- | --- |
| `DB_HOST` / `DB_PORT` | 默认 `127.0.0.1` / `3306` |
| `DB_NAME` | **固定 `kaoyanruanjian`**，写成别的库名会直接拒绝启动 |
| `DB_USER` / `DB_PASSWORD` | `kaoyan_app` 单库账号；密码你自己设，别贴给任何人 |
| `LOCAL_MODE` | `1` 允许回环地址未登录时落到占位账号（保留旧进度）；`0` 强制登录 |
| `LLM_*` / `LLM_FALLBACK_*` | 可选。`BASE_URL` / `MODEL` / `API_KEY` 三家信息全走配置，不写死模型名；留空则本地生成 |
| `LLM_TIMEOUT` | 单次调用超时秒数，默认 12 |

## 关键约束

- `progress` 唯一键 `(user_id, word_id, round)`；取词只查 `status='pending'` 的最小 `cycle_no → round → id`，**不维护下标**。
- 所有业务 SQL 带 `user_id`；身份只从会话 cookie 解析，越权与不存在统一 404。
- `POST /api/answer` 里「改 `progress`」与「写 `answer_log`」在**同一事务**；更新带 `WHERE status='pending'` 防重复提交。
- 短文与阅读**不写 `progress`**；`article_word` 存生成时的释义快照；`article.revealed` 是落盘的显示模式，不是重新生成。
- `study_date` 用 MySQL `DATE` + `CURDATE()`；全链路 `utf8mb4`。
- 接口清单见 `docs/api-contract.md`，实现取舍见 `docs/design-decisions.md`。

## 依赖

`requirements.txt`：`fastapi` / `uvicorn` / `PyMySQL` / `python-dotenv`。装在 `.venv`，不污染系统 Python；哈希、HTTP 调用、定时任务都用标准库，没有额外依赖。
