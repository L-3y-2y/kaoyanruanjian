# 实现决策记录（v2 · 阶段 1～5 完整版）

> 需求文档 0.3 节列了一批「写到那一步再处理，都有默认」的实现细节。这里把本次实现**实际采用的默认**逐条写清楚，方便以后回看或改。
> 这些是**实现选择，不是需求变更**；凡涉及需求定义的，一律以 `docs/需求与设计骨架.md` 为准。

## 1. 本次做到哪一步

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 0 / 5.0 | 环境准备：MySQL 8.0、`utf8mb4`、单库 `kaoyanruanjian`、`kaoyan_app` 单库授权、干净连接、`.env`、`.gitignore` | ✅ 已完成（在旧会话中） |
| 1 | R1→R2→R3 状态机、`speechSynthesis` 发音、进度不靠下标 | ✅ |
| 2 | FastAPI + MySQL、进度持久化、事务、接口契约、中文不乱码 | ✅ |
| 3 | 词库、每天自选 N（滑轴 1～100）、5 词小周期、学习 R1～R5 一次学完 | ✅ |
| 4 | 注册登录、密码哈希、多用户隔离、管理员词库维护 | ✅ |
| 5 | 每日短文、每周阅读、AI 判分、周五晚定时 | ✅ |

需求文档 9 张表全部落地，另加一张会话表。阶段 4 起每次启动执行**幂等增量迁移**（`db.ensure_columns` 查 `information_schema` 后只 `ADD COLUMN` / `MODIFY ... NULL`），从阶段 1～3 的旧库平滑升级，**没有 drop、truncate 或删行**。

## 2. 表（10 张 + 旧演示 3 张）

| 表 | 用途 |
| --- | --- |
| `user` | 账号；`password_hash` 存 pbkdf2；`sample_cursor` 轮转抽词 |
| `admin` | 管理员（`scripts/create_admin.py` 初始化） |
| `user_session` | 会话 token、过期时间、`is_admin` |
| `word` | 词库（`word` 唯一索引、`source` / `verified`、`exchange` 供词形还原） |
| `daily_plan` | 每日计划 = **日期批次本身**，不另加表 |
| `progress` | 每词每轮一条，含 `cycle_no` / `round` / `status` / `wrong_count` / `stage` / `review_round` / `review_stage` / `next_due_at` |
| `answer_log` | 学习答题 + 阅读答卷共用；`kind='word'|'exam'`，含 `score` / `scored` / `is_late` / `ai_result` / `question_id` / `week_key` |
| `article` | 每日短文 / 周阅读共用；`revealed` / `mode` / `uncovered` / `publish_at` / `deadline` / `generator` |
| `article_word` | 文章-词关联 + **当时的释义快照** |
| `question` | 阅读题目、选项 JSON、正确答案 / 参考答案、`ai_graded` |

`article` 的两个唯一键互补：`(user_id,kind,daily_plan_id,seq)` 约束每日短文（此时 `week_key` 为 NULL），`(user_id,kind,week_key,seq)` 约束周阅读（此时 `daily_plan_id` 为 NULL）。MySQL 唯一索引允许多个 NULL，正好各管一半，从数据库层保证「重复进入不重新生成」。

增量迁移把 `answer_log` 的 `word_id / daily_plan_id / progress_id / round` 放宽为 NULL —— 阅读答卷没有这些字段；**已有行的值全部保留**。旧演示项目的 `subjects / study_tasks / checkins` 三张表与本软件无关，仍不删除。

## 3. 进度模型

