<script setup>
import { computed, onMounted } from 'vue'
import { store, logout } from '../store.js'
import OverviewPanel from './admin/OverviewPanel.vue'
import LearnersPanel from './admin/LearnersPanel.vue'
import ModelPanelAdmin from './admin/ModelPanelAdmin.vue'
import WordBank from './WordBank.vue'

const NAV = [
  { key: 'overview', label: '数据概览', ico: '▤', group: '运营' },
  { key: 'learners', label: '学员看板', ico: '◔', group: '运营' },
  { key: 'words',    label: '词库管理', ico: '▣', group: '内容' },
  { key: 'model',    label: '模型与成本', ico: '⚙', group: '系统' },
]

const groups = computed(() => {
  const out = []
  for (const n of NAV) {
    let g = out.find(x => x.name === n.group)
    if (!g) { g = { name: n.group, items: [] }; out.push(g) }
    g.items.push(n)
  }
  return out
})

const allowed = computed(() => !!store.me?.is_admin || !!store.me?.local)
const current = computed(() => NAV.find(n => n.key === store.adminTab) || NAV[0])
const title = computed(() => ({
  overview: '数据概览', learners: '学员看板', words: '词库管理', model: '模型与成本',
}[store.adminTab] || '数据概览'))
const crumb = computed(() => {
  if (store.adminTab === 'learners') return `共 ${store.learnersTotal} 名注册学员`
  if (store.adminTab === 'words') return `词库 ${store.health?.word_count ?? '—'} 词`
  if (store.adminTab === 'model') return `当前 ${store.modelInfo?.current || '未配置'}`
  return '只看能带来动作的数字'
})

onMounted(() => {
  if (!store.health) store.error = ''
})

function go(key) {
  store.adminTab = key
}
async function signOut() {
  await logout()
  // 管理端登出后仍停在管理端：重新加载会回到管理员登录页，不会掉回学员端。
  store.adminTab = 'overview'
  window.location.reload()
}
</script>

<template>
  <div class="adm-shell">
    <aside class="adm-side">
      <div class="adm-brand">
        <i>研</i>
        <div><b>考研英语</b><span>管理控制台</span></div>
      </div>

      <template v-if="allowed">
        <div v-for="g in groups" :key="g.name">
          <div class="adm-side-h">{{ g.name }}</div>
          <button v-for="n in g.items" :key="n.key"
                  class="adm-nav" :class="{ on: store.adminTab === n.key }"
                  @click="go(n.key)">
            <em>{{ n.ico }}</em>{{ n.label }}
          </button>
        </div>
      </template>

      <div class="adm-side-foot">
        v1.0 · 生产环境<br />
        {{ store.health?.database || 'kaoyanruanjian' }} ·
        {{ store.health?.word_count ?? '—' }} 词
      </div>
    </aside>

    <main class="adm-main">
      <header class="adm-head">
        <div>
          <h2>{{ allowed ? title : '需要管理员账号' }}</h2>
          <div class="adm-crumb">{{ allowed ? crumb : '这个页面只对管理员开放' }}</div>
        </div>
        <div class="adm-head-r">
          <button class="adm-btn" @click="signOut">退出</button>
          <div class="adm-ava">{{ store.me?.is_admin ? '管' : (store.me?.username || '我').slice(0, 1).toUpperCase() }}</div>
        </div>
      </header>

      <div v-if="!allowed" class="adm-card">
        <p style="margin-top:0">
          当前账号 <b>{{ store.me?.username }}</b> 不是管理员。管理端只接受管理员账号登录，
          请退出后用管理员账号重新进入。
        </p>
        <div style="display:flex;gap:10px;margin-top:14px">
          <button class="adm-btn pri" @click="signOut">退出并重新登录</button>
        </div>
      </div>

      <template v-else>
        <p class="error" v-if="store.error">{{ store.error }}</p>
        <OverviewPanel v-if="store.adminTab === 'overview'" />
        <LearnersPanel v-else-if="store.adminTab === 'learners'" />
        <ModelPanelAdmin v-else-if="store.adminTab === 'model'" />
        <div v-else-if="store.adminTab === 'words'" class="adm-narrow"><WordBank /></div>
      </template>
    </main>
  </div>
</template>
