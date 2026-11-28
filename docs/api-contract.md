# 接口契约 v2（阶段 1～5 完整版）

> 对应需求文档第 5 节「阶段 2 开工前先定一页接口契约」。
> 所有接口前缀 `/api`；开发期由 Vite 转发到 FastAPI（`http://127.0.0.1:8000`），交付期前端构建产物交 FastAPI 同源托管，前端**永远只写相对路径** `/api/...`。
> 统一约定：成功返回 JSON；业务失败返回 `{"detail": "中文说明"}`，HTTP 用 400 / 404 / 409 / 500。
> **身份**：除 `/api/health` 与 `/api/auth/*` 外，所有接口都从 HttpOnly 会话 cookie 解析 `user_id`，绝不采信前端传来的用户 id。未登录且 `LOCAL_MODE=1` 时，回环地址请求落到占位账号 `local`（id=1）。越权访问别人的批次/文章/题目一律 404。
> 前端 `fetch` 统一带 `credentials:'include'`，开发期 Vite 转发、交付期同源，代码里只写 `/api/...`。

## 通用原则

1. **单一数据源**：前端不保存当前词、轮次、下标。每次渲染前调 `GET /api/state`，服务端查「未完成的、最该做的那一条」返回。
2. **取词只靠查询，不靠游标**：服务端按 `status=pending` + `cycle_no` + `round` + `id` 排序取第一条。
3. **答完立刻落盘**：`POST /api/answer` 在**同一个事务**里完成「改 `progress` + 写 `answer_log`」。
4. **防重复提交**：更新带 `WHERE status='pending'` 条件；影响行数为 0 视为重复提交，直接返回最新 state，不报错、不跳两个词。
5. **判分在服务端**，前端只负责显示与发音（`speechSynthesis`）。

---

## GET /api/health

服务与数据库连通性自检。

```json
{
  "ok": true,
  "database": "kaoyanruanjian",
  "account": "kaoyan_app@127.0.0.1",
  "tables": 5,
  "word_count": 60,
  "server_date": "2026-11-06"
}
```

`server_date` 用来表明日期批次以**服务端 DATE** 为准，不用浏览器本地时间。

## 多书籍接口

所有取词 / 进度 / 生成接口都按 `user.current_book_id` 过滤，浏览器不传书籍 id。

### GET /api/books
列出四本书及该用户的词数 / 已学词数：

```json
{ "books": [{ "id": 1, "code": "ky", "name": "考研英语", "exam_type": "考研英语",
              "tag_filter": "ky", "word_count": 4803, "learned": 45, "current": true }],
  "current_book_id": 1 }
```

### GET /api/book/current · POST /api/book/current
查看 / 切换当前书。POST 请求体 `{ "book_id": 2 }`；切换立即影响后续所有请求，各页面会重建并重新取数。管理员账号（不在 `user` 表）不支持切换。

## GET /api/pool

返回**当前书**的「未掌握池」剩余可学词数与该书总词数。

```json
{ "available": 42, "total": 60, "book_id": 1, "book_name": "考研英语" }
```

## GET /api/state?mode=learn

取当天学习计划里下一个该做的题。

| 返回 `kind` | 含义 | 主要字段 |
| --- | --- | --- |
| `need_plan` | 今天还没选 N | `pool`（可选词数） |
| `r1` | R1 认词 | `progress_id, word, phonetic, translation, pos, cycle_no, round, cycle_size, group_index, done, total` |
| `r2` | R2 听写 | 同上，`speak_text`（供前端发音） |
| `r3` | R3 默写 | 同上，`translation` 为题干、`speak_text` 供发音 |
| `learning_done` | 今日 N 个词 R1～R5 全走完 | `plan_id, word_count, stage:"memorized"`；此时已进复习池、已解锁短文 |

> R1 接口会返回 `translation`，但**前端只在用户点「陌生」后才展开显示**；服务端不控制这个视觉状态（它属于界面呈现，不是学习进度）。
> R2 / R3 / R5 的 `speak_text` 就是单词拼写本身，浏览器发音需要它；答案判定仍由服务端做。