- 一个词在一个用户下的 **round 1～5 各一条记录**，唯一键 `(user_id, word_id, round)`；建计划时一次性插好，全部 `pending`。
- 学习态查 `status='pending'`（R1～R5 未走完），复习态查 `round=5 AND stage='memorized'` 且 `next_due_at` 到期，两个阶段天然不会串轮次。
- 「未掌握池」= `word` 中**从未出现在 `progress` 里的词**。进过任一批次的词不再被选，保证「同一个词不会落在两个日期组」。
- `daily_plan` 存两个独立时间戳：`memorized_at`（学完 5 轮）与 `mastered_at`（复习背熟）。**两级状态分开存、分别可查**，不是用一个布尔值糊过去；`stage` 只是它俩派生出的展示值。
- 一个日期批次 R1～R5 全完成后即写 `memorized_at` 并进复习池，不再有额外关卡。该词 6 个艾宾浩斯间隔全走完时写 `mastered_at`（背熟）。语境填空放在短文页，只做巩固，不改进度。

## 4. 取词顺序：按小周期循环

需求 0.1 表格写「R3 完成后回到 R1 开始下一个 5 词小周期」，1.2 的伪代码却写「round 最小、cycle_no 最小」。两者排序优先级不同，本次采用**小周期优先**：

```
ORDER BY cycle_no ASC, round ASC, id ASC
```

即：第 1 组的 5 个词先走完 R1 → 同组 R2 → 同组 R3，再进入第 2 组。这样一次只记 5 个词，符合「小周期」的初衷，也让「R3 后回到 R1 开始下一组」这句话成立。
若以后想改成「当天 N 个词先全部 R1、再全部 R2」，只需把 `backend/main.py` 里这一处 `ORDER BY` 换成 `round ASC, cycle_no ASC`，**不用改表、不用改前端**。

## 5. 判分归一化

R2 / R3 / R5 比对英文拼写，前后统一处理：

1. 去首尾空白
2. 内部连续空白合并为一个空格
3. 忽略大小写
4. **保留连字符**（`well-known` 这类词本身带连字符，去掉反而判错）
5. 短语按整串比对，不拆词

R4 的汉语释义**不机器判分**：需求定的是「写完显示标准答案 → 用户自评」，所以只记录用户写了什么并返回词库主释义，判分权交给用户。

## 6. 答案出现 / 消失

- 答错时服务端返回 `answer`，前端在输入框下方显示正确单词。
- 用户在输入框里**开始重新输入的那个 `input` 事件**就清空答案，不用定时器。
- 刷新后答案状态不恢复（它是瞬时呈现状态，不是学习进度），符合「前端不缓存进度」。
- R1 点「陌生」会展开音标 + 释义，并把 `wrong_count + 1` 记进服务端；再点「认识」才算过。狂点「认识」不会留下陌生计数，但也不会被拦 —— 统计字段在，后续可加限制。

## 7. R4「没记住」

只写进 `answer_log.self_assessment` 并计入统计，**不改变本批次流程、不重排词、不触发重填**。需求 4.4 已经把 R4 定成自评，不是答错重填循环。

## 8. N 不被 5 整除

`cycle_no = (index // 5) + 1`，最后一组允许 1～4 个词，`cycle_count = ceil(N/5)`。N=18 → 4 组（5/5/5/3）。前端进度条按组显示，不假设每组都是 5。

## 9. 发音

浏览器 `speechSynthesis`，`lang='en-US'`，零依赖、不用 Key。前端封装成一个 `playWord(text)` 函数，以后换预下载音频 / 在线 TTS 只改这一处。**这只是代码组织方式，不是新增需求**。

## 10. 连接与事务

- 凭据只在 `backend/.env`，Python 侧用 `python-dotenv` 读取，永不进浏览器、不进 Git。
- 每次请求取一个连接，`autocommit=False`；`POST /api/answer` 与 `POST /api/plan` 显式 `begin / commit / rollback`。
- 自用小软件每次新建连接足够；并发上来后再换连接池，**不改业务代码**。
- 后端启动时自动执行 `backend/schema.sql`（全部 `CREATE TABLE IF NOT EXISTS`），重复启动安全。

## 11. 日期与字符集

