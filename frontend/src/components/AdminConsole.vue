<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'
import WordBank from './WordBank.vue'

const props = defineProps({
  me: { type: Object, required: true },
  health: { type: Object, default: null }
})

const tab = ref('overview')

// overview
const stats = ref(null)
const sources = ref([])
const users = ref([])
const loading = ref(false)
const error = ref('')

// model
const modelInfo = ref({ configured: false, current: '', models: [] })
const busyModel = ref(false)
const testingModel = ref(false)
const testResult = ref(null)
const modelError = ref('')

const NAV = [
  { key: 'overview', label: '数据概览', ico: '📊' },
  { key: 'words',    label: '词库管理', ico: '📚' },
  { key: 'users',    label: '用户管理', ico: '👥' },
  { key: 'model',    label: '模型配置', ico: '🧠' }
]

async function loadOverview() {
  loading.value = true
  error.value = ''
  try {
    stats.value = await api('/api/admin/stats')
    const r = await api('/api/admin/users')
    users.value = r.users
    sources.value = stats.value.sources || []
  } catch (e) { error.value = e.message }
  finally { loading.value = false }
}

async function loadModelInfo() {
  try { modelInfo.value = await api('/api/llm/models') } catch { /* dormant */ }
}

async function chooseModel(model) {
  if (busyModel.value || model.id === modelInfo.value.current) return
  busyModel.value = true
  modelError.value = ''
  try {
    const r = await api('/api/llm/model', { method: 'POST', body: JSON.stringify({ model: model.id }) })
    modelInfo.value.current = r.model
  } catch (e) { modelError.value = e.message }
  finally { busyModel.value = false }
}

async function testModel() {
  if (testingModel.value) return
  testingModel.value = true
  testResult.value = null
  modelError.value = ''
  try {
    testResult.value = await api('/api/llm/test', { method: 'POST' })
  } catch (e) { modelError.value = e.message }
  finally { testingModel.value = false }
}

function switchTab(k) {
  tab.value = k
  error.value = ''
  if (k === 'overview' && !stats.value) loadOverview()
}

onMounted(() => {
  loadOverview()
  loadModelInfo()
})
</script>

<template>
  <div class="admin-console">
    <div class="admin-banner">
      <div class="logo">研</div>
      <div>
        <strong>管理员控制台</strong>
        <span>管理词库、查看全体学员进度、配置生成模型 —— 学员端看不到这些能力</span>
      </div>
      <div class="admin-badge"><span class="dot"></span>管理员 · {{ me.username }}</div>
    </div>

    <nav class="sub-tabs">
      <button v-for="n in NAV" :key="n.key"
              :class="{ active: tab === n.key }" @click="switchTab(n.key)">
        {{ n.ico }} {{ n.label }}
      </button>
    </nav>

    <p class="error" v-if="error">{{ error }}</p>

    <!-- 概览 -->
    <template v-if="tab === 'overview'">
      <div v-if="loading" class="card muted">正在统计…</div>
      <template v-else-if="stats">
        <div class="stat-grid">
          <div class="stat-card">
            <div class="stat-ico">📚</div>
            <div class="stat-num">{{ health?.word_count ?? '—' }}</div>
            <div class="stat-label">词库总量</div>
          </div>
          <div class="stat-card green">
            <div class="stat-ico">👤</div>
            <div class="stat-num">{{ stats.users }}</div>
            <div class="stat-label">注册学员</div>
          </div>
          <div class="stat-card amber">
            <div class="stat-ico">📅</div>
            <div class="stat-num">{{ stats.plans }}</div>
            <div class="stat-label">学习批次</div>
          </div>
          <div class="stat-card violet">
            <div class="stat-ico">📄</div>
            <div class="stat-num">{{ stats.articles }}</div>
            <div class="stat-label">短文 / 阅读篇数</div>
          </div>
        </div>

        <section class="card">
          <h2>词源分布</h2>
          <p class="muted small">词库中各来源的词条数量。</p>
          <div v-if="!sources.length" class="empty">暂无词条。</div>
          <div v-for="s in sources" :key="s.source" class="source-row">
            <span class="src-name">{{ s.source }}</span>
            <div class="bar"><div :style="{ width: Math.max(4, Math.round(s.c / Math.max(1, sources[0].c) * 100)) + '%' }"></div></div>
            <span class="muted small">{{ s.c }}</span>
          </div>
        </section>

        <section class="card">
          <h2>生成模型</h2>
          <p class="muted small">
            当前所有学员的短文、阅读与 AI 答疑统一使用
            <strong>{{ modelInfo.current || '（未配置）' }}</strong>
            <template v-if="!modelInfo.configured"> · <span class="muted">尚未配置 Key，生成会走本地模板</span></template>
          </p>
          <div class="actions" style="justify-content: flex-start">
            <button class="primary" @click="switchTab('model')">去配置模型</button>
          </div>
        </section>
      </template>
    </template>

    <!-- 词库 -->
    <WordBank v-else-if="tab === 'words'" />

    <!-- 用户管理 -->
    <section v-else-if="tab === 'users'" class="card">
      <h2>全体学员进度</h2>
      <p class="muted small">只有管理员能看到的跨账号总览：每个注册学员的学习批次、已掌握词数与答题情况。</p>
      <div v-if="!users.length" class="empty">还没有注册学员。</div>
      <table v-else class="user-table">
        <thead>
          <tr>
            <th>用户名</th>
            <th>注册时间</th>
            <th>学习批次</th>
            <th>已掌握词</th>
            <th>答题次数</th>
            <th>AI 会话</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id">
            <td class="uname">{{ u.username }}</td>
            <td class="udate">{{ u.created_at.slice(0, 10) }}</td>
            <td class="num">{{ u.plans }}</td>
            <td class="num">{{ u.mastered_words }}</td>
            <td class="num">{{ u.done_items }}</td>
            <td class="num">{{ u.chat_sessions }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 模型配置 -->
    <section v-else-if="tab === 'model'" class="card">
      <h2>生成模型配置</h2>
      <p class="muted small">
        只允许在服务端预置的模型里切换，接口地址和密钥始终留在服务端。切换立即生效、无需重启，影响所有学员的生成内容。
      </p>

      <p v-if="!modelInfo.configured" class="notice warn">
        还没配置 Key。请先在 <code>backend/.env</code> 填 <code>LLM_API_KEY</code> 后重启，否则生成会走本地模板。
      </p>

      <div class="model-list">
        <button v-for="m in modelInfo.models" :key="m.id" class="model-item"
                :class="{ active: m.id === modelInfo.current }"
                :disabled="busyModel || !modelInfo.configured" @click="chooseModel(m)">
          <span class="model-name">{{ m.id }}</span>
          <span class="model-note">{{ m.note }}</span>
          <span v-if="m.id === modelInfo.current" class="model-current">使用中</span>
        </button>
      </div>

      <div class="article-foot">
        <button class="primary" :disabled="testingModel || busyModel || !modelInfo.configured" @click="testModel">
          {{ testingModel ? '测试中…' : '测试当前模型' }}
        </button>
        <span class="muted small">发一句极简请求，确认模型此刻真的能回答</span>
      </div>
      <p v-if="testResult" class="test-result" :class="testResult.ok ? 'ok' : 'bad'">
        {{ testResult.ok
          ? `可用 · ${testResult.model} · ${testResult.latency_ms}ms · 回复「${testResult.reply}」`
          : `不可用 · ${testResult.error}` }}
      </p>
      <p class="error" v-if="modelError">{{ modelError }}</p>
    </section>
  </div>
</template>