## GET /api/state?mode=review

取「今天到期」的下一个待复习词（词级，跨所有日期批次）。

| 返回 `kind` | 含义 | 主要字段 |
| --- | --- | --- |
| `review` | 下一个到期词，含当前复习子步 | `progress_id, word, translation, pos, speak_text, review_round(0/1/2), review_stage` |
| `review_done` | 今天到期的词都复习完了 | `due, mastered` |

- `review_round`：0 = 认不认识；1 = 听音默写；2 = 汉译英默写（答对即本轮通过，间隔 +1）
- 到期判定：`stage='memorized' AND next_due_at <= CURDATE()`

## POST /api/plan

新建当天的「大周期」。一天只能有一条。

```json
{ "count": 18 }
```

- `count` 必须是 1～200 的整数（前端学习页滑轴 1～100），且 `count <= /api/pool 的 available`
- 从未掌握池按 `word.id` 升序取 N 个新词；每 5 个一组切小周期，`cycle_no` 从 1 开始，最后一组允许不足 5 个
- 事务内写 `daily_plan` 一行 + 每个词 5 条 `progress`（round 1～5 全部 `pending`）
- 成功：`{ "plan_id": 3, "study_date": "2026-11-06", "word_count": 18, "cycle_count": 4 }`
- 当天已有计划 → 409 `{"detail":"今天已经建过学习计划了"}`；词池不足 → 400

## POST /api/answer

提交一次作答。`progress_id` 由 `GET /api/state` 返回，前端不自己算。

```json
{
  "progress_id": 123,
  "action": "known | unknown | spell | r4_reveal | self_assess | r_known | r_unknown | r_spell",
  "value": "用户输入的文本（spell / r4_reveal / self_assess 时）",
  "assessment": "remembered | forgotten（仅 self_assess）"
}
```

| action | 适用轮次 | 服务端行为 |
| --- | --- | --- |
| `known` | R1 | `status=done`，写 `answer_log` |
| `unknown` | R1 | `wrong_count+1` + 写 log，**status 保持 pending**，返回 `reveal:true`（前端展开音标+释义，再点「认识」发 `known`） |
| `spell` | R2 / R3 / R5 | 归一化后比对 `word.word`；对 → `done`；错 → `wrong_count+1` + 写 log，返回 `correct:false, answer:"正确单词"` |
| `r4_reveal` | R4 | 只记录用户写的释义 + 返回 `standard_answer`（词库主释义），**不改进度** |
| `self_assess` | R4 | 结合 `assessment` 写 log 并把 `status=done`；「没记住」只做标记与统计，**不改变本批次流程** |
| `r_known` | 复习·认识 | 认识 → 复习子步 +1，进入下一步 |
| `r_unknown` | 复习·不认识 | 间隔归零、`next_due_at` = 明天；返回 `reveal:true` 显示释义 |
| `r_spell` | 复习·听写 / 默写 | 归一化比对；对 → 子步 +1（默写步通过则间隔 +1）；错 → 间隔归零、明天再来，返回 `answer` |

统一返回：

```json
{
  "correct": true,
  "answer": null,
  "reveal": false,
  "standard_answer": null,
  "duplicate": false,
  "state": { "...下一次 GET /api/state 的结果，或 null..." }
}
```

`state` 直接内联返回下一题，前端省一次请求；刷新页面时仍以 `GET /api/state` 为准。

`state` 为 `null` 表示**题目不变**，前端保留当前卡片，只切换呈现：

| 情况 | `state` | 前端动作 |
| --- | --- | --- |
| R1 点「陌生」 | `null` + `reveal:true` | 展开音标 + 释义，再点「认识」重新提交 |
| R2 / R3 / R5 拼写答错 | `null` + `answer:"正确单词"` | 下方显示答案，重新输入时清空 |
| R4 写完汉语释义 | `null` + `standard_answer` | 切到「记住了 / 没记住」自评 |
| 答对 / 自评完成 | 下一题对象 | 换到下一张卡片 |