- `study_date` 用 MySQL `DATE`，取值 `CURDATE()`（**服务端日期**），不用浏览器时间、不用带时区类型。
- 库 / 表一律 `utf8mb4`；后端连接显式 `charset='utf8mb4'`；中文释义、中文题干、中文提示都走这一条链路。
- 前端 `index.html` 声明 `<meta charset="utf-8">`，CSV 词库用 UTF-8 读取。

## 12. 词库

- 内置 `data/kaoyan_core_words.csv`（102 个高频样本）由 `scripts/seed_words.py` 导入。
- **已导入 ECDICT 官方 `ecdict.csv`（77 万词条）中 `tag` 含 `ky` 的 4,701 个考研词**，词库共 **4,803 词**。导入用 `INSERT IGNORE` + `word` 唯一索引，原有 102 词的 id 不变，已有 `progress.word_id` 引用不受影响。
- ECDICT 释义里的字面 `\n` 统一转成 `; `，方便前端显示与判分要点切分；`exchange` 原样保留供词形还原。
- ECDICT 导入的词 `verified=0`，需求 B1 要求的「按当年大纲抽查校验」仍需人工做；ECDICT 是社区整理，不是官方大纲原文。
- `data/ecdict.csv` 62 MB 已被 `.gitignore` 排除，不进仓库。

## 13. 前端形态

- Vue 3 + Vite，阶段 1 时是单组件；阶段 4/5 功能变多后拆成 **1 个外壳 + 6 个子组件**（`App.vue` + AuthPanel / LearnView / ReviewView / ArticlesView / ReadingView / AdminView）。仍**不上路由 / Pinia / UI 组件库**，页签就是外壳里的一个 ref，共享的刷新函数用 `provide/inject` 传下去 —— 没有引入全家桶。
- 开发期两端口：Vite `:5173` 把 `/api` 转发到 FastAPI `:8000`；交付期 `npm run build` 出 `dist/`，由 FastAPI 同源托管，一个端口 `:8000`（A4）。
- 前端所有状态来自服务端接口，只有「输入框内容、答案是否正显示、当前在哪个页签」这类纯 UI 状态留在组件里；`fetch` 统一带 `credentials:'include'`。

---

# 阶段 4 / 5 新增决策

## 14. 旧数据不丢：本机模式 + 首个注册继承

阶段 1～3 的进度挂在占位账号 `user_id=1` 下。直接加登录会把用户正在做的 10 词计划「锁」在一个没人能登录的账号里，所以：

- `LOCAL_MODE=1`（默认）时，**来自回环地址且没有会话 cookie 的请求落到 id=1**，打开网址照旧能用；非回环地址必须登录。设 `LOCAL_MODE=0` 可强制全局登录。
- 第一个真人账号注册时，在同一事务里把 id=1 的 `daily_plan / progress / answer_log / article / article_word / question` 整体改挂到新账号，并标记 `inherited_local_progress=true`。之后不再触发（用「除本次新账号外是否还有带密码的用户」判断）。
- `user_session` **不参与**迁移：占位账号没有会话，而 `admin` 表也可能有 id=1，误改会把管理员会话串到普通账号。
- 端到端自检脚本先插一个 guard 用户，确保测试注册不会触发继承；脚本前后各做一次老数据快照比对。

## 15. 密码与会话

- 标准库 `hashlib.pbkdf2_hmac('sha256', pwd, 16-byte salt, 200_000)`，格式 `pbkdf2_sha256$rounds$salt$hash`，比对用 `hmac.compare_digest`。不引第三方依赖，安全强度与文档说的 bcrypt/passlib 属同一档。列名固定 `password_hash`。
- 会话 token `secrets.token_hex(32)` 存 `user_session`，cookie `ky_session` 设 `HttpOnly / SameSite=Lax / 30 天 / path=/`。`resolve_user` 只从 cookie 取身份，**前端传任何 user_id 都不采信**。
- 越权与不存在统一返回 404，不泄露「这个 id 存在但不属于你」。本机模式仅放行 `127.0.0.1 / ::1 / localhost`。

