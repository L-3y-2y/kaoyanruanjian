import { reactive } from 'vue'
import { api } from './api.js'

// 全局状态单例：集中管理登录态、健康信息、主题、弹窗开关等跨组件状态，
// 替代原先散落在 App.vue 里的一堆 ref + provide/inject 魔法字符串。
export const store = reactive({
  me: null,                 // 当前登录用户（含 is_admin / local 字段），null = 未登录
  health: null,             // /api/health 结果（词库数、模型、数据库名）
  pool: null,               // 未掌握词池 { available, total }
  batchesCount: 0,          // 复习批次数量（导航角标）
  modelInfo: { configured: false, current: '', models: [] },
  theme: 'light',
  activeTab: 'learn',       // 当前主视图：learn/review/articles/reading/wordbank/admin
  chatOpen: false,
  chatContext: null,        // null=通用答疑；{source_type, source_id, title}=带上下文
  showAuth: false,
  showModel: false,
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
    const r = await api('/api/review-batches')
    store.batchesCount = r.batches.length
  } catch (e) {
    store.error = e.message
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
  await refreshHeader()
  await loadModelInfo()
  store.activeTab = 'learn'
}

export async function onModelChanged() {
  await refreshHeader()
  await loadModelInfo()
}