`duplicate:true` 表示同一个 `progress_id` 被重复提交，服务端**不跳两个词**，只把当前应有的题目同步给前端。

> 「答错 → 下方出现正确单词 → 重新输入时答案消失」由前端 `input` 事件清空 `answer` 实现；服务端不保存这个瞬时状态。

## GET /api/review/stats

复习概览统计：

```json
{ "due": 15, "pool": 15, "mastered": 10 }
```

- `due`：今天到期待复习的词数
- `pool`：复习池中的词数（`stage='memorized'`）
- `mastered`：已背熟的词数（`stage='mastered'`）

## 模型选择与自检（可选，需配置 Key）

| 接口 | 请求 | 说明 |
| --- | --- | --- |
| `GET /api/llm/models` | — | 返回 `{configured, current, models[]}`。`models[]` 每项 `{id, label, note, current}`，来自服务端 `LLM_MODEL_PRESETS` 白名单；**绝不下发 base_url 与 Key** |
| `POST /api/llm/model` | `{model}` | 在白名单内切换当前模型。运行时改 `os.environ` 立即生效（下次生成即用新模型），并写回 `.env` 的 `LLM_MODEL=` 行持久化；清主观题判分缓存。非法名/非白名单/未配 Key 均返回 400 |
| `POST /api/llm/test` | — | 向当前模型发一句极简请求，返回 `{ok, model, latency_ms, reply}` 或 `{ok:false, model, error}`（含 HTTP 429 的真实提示）。不抛异常，供前端直接展示结论 |

未配 Key 时仍能调前两个接口（只是 `configured=false`，界面禁用切换）；`POST /api/llm/test` 会返回明确错误说明。`GET /api/health` 另附 `llm_model` 字段显示当前模型名。

## POST /api/word/known

「熟知」按钮：把某个词的全部 progress 直接标记为背熟（`status=done` + `stage=mastered`），跳过之后一切学习和复习。

```json
{ "progress_id": 123 }
```

只影响该词自己的 progress 行（同一词在不同日期批次下可能有多行，全部一并标记）。

---

## 错误对照

| HTTP | detail | 原因 |
| --- | --- | --- |
| 400 | `count 必须是 1~200 的整数` | 入参非法 |
| 400 | `未掌握池只剩 X 个词，请先导入词库` | 词库不够 |
| 404 | `找不到该题目 / 批次` | progress_id / plan_id 无效或越权 |
| 409 | `今天已经建过学习计划了` | 违反「一天一条」 |
| 500 | 数据库错误信息 | 连接 / SQL 异常，看后端终端日志 |

---

## 阶段 4：账号（v2 已实现）

| 接口 | 入参 | 返回 / 行为 |
| --- | --- | --- |
| `GET /api/me` | — | `{user:{id,username,is_admin,local}, local_mode}`。本地模式返回 `local` 占位账号 |
| `POST /api/auth/register` | `{username,password}` | 建号、写 pbkdf2 哈希、发会话 cookie。返回 `inherited_local_progress`：首个真人注册时把 id=1 的历史进度整体改挂到新账号（事务内，一次性） |
| `POST /api/auth/login` | `{username,password}` | 校验哈希，发会话 cookie（`HttpOnly / SameSite=Lax / 30 天`） |
| `POST /api/auth/admin-login` | `{username,password}` | 校验 `admin` 表，发 `is_admin=1` 会话 |
| `POST /api/auth/logout` | — | 删会话行 + 清 cookie |

密码 <8 位 / 两次不一致 / 用户名重复 → 400 / 409；密码错误统一 401 `用户名或密码不正确`（不区分账号是否存在）。

## 阶段 5：每日短文（v2 已实现）