## 16. 生成层：一个函数、两家兜底、离线可用

`llm.generate_text(prompt)` 是唯一调用入口，`generate_json(prompt, validator)` 在其上做 JSON 提取与一次修复重试；短文、阅读题目、主观题判分共用。主源与兜底源的 `base_url / model / api_key` 全从 `.env` 读，**不写死模型名**（需求 3.3 / B3）。实际配置建议主用智谱 `glm-4-flash`（永久免费、国内直连），Agnes 作兜底。

当前没配 Key：`configured()` 为 false，走**本地确定性生成器**——用句式模板把每个目标词写进一段可读英文，覆盖率天然 100%，短文与题目照常生成、照常可点、照常可判分。顶部状态条如实显示「本地生成」。配好 Key 后同一链路自动切到模型，并对返回结果做覆盖率校验，漏词自动补句。两家都失败会抛 `GenerationFailed` 并给明确重试提示，不会卡死界面。

> 这是本次唯一「功能在、但内容质感依赖外部 Key」的地方，README 已如实说明，不冒充接了大模型。

## 17. 每日短文：全放 / 抽词 / 揭示

- 只在 `daily_plan.stage IN ('memorized','mastered')`（学完 5 轮）后出现；`GET /api/articles/daily` 先查已有行，有就直接返回，**按钮和接口都不重新生成**。
- 每篇 12 词、最多 3 篇，容量 36 词。N≤36 → `mode='full'`；N>36 → `mode='sample'`，按 `wrong_count` 降序优先错得多的词，并用 `user.sample_cursor` 轮转起点，保证长期每个词都轮得到；`uncovered[]` 明确列出未收录词，`summary` 用文档 4.1 的措辞告知。
- **首次渲染纯文本**：`revealed=false` 时接口连 `segments` 都不返回（`null`），前端只输出 `{{ body }}`，无任何可点元素，目标词外观与其它词完全一致（A4）。
- 底部按钮「显示今日词」→ 按 `article_word` 把目标词包成 `segments`，点词显示**生成时存下的释义快照**（A3 / A6 / A7）。**揭示改为每次视图手动触发，不再 `UPDATE revealed` 落盘**：`article_payload` 恒返回 `revealed=false / segments=null`，刷新后回到纯文本，可再点按钮重新显示。释义快照常驻 `article_word`，三个月前点开仍是当时的意思（2026-11-20 按用户反馈调整：原来一点就永久高亮，用户要的是每次手动点亮）。
- 新增 `POST /api/articles/regenerate`：用当前大模型按原场景重写已生成的 daily 短文，保留 article id 与幂等语义，重建 `article_word` 快照；未配 Key 时返回 400 提示，不会用本地模板覆盖已有文章。用于把早期 local 模板生成的"硬凑句"文章升级为模型正文。
- 词形还原：用 ECDICT `exchange`（`s:/d:/p:/3:/i:` 等）+ 常见后缀规则建 `surface → word` 映射，再扫描正文 token。句首大写、复数、过去式、ing 都能点；含空格的短语动词按整体正则匹配（4.3）。本地正文里还专门有一行 `Word forms you may also meet: ...`，让变形**真实出现在正文**而不是只在映射表里。

## 18. 周次、发布与逾期

- 周次用 ISO 周 `YYYY-Www`，`date.fromisocalendar` 算周一～周日；`publish_at = 周五 18:00`，`deadline = 周日 23:59:59`。用 Python/MySQL 本地时间，不用带时区类型，避免跨天串批次。
- 后端 lifespan 起一个 daemon 线程，每 10 分钟检查一次是否该发布（`_publish_due_week`）；`GET /api/reading` 再做一次打开时兜底；`scripts/weekly_publish.py` 可另挂 Windows 计划任务。三条路都幂等。
- 本周词 = 本周内 `stage='mastered'` 的批次取并集；本周没有就用最近已掌握的批次兜底；2 篇放不下就抽词并列出未收录词。
- 逾期（`now > deadline`）仍可补做，`scored=0`、`is_late=1`，成绩汇总不计入（B5）。
- 「提前生成本周（试用）」是主动按钮，只生成本人那一周，方便周五 18:00 前体验；默认行为仍是定时发布。

