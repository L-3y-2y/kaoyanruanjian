<script setup>
import { onMounted, provide, ref } from 'vue'
import { api } from './api.js'
import AuthPanel from './components/AuthPanel.vue'
import LearnView from './components/LearnView.vue'
import ReviewView from './components/ReviewView.vue'
import ArticlesView from './components/ArticlesView.vue'
import ReadingView from './components/ReadingView.vue'
import WordBank from './components/WordBank.vue'
import AdminConsole from './components/AdminConsole.vue'
import ModelPanel from './components/ModelPanel.vue'
import ChatPanel from './components/ChatPanel.vue'

const tab = ref('learn')
const showModel = ref(false)
const modelInfo = ref({ configured: false, current: '', models: [] })
const chatContext = ref(null)   // null = 通用答疑；{source_type, source_id, title} = 带上下文
const chatOpen = ref(false)
const health = ref(null)
const pool = ref(null)
const me = ref(null)
const localMode = ref(true)
const showAuth = ref(false)
const batchesCount = ref(0)
const error = ref('')
const theme = ref('light')

function applyTheme(t) {
  theme.value = t
  document.documentElement.setAttribute('data-theme', t)
  localStorage.setItem('ky-theme', t)
}
function toggleTheme() {
  applyTheme(theme.value === 'light' ? 'dark' : 'light')
}

async function refreshHeader() {
  try {
    health.value = await api('/api/health')
    pool.value = await api('/api/pool')
    const r = await api('/api/review-batches')
    batchesCount.value = r.batches.length
  } catch (e) { error.value = e.message }
}

async function loadMe() {
  try {
    const r = await api('/api/me')
    me.value = r.user
    localMode.value = r.local_mode
  } catch { me.value = null }
}

async function loadModelInfo() {
  try {
    modelInfo.value = await api('/api/llm/models')
  } catch { /* model panel stays dormant if the endpoint is unavailable */ }
}

function goTab(next) {
  tab.value = next
  error.value = ''
  refreshHeader()
}

async function onAuthDone() {
  showAuth.value = false
  await loadMe()
  await refreshHeader()
  await loadModelInfo()
  tab.value = 'learn'
}

async function onModelChanged(model) {
  await refreshHeader()
  await loadModelInfo()
}

async function logout() {
  await api('/api/auth/logout', { method: 'POST' }).catch(() => {})
  chatOpen.value = false
  showAuth.value = true
  await loadMe()
}

function openChat(ctx = null) {
  chatContext.value = ctx
  chatOpen.value = true
}

provide('onChanged', refreshHeader)
provide('goTab', goTab)
provide('openChat', openChat)

onMounted(async () => {
  applyTheme(localStorage.getItem('ky-theme') === 'dark' ? 'dark' : 'light')
  await loadMe()
  await refreshHeader()
  await loadModelInfo()
})
</script>

<template>
  <div class="app">
    <AuthPanel v-if="showAuth || !me" @done="onAuthDone" @cancel="showAuth = false" />

    <ModelPanel v-if="showModel" :info="modelInfo" @close="showModel = false" @changed="onModelChanged" />
    <ChatPanel v-if="chatOpen" :context="chatContext" @close="chatOpen = false" />

    <template v-if="me && !showAuth">
      <header class="topbar">
        <div class="brand">
          <div class="logo">研</div>
          <div>
            <h1>考研背单词</h1>
            <p class="sub">五轮递进 · 日期批次复习 · 短文与周阅读</p>
          </div>
        </div>
        <div class="header-right">
          <div class="db-chip" v-if="health">
            <span class="dot"></span>
            已连接 {{ health.database }}
            <small>词库 {{ health.word_count }} 词 · {{ health.server_date }}</small>
            <button v-if="me.is_admin || me.local" class="model-chip" @click="showModel = true">
              {{ health.llm_configured ? '已接 · ' + (health.llm_model || '大模型') : '本地生成' }}
            </button>
            <span v-else class="model-chip static">
              {{ health.llm_configured ? '已接 · ' + (health.llm_model || '大模型') : '本地生成' }}
            </span>
          </div>
          <div class="account" v-if="me">
            <span class="who" :class="{ admin: me.is_admin }">
              {{ me.is_admin ? '管理员' : (me.local ? '本机模式' : '账号') }}：{{ me.username }}
            </span>
            <button class="ghost tiny" @click="logout">切换账号</button>
            <button class="theme-toggle" @click="toggleTheme">
              {{ theme === 'light' ? '🌙 深色' : '☀️ 浅色' }}
            </button>
          </div>
        </div>
      </header>

      <div class="notice" v-if="me.local">
        当前是<strong>本机模式</strong>：进度存在本机账号里。点「切换账号」注册，已有进度会自动归到你名下；注册后各账号数据互相隔离。「词库管理」在这里直接可用，无需管理员账号。
      </div>
      <p class="error" v-if="error">{{ error }}</p>

      <nav class="tabs">
        <button :class="{ active: tab === 'learn' }" @click="goTab('learn')">今日学习</button>
        <button :class="{ active: tab === 'review' }" @click="goTab('review')">
          复习板块 <span class="badge" v-if="batchesCount">{{ batchesCount }}</span>
        </button>
        <button :class="{ active: tab === 'articles' }" @click="goTab('articles')">每日短文</button>
        <button :class="{ active: tab === 'reading' }" @click="goTab('reading')">周阅读</button>
        <button v-if="me.local" :class="{ active: tab === 'wordbank' }" @click="goTab('wordbank')">词库管理</button>
        <button v-if="me.is_admin" class="admin-tab" :class="{ active: tab === 'admin' }" @click="goTab('admin')">
          管理后台<span class="admin-dot"></span>
        </button>
        <button v-if="health?.llm_configured" @click="openChat(null)">英语 AI</button>
        <span class="pool" v-if="pool">未掌握池 {{ pool.available }} / {{ pool.total }}</span>
      </nav>

      <LearnView v-if="tab === 'learn'" />
      <ReviewView v-else-if="tab === 'review'" />
      <ArticlesView v-else-if="tab === 'articles'" />
      <ReadingView v-else-if="tab === 'reading'" />
      <WordBank v-else-if="tab === 'wordbank'" />
      <AdminConsole v-else-if="tab === 'admin'" :me="me" :health="health" />

      <footer>
        进度、短文快照与答卷只存在服务端 MySQL 的 {{ health?.database || 'kaoyanruanjian' }} 库里；
        刷新、换设备都会接着上次继续。短文与周阅读不计入五轮，也不改动两级状态。
      </footer>
    </template>
  </div>
</template>
