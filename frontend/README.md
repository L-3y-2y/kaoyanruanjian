# frontend · Vue 3 + Vite（完整版）

按需求 9.5：Vue 3 + Vite，**不上路由 / Pinia / UI 组件库**。阶段 1 时是单组件；阶段 4/5 功能变多后拆成 1 个外壳 + 6 个子组件，页签只是外壳里的一个 ref，共享刷新函数用 `provide/inject` 传下去。

| 文件 | 板块 |
| --- | --- |
| `src/App.vue` | 外壳：账号状态、顶部连接信息、五个页签、退出/切换账号 |
| `src/api.js` | `fetch` 封装（`credentials:'include'`）、`playWord()`、轮次/状态文案 |
| `components/AuthPanel.vue` | 登录 / 注册 / 管理员登录 |
| `components/LearnView.vue` | 今日学习（R1～R3） |
| `components/ReviewView.vue` | 日期批次列表 + R4 / R5 |
| `components/ArticlesView.vue` | 每日短文：纯文本 → 揭示 → 点击释义快照 |
| `components/ReadingView.vue` | 周阅读：2 篇、四选一 + 简答题、成绩 |
| `components/AdminView.vue` | 词库检索 / 新增 / 修正（管理员或本机模式） |

## 两种运行方式

| 时期 | 端口 | 怎么做 |
| --- | --- | --- |
| 开发期 | 前端 `:5173` + 后端 `:8000` | 后端先起，再在 `frontend/` 执行 `npm run dev`；Vite 把 `/api` 转发给 FastAPI |
| 交付期 | **只有 `:8000`** | `npm run build` 出 `dist/`，由 FastAPI 同源托管（需求 A4） |

根目录 `setup.bat` 已把 `npm install` 和 `npm run build` 做完；改完前端要重新 `npm run build` 才会更新 `dist/`。

## 状态纪律

组件里只保留**纯 UI 状态**：输入框内容、答错时正显示的正确单词、当前页签 / R4 自评阶段、点开的释义卡片。

「当前词、当前轮次、批次进度、短文是否可点、答卷与成绩」全部来自服务端：前端不存下标 `i`、不缓存进度、不缓存登录身份。刷新会重新向服务端取题，接着上次继续。

## 关键交互约定

| 轮次 | 显示 | 交互 |
| --- | --- | --- |
| R1 认词 | 只有英文 | 认识 / 陌生（陌生展开音标+释义，再点认识才过） |
| R2 听写 | 读音按钮 | 拼写英文，错了显示正确单词，**重新输入时答案消失** |
| R3 默写 | 汉语释义 + 读音 | 默写英文 |
| R4 英→中 | 英文 | 写汉义 → 看标准答案 → 自评记住了 / 没记住 |
| R5 中→英 | 汉语释义 | 默写英文，错了显示正确答案 |

- 每日短文首次渲染 `{{ body }}` 纯文本，`segments` 为 `null`，**没有任何可点元素**；点底部唯一按钮后才由服务端返回可点片段。刷新后服务端记住 `revealed`，不会退回不可点，也不会换一篇文章。
- 周阅读未到周五 18:00 时显示发布时间 + 「提前生成本周（试用）」；客观题未作答前服务端不下发标准答案。
- 发音走浏览器 `speechSynthesis`（`playWord(text)`），零依赖、不用 Key。