## 19. 判分

- 客观四选一：`value.upper() == correct_option`，直接比对（B4）。**未作答前接口不下发 `correct_option` / `standard_answer`**，防止看答案。
- 简答题：走 `llm.grade_subjective`，配了 Key 就让模型返回 `{score, reason}`；没配或调用失败则按参考答案要点覆盖率 + 长度给分，理由写明命中几个要点。`answer_log.ai_result` 存 `{reason, provider}`。
- 每道题只记一次答卷，重复提交返回 `duplicate:true`，不重复计分。成绩按「用户 + 周次」聚合。
- 短文与阅读**全程不写 `progress`**，不改 `memorized_at` / `mastered_at`（4.5 最后一条）。

## 20. 管理员范围

`admin` 账号由 `scripts/create_admin.py` 交互创建，密码自己输、不回显、不明文存。管理员只有 `GET /api/admin/stats`、`GET/POST /api/admin/words|word` 三个词库接口，读不到别人的进度/答卷。
本机模式（`LOCAL_MODE=1` + 回环）视为完全可信，也放行这三个接口并出现「词库管理」页签，方便单机自用；一旦有真实账号登录，非管理员一律 403。

## 21. 本次交付的验证口径

`scripts/verify_full.py` 覆盖 13 项：老数据快照前后一致、注册/登录/Session、N=18 → 4 组 5/5/5/3、R1～R5 各 18 题、两级状态、短文幂等与首次纯文本、揭示持久与词形标注、周阅读 2 篇 10 题、客观判分 + 主观判分理由、防重复提交、成绩汇总、多用户隔离。测试数据全部落在临时账号下并在 `finally` 中删除；脚本用短连接查询避免 REPEATABLE READ 快照看不到 HTTP 事务新写入的行。

## 22. 后续增强（2026-11-20）

- 生成链：主源改推荐智谱 `glm-4-flash`（永久免费、国内直连），Agnes 作兜底；JSON 输出 + 词形还原覆盖率校验 + 漏词重写一次；周阅读题目由模型按正文一次生成，模板只作兜底；主观题判分带缓存。
- 场景化：`article.scene` 字段记录生成时的场景，已生成文章不重写，保持幂等。
- 语境完形：直接用 `article_word` 的释义快照在前端拼题，不新增表、不写 `progress`。
- 间隔复习：已由「可选增强」重构为内置的艾宾浩斯复习（见第 23 节）。
- 模型切换：界面只下发服务端 `LLM_MODEL_PRESETS` 白名单里的模型名，**Key 与 base_url 永不下发到浏览器**（守住 `llm.py` 的安全边界）。切换同时做三件事——改 `os.environ`（`_slot()` 每次调用都读，故下次生成立即生效、无需重启）、清判分缓存（换模型后旧缓存失效）、把 `LLM_MODEL=` 行写回 `.env`（重启后保持）。写回用逐行替换且加线程锁，Key 行与其他配置原样保留。另加 `POST /api/llm/test` 真发一句极简请求，把 HTTP 429 等真实错误直接显示出来，修掉「状态条显示已接、实际限流却静默回退」的误导。自检 `[12a]` 验证切换→探测→恢复闭环，跑完必恢复原模型。

## 23. 学习 / 复习重构（2026-11-22）

按用户拍板，把「学习」和「复习」彻底分开：