| 接口 | 入参 | 返回 / 行为 |
| --- | --- | --- |
| `GET /api/articles/batches` | — | 只列出学完 5 轮（`stage IN ('memorized','mastered')`）的日期批次 + 已生成篇数 |
| `GET /api/articles/scenes` | — | 可选场景列表（默认 / 校园 / 实验室 / 职场 / 新闻 / 旅行） |
| `GET /api/articles/daily?plan_id=&scene=` | 批次 id、`scene`（可选） | 首次调用按场景生成并持久化；重复调用返回**同一组文章**（场景只在首次生效）。返回 `summary`、`mode(full/sample)`、`scene`、`articles[]` |
| `POST /api/articles/reveal` | `{article_id}` | 按 `article_word` 快照构建并返回 `segments[]`；**不落盘、不重新生成、不改正文**。揭示是每次视图手动触发，刷新后 `GET daily` 仍返回纯文本 |
| `POST /api/articles/regenerate` | `{article_id}` | 用当前大模型按原场景重写这篇 daily 短文，保留 id、重建词义快照；未配 Key 返回 400 |

`articles[]` 每项：`{id, seq, title, body, mode, scene, covered_words, total_words, uncovered[], revealed, generator, marked_count, segments}`。前端用 `segments` 里的快照生成语境完形小测，不写库、不改进度。
**`revealed` 恒为 `false`、`segments` 恒为 `null`** —— `GET daily` 永远只下发纯文本，界面每次进入都没有可点高亮；用户点「显示今日词」调 `reveal` 才拿到可点层，刷新后回到纯文本。`segments` 是 `[{text, word|null}]`，`word` 含 `surface / translation / phonetic / pos` 的生成时快照。

`summary` 固定一行告知：全放 `本批次 18 词已全部收入 2 篇短文。`；抽词 `已从 45 词中抽取 20 词编入 3 篇短文，其余 25 词见下方未收录清单…`。

## 阶段 5：每周阅读（v2 已实现）

| 接口 | 入参 | 返回 / 行为 |
| --- | --- | --- |
| `GET /api/reading/weeks` | — | 可回看周次列表（当前周排第一） |
| `GET /api/reading?week_key=2026-W45` | 周次 | `not_published`（带 `publish_at / deadline`）或 `published`（2 篇 + 题目 + `summary`） |
| `POST /api/reading/publish-now` | — | 提前生成**本人**本周 2 篇（试用按钮），幂等 |
| `POST /api/reading/answer` | `{question_id, week_key, value}` | 客观题比对 `correct_option`；主观题走 `generate_json` / 本地规则判分（相同作答有缓存，不重复烧额度），理由写 `answer_log.ai_result`。逾期 `scored=0`。重复提交返回 `duplicate:true` 不重复计分 |

题目对象：`{id, qtype, stem, options{A..D}|null, ai_graded, answered, user_answer, correct, score, ai_result, scored, correct_option?, standard_answer?}`。**`correct_option` / `standard_answer` 只在已作答后返回**，未答不下发答案。

## 英语 AI 助手（旁路，需配 LLM Key）

| 接口 | 入参 | 返回 / 行为 |
| --- | --- | --- |
| `POST /api/chat/start` | `{source_type: general\|article\|question, source_id?, first_question?}` | 建会话（带来源时冻结该文章/题目的真实快照作语境），可选首问；返回 `{session_id, title, reply?}` |
| `POST /api/chat/ask` | `{session_id, question}` | 多轮文字答疑，返回 `{content, provider, turn_count}`；上下文取最近 12 条历史 |
| `POST /api/chat/practice` | `{kind: cloze\|reading, session_id?, source_type?, source_id?}` | 生成**结构化交互题卡**：`{session_id, title, card, turn_count}`。card 含 `kind/title/passage`，cloze 另有 `options{A..J}` + `blanks[{no,answer,explanation}]`，reading 另有 `questions[{no,stem,options{A..D},answer,explanation}]`。题卡以 `[[PRACTICE_CARD]]` 标记存为 assistant 消息，判分在前端本地完成 |
| `GET /api/chat/history?session_id=` | 会话 id | 消息列表（题卡消息 content 以 `[[PRACTICE_CARD]]` 开头 + JSON） |
| `GET /api/chat/sessions` | — | 本 user 的会话列表（按最近更新） |
| `POST /api/chat/delete` | `{session_id}` | 删本人会话及其消息 |

