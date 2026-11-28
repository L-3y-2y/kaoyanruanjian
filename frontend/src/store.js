import { reactive } from 'vue'
import { api } from './api.js'

// 全局状态单例：集中管理登录态、健康信息、主题、弹窗开关等跨组件状态，
// 替代原先散落在 App.vue 里的一堆 ref + provide/inject 魔法字符串。
export const store = reactive({
  me: null,                 // 当前登录用户（含 is_admin / local 字段），null = 未登录
  health: null,             // /api/health 结果（词库数、模型、数据库名）
  pool: null,               // 未掌握词池 { available, total }
  batchesCount: 0,          // 今日到期复习词数（导航角标）
  todaySeconds: 0,          // 今日学习时长（秒）
  todayLearned: 0,          // 今日已背单词数
  books: [],                // 可选书籍（考研/四级/六级/高考）
  currentBook: null,        // 当前正在学的书
  modelInfo: { configured: false, current: '', models: [] },
  theme: 'light',
  activeTab: 'learn',       // 当前主视图：learn/review/articles/reading/wordbank
  adminMode: typeof window !== 'undefined' && window.location.pathname.startsWith('/admin'),
  adminTab: 'overview',     // 管理端当前面板：overview/learners/words/model
  learnersTotal: 0,         // 管理端学员总数（顶栏面包屑用）
  chatOpen: false,
  chatContext: null,        // null=通用答疑；{source_type, source_id, title}=带上下文
  showAuth: false,
  showModel: false,
  moreOpen: false,          // 底部「我的」面板
  error: '',
})

export function applyTheme(t) {
  store.theme = t
  document.documentElement.setAttribute('data-theme', t)
  localStorage.setItem('ky-theme', t)
}

export function toggleTheme() {
  applyTheme(store.theme === 'light' ? 'dark' : 'light')
}

export function initTheme() {
  applyTheme(localStorage.getItem('ky-theme') === 'dark' ? 'dark' : 'light')
}

export async function loadMe() {
  try {
    const r = await api('/api/me')
    store.me = r.user
  } catch {
    store.me = null
  }
}

export async function refreshHeader() {
  try {
    store.health = await api('/api/health')
    store.pool = await api('/api/pool')
    const r = await api('/api/review/stats')
    store.batchesCount = r.due
  } catch (e) {
    store.error = e.message
  }
}

export async function loadBooks() {
  try {
    const r = await api('/api/books')
    store.books = r.books || []
    store.currentBook = store.books.find(b => b.id === r.current_book_id) || null
  } catch {
    // 接口不可用时保持上一次的书，不打断学习
  }
}

export async function switchBook(bookId) {
  await api('/api/book/current', {
    method: 'POST',
    body: JSON.stringify({ book_id: bookId }),
  })
  await loadBooks()
  await refreshHeader()
  await loadTodayStats()
  store.activeTab = 'learn'
  store.error = ''
}

export async function loadTodayStats() {
  try {
    const t = await api('/api/study/today')
    store.todaySeconds = t.seconds
    store.todayLearned = t.learned_words
  } catch {
    // 统计接口不可用时保持原值，不打断学习流程
  }
}

export async function loadModelInfo() {
  try {
    store.modelInfo = await api('/api/llm/models')
  } catch {
    // 接口不可用时模型面板保持休眠，不报错
  }
}

export function goTab(next) {
  store.activeTab = next
  store.error = ''
  refreshHeader()
}

export function openChat(ctx = null) {
  store.chatContext = ctx
  store.chatOpen = true
}

export function closeChat() {
  store.chatOpen = false
  store.chatContext = null
}

export function openModel() {
  store.showModel = true
  store.moreOpen = false
}

export function openMore() {
  store.moreOpen = true
}

export function closeMore() {
  store.moreOpen = false
}

export function closeModel() {
  store.showModel = false
}

export function openSwitchAccount() {
  // 非破坏：只弹出登录框，不登出当前账号，可随时点「返回」取消。
  store.showAuth = true
}

export async function logout() {
  // 真正登出：清空服务端会话，回到本机模式/未登录（不再弹出登录框）。
  await api('/api/auth/logout', { method: 'POST' }).catch(() => {})
  closeChat()
  store.showAuth = false
  await loadMe()
  store.activeTab = 'learn'
  store.error = ''
}

export async function onAuthDone() {
  store.showAuth = false
  await loadMe()

  // 管理员账号没有学员身份，登录后直接进管理端，不要先掉进背单词页面。
  if (store.me?.is_admin) {
    window.location.replace('/admin')
    return
  }

  await loadBooks()
  await refreshHeader()
  await loadTodayStats()
  await loadModelInfo()
  store.activeTab = 'learn'
}

export async function onModelChanged() {
  await refreshHeader()
  await loadModelInfo()
}