- **学习**：R1 认词 → R2 听音默写 → R3 看义默写 → R4 看词写义 → R5 看义默写，五轮在「学习」视图一次学完，走完即 `stage=memorized` 进复习池，并解锁每日短文（不再卡 mastered）。
- **复习**：真正的艾宾浩斯遗忘曲线，词级调度，间隔 1/2/4/7/15/30 天；每次复习走三轮（认不认识 → 听音默写 → 汉译英默写），三轮过 → `review_stage+1`，6 个间隔全过 → `mastered`；不认识/拼错 → 间隔归零、明天再来。
- **熟知按钮**：`POST /api/word/known` 一键把该词标背熟，跳过学习和复习。
- **数据**：`progress` 新增 `review_round`（当前间隔内子步 0/1/2）；删除旧的批次级复习接口（`/api/review-batches`、`/api/review/restart`、`/api/review/due`、`SMART_REVIEW`），新增 `/api/review/stats`。
- **迁移**：`scripts/migrate_ebbinghaus.py` 把历史批次未学完的词补齐进复习池，mastered 词保持。

## 24. 多书籍 + 情景式背词 A/B/C（2026-11-27）

**为什么按书隔离而不是全书共用**：approach 这类词同时带 `cet4/cet6/ky` 标签，若 progress 只有 `(user, word, round)` 唯一键，换书后进度会串，用户会遇到「选了四级书，词池里却混着考研学过的词」。用户拍板「每本书独立进度」，因此把唯一键扩成 `(user, book, word, round)`，`daily_plan` 的日唯一键扩成 `(user, book, date)`，周阅读文章唯一键扩成 `(user, kind, week_key, seq, book)`。老数据全部标 `book_id=1`，迁移脚本幂等无损。

**书籍是标签过滤，不是词条复制**：ECDICT 的 `tag` 本来就带多个考试标签，一个词天然属于多本书。`book.tag_filter` 存 `ky/cet4/cet6/gk`，取词用 `CONCAT(' ', tag, ' ') LIKE '% ky %'` 保证边界匹配；`INSERT IGNORE` 让新导入的词不覆盖已有词。词库从 4803 涨到 7395 属预期。

**四条生成链路的一致性**：短文、周阅读题目、AI 助教、练习卡都从 `user.current_book_id` 读考试类型并注入 prompt，做到「难度 + 词汇范围跟书走」。明确**不做**各考试专属题型（高考语法填空等）——那是二期。已生成的内容不因换书重写；已开的 AI 会话沿用建会话时的书，避免中途串味。

**语境填空的定位**：不单独做成学习后的关卡，而是并入已有的每日短文环节，形成「五轮学习 → 短文理解 → 语境填空 → 次日复习」一条记忆链。理由：短文中原有的语境题是四选一选中文义，属于被动识别；改成看句填英文单词才是主动提取，正好补上「看懂但想不起」的短板，也避免和五轮默写雷同。题目按 `(article, word, source)` 幂等生成，但**两类都不取原文句**：每句按当前书考试级别现写，并与该词历史句子做相似度去重，避免"刚读过原文又原句挖空"的复制感；接口只下发挖空句，不给翻译/词性等提示。答案只存 `article_cloze.answer`，接受原形或句中词形。判分写 `answer_log`，答错时置 `progress.cloze_wrong=1` 让次日复习优先安排，复习通过后清零——这是唯一写回 progress 的地方，不动五轮状态。图片联想方案已取消。周五定时任务只为「该书有 mastered 批次」的用户生成周阅读，避免每次启动为四本书盲生成浪费 LLM 额度。

## 25. 界面骨架改为「移动优先」（2026-11-28）

**为什么换骨架而不是换配色**：用户反馈页面单调。真正的问题是层级——原来所有模块都是同一张白卡、同一套靛蓝渐变，导航是顶部一排 chip，手机上要么挤成两行、要么横向溢出。对比四版框架（侧边栏应用 / 顶栏单栏 / 图标栏双栏 / 移动优先卡片流）后，用户选第 4 版，并要求整站统一。