所有 chat 接口**只写 `chat_session`/`chat_message`，不碰 progress / answer_log / 五轮状态**。8 轮上限、最多 8 次提问；未配 Key 返回 400。AI 角色、出题难度、今日词库、要规避的旧文章都按当前书注入；已开会话沿用建会话时的书。

## 语境填空（短文内练习，不改进度）

| 接口 | 入参 | 返回 / 行为 |
| --- | --- | --- |
| `GET /api/articles/cloze?article_id=` | 文章 id | `{items:[{id, sentence, status, attempts}], done, total, complete}`；`sentence` 已挖空且**不含任何提示**（无中文翻译、无词性、不显示来源）；答案只存服务端 |
| `POST /api/articles/cloze/check` | `{item_id, value}` | `{correct, answer, done, total, weak}`；接受原形或句中实际词形（absorbed / studies 等）；答错写 `answer_log` 并把该词 `progress.cloze_wrong=1`（次日复习队列优先），答对不写 progress |

五轮走完即 `stage='memorized'` 进复习池、解锁短文。语境填空挂在每篇短文下方，首次揭示时按 `(article, word, source)` 幂等生成：**无论 `article` 还是 `extra`，句子都是按当前书考试级别现写的新句**——不取原文句，并与 `word_example` 里该词的历史句子、本轮其他句子做相似度去重。生成走并行（最多 5 路），读写分成两端短事务，LLM 调用期间不持有数据库事务。全部练习只写 `answer_log`，**不写 progress 的五轮状态**；唯一例外是答错时置 `cloze_wrong` 标记，让次日复习优先安排该词（复习三轮通过后清零）。旧的 `/api/learn/cloze*` 与 `/api/scenario/example` 接口已移除。

## 阶段 4：管理员词库（v2 已实现）

| 接口 | 权限 | 说明 |
| --- | --- | --- |
| `GET /api/admin/stats` | 管理员或本机模式 | 账号数 / 批次数 / 文章数 / 词库来源分布 |
| `GET /api/admin/words?q=&limit=` | 同上 | 按拼写或中文释义检索 |
| `POST /api/admin/word` | 同上 | `{word,phonetic,translation,pos,tag}`；`ON DUPLICATE KEY UPDATE` 修正词条并标 `verified=1` |
| `GET /api/admin/overview?days=14` | 同上 | 管理端首页：今日活跃/答题/复习积压/语境正确率、逐日答题序列（补齐空白天）、语境错词 Top8、词源分布、各书在用人数 |
| `GET /api/admin/learners` | 同上 | 跨账号学员表：当前书籍、今日答题、已掌握、复习积压、语境错题、最后活跃时间、`state`（stable/wobble/lost/never）+ 分组汇总 |
| `GET /admin` | — | 返回同一份 SPA 产物；前端按路径前缀挂载管理端（独立登录页 + 宽屏侧边栏），**不加载学员端任何组件**，也不占用底部 tab。管理员身份在 `/` 上会被前端重定向到这里 |

## 错误对照（v2 补充）

| HTTP | detail | 原因 |
| --- | --- | --- |
| 401 | `请先登录` | `LOCAL_MODE=0` 且无有效会话 |
| 403 | `需要管理员权限` | 非管理员访问 `/api/admin/*` |
| 400 | `短文只在该批次学完 5 轮后才出现` | 批次还没学完 |
| 400 | `题目与周次不匹配` / `周次格式应为 2026-W45` | 阅读答卷参数越界 |
| 404 | `找不到该日期批次 / 这篇短文 / 这道题` | 不存在或属其他用户（越权与不存在同样返回 404） |