**骨架规则**：
- **外壳** `560px` 居中（`--shell`），左右留白用 `--pad` 变量，`≤400px` 时降到 12px；正文基准 15px。这样手机上就是原生 app，桌面上是居中一列，不再为两套布局各写一遍样式。
- **导航改为底部 tab**：学习 / 复习 / 短文 / 阅读 / 我的，五个等宽、图标 + 文字、`复习` 带到期角标。原来顶部的「英语 AI」「词库管理」「管理后台」「书籍下拉」「深浅色」「账号」全部收进「我的」底部面板（`MoreSheet.vue`）——功能一个没少，但主屏只剩内容。
- **卡片体系**：圆角 20/14/10 三档（`--r-lg/md/sm`），卡片内边距 20px、间距 14px，阴影只做一层很淡的浮起；语境填空用 `--primary-soft` 底色直接和正文区分，不再靠虚线分隔。
- **进度可视化**：学习/复习的进度从细条改为环形（`conic-gradient` + `--pct`），与左侧轮次标签、右侧「熟知·跳过」同一行；单词 40px、汉语 26px、操作按钮最小高度 48px，`actions` 里的按钮等宽平分，拇指可及。
- **主题**：主色由靛蓝 `#4f5ef0` 换成珊瑚 `#ff6b4a`（深色模式 `#ff7a5c`），去掉 body 的径向渐变背景改为纯色 `--bg`，整体更平、更接近原生。

**代价与取舍**：删掉 `NavTabs.vue`，新增 `TabBar.vue` / `MoreSheet.vue`；`store` 增加 `moreOpen` 与 `openMore/closeMore`。桌面端不再有横向导航条，功能入口多了一次点击——换来的是手机上不再需要抽屉或横滑。

**验证**：用无头 Chrome（puppeteer-core）在 390×844 下逐页截图，确认 `scrollWidth === clientWidth`（无横向溢出）、五个 tab 全部可见、深浅两套主题可读；题库卡与语境填空区块单独截图核对。

## 26. 英语 AI 提到顶栏；管理端三版设计稿（2026-11-28）

**为什么把 AI 提到顶栏**：上一版把「英语 AI」收进「我的」面板，入口多了一次点击，用户反馈「一眼看不到、用不起来」。现在顶栏顺序为 `英语 AI`（珊瑚实心）→ `深色`（描边）→ 头像，AI 用主色实心、其余为描边，形成唯一的视觉焦点；`≤380px` 时只保留 ✦ 图标，避免挤压品牌区。

**顺带修掉的一处排版 bug**：`.notice` 原来是 `display:flex`，把「当前是 / **本机模式** / ，进度存在…」拆成三个 flex item，手机上读起来像断句错误。改为块级流式排版，`.score-chip` 单独成行。

**管理端**：现状是嵌在学员外壳里的一个 tab，与移动优先的骨架互相将就——桌面宽屏用不上、表格挤。`docs/admin-proposals.html` 给出三版独立形态（侧边栏控制台 / 顶栏+数据表 / 仪表盘优先），等用户选型后再落地；落地时按 `/admin` 独立路由 + 独立外壳实现，不复用底部 tab。

## 27. 管理端独立成页（`/admin` 侧边栏控制台）（2026-11-28）

**为什么独立**：管理端原本是学员外壳里的一个 tab，用的是 560px 移动优先的宽度——桌面上大量留白、表格挤成两行；而学员端为了容纳管理功能又背负了不该出现的入口。管理是低频、宽屏、多表格的事，两者混在一起只能互相将就。

**形态**（对比三版：侧边栏 / 顶栏+表格 / 仪表盘优先，用户选侧边栏）：
- 同一份 `dist` 产物，`GET /admin` 直接回 `index.html`，前端按 `location.pathname` 前缀决定挂哪套外壳。不额外维护第二个构建。
- 左侧 214px 固定深色栏（运营 / 内容 / 系统三组），主区宽屏铺开，KPI 四列、图表双列；`≤760px` 时侧栏压成顶部横向条，KPI 两列。
- 学员端底栏不再有管理项；本机模式下「我的」里的「管理控制台」直接跳 `/admin`。管理端不提供「回学员端」按钮（管理员没有学员身份，回学员端只会看到空数据）。

**只做能带来动作的指标**（新增 `GET /api/admin/overview`、`GET /api/admin/learners`）：
- 今日活跃 / 今日答题 / 复习积压 / 语境正确率——四个数字各自对应一个动作（找掉队、看量、催复习、补错词）。语境样本 < 10 题时显示「—」和「样本不足」，不显示误导性的 0%。
- 逐日序列在服务端补齐空白天，否则「没人学的那天」会被柱状图挤掉，趋势失真。
- 错词 Top8 取 `answer_log kind='cloze' AND correct=0`，这是「认得但写不出」的直接证据，比笼统的错误率更能指导下一批短文。
- 学员表按最近活跃分 `stable`（≤1 天）/ `wobble`（2–3 天）/ `lost`（>3 天）/ `never`，并支持复制掉队名单——考研学生断 3 天基本就流失了。

**刻意没做**：审核队列、批量导入、审计日志目前没有对应接口，就不在界面上画空按钮；留作下一步（`docs/admin-proposals.html` 里画了形态，落地时再补后端）。

**删除**：`AdminConsole.vue`（内嵌版）与其在 `App.vue` 的分支。

## 28. 管理员登录后直接进 `/admin`；学员端不再渲染管理员（2026-11-28）

**用户反馈**：「不要从登入管理员后就直接显示图片这样，要直接进入管理端面板，并且管理员端不要学生端，就是不要学习和复习这一类的，只要管理端面板。」同时要求先退回上一版管理端的形态。

**上一次改动的问题**：我把「管理端只做管理」理解成了重做界面（压成三页、6px 圆角、12px 字号），方向对但走偏了——用户真正要的是**入口与边界**，不是换皮。已用 `git revert 96db97c` 退回上一版（`bdd87a4` 的侧边栏控制台：概览 / 学员 / 词库 / 模型），那一套保留，不再重做视觉。

**这次改的是三件事**：

1. **管理员登录 = 直接进管理端。** `onAuthDone` 里发现 `me.is_admin` 就 `location.replace('/admin')`；在 `/` 上检测到管理员身份也立刻送走（先渲染一句「正在进入管理端…」占位，避免闪一下空白）。原来会掉进学员端首页，而且因为本机账号 `local` 与管理员 `local` 的 id 都是 1，页面上还显示别人的「今天的 1 个词已学完」，极具误导性。
2. **管理端不再挂学员端任何一个组件。** `App.vue` 按 `adminMode` 分流成两棵互斥的树：管理端那棵只有 `AdminAuth`（未登录）或 `AdminShell`（已登录），没有 `TopBar`、`TabBar`、`AuthPanel`、`ChatPanel`、`MoreSheet`，也不会启动 `loadBooks` / `loadTodayStats` / 学习计时。原来虽然视觉上是管理端，但学员端的模态框与状态全在后台挂着。
3. **管理端有自己的登录页。** 未登录时看到的是 `admin/AdminAuth`（深色页 + 居中卡片，只调 `/api/auth/admin-login`），不再是学员端那个带「注册 / 本机模式提示」的登录框。非管理员误入时只有一句提示和「退出并重新登录」，没有任何通往学员端的链接。

**顺手修的**：管理端词库页复用学员端 `WordBank` 时，搜索框被 `.plan-row input { width:130px }` 挤成 130px、占位文字截断；四张统计卡还带学员端的 emoji 图标。改为在 `.adm-narrow` 作用域内收敛（去图标、字号 22px、搜索框自适应、卡片内边距 18px），不改学员端那一份。

**教训**：用户说「X 模块不要像 Y 模块」时，先问清楚是**内容边界**（不该出现哪些入口/功能）还是**视觉语言**（排版密度与配色）。这次两个都改了，但用户只想要前者的一半。
