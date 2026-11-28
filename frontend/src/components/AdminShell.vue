<script setup>
import { computed, ref } from 'vue'
import { store, logout } from '../store.js'
import OverviewPanel from './admin/OverviewPanel.vue'
import LearnersPanel from './admin/LearnersPanel.vue'
import WordsPanel from './admin/WordsPanel.vue'
import ModelDrawer from './admin/ModelDrawer.vue'

const NAV = [
  { key: 'overview', label: '概览', ico: '▤' },
  { key: 'learners', label: '学员', ico: '◔' },
  { key: 'words',    label: '词库', ico: '▣' },
]
const PANEL = { overview: OverviewPanel, learners: LearnersPanel, words: WordsPanel }

const allowed = computed(() => !!store.me?.is_admin || !!store.me?.local)
const panel = computed(() => PANEL[store.adminTab] || OverviewPanel)
const title = computed(() => (NAV.find(n => n.key === store.adminTab) || NAV[0]).label)
const sub = computed(() => {
  if (store.adminTab === 'learners') return `${store.learnersTotal} 名注册学员 · 越需要处理越靠前`
  if (store.adminTab === 'words') return `全库 ${store.health?.word_count ?? '—'} 词 · 按最近修改`
  return '今天有没有事情需要我做'
})

const drawer = ref(false)
const panelKey = ref(0)

async function signOut() {
  await logout()
  window.location.reload()
}
</script>

<template>
  <div class="adm-shell">
    <aside class="adm-side">
      <div class="adm-brand">
        <i>研</i>
        <div><b>考研英语</b><span>管理</span></div>
      </div>

      <template v-if="allowed">
        <button v-for="n in NAV" :key="n.key" class="adm-nav"
                :class="{ on: store.adminTab === n.key }" @click="store.adminTab = n.key">
          <em>{{ n.ico }}</em>{{ n.label }}
          <span v-if="n.key === 'learners' && store.learnersTotal" class="cnt">{{ store.learnersTotal }}</span>
        </button>

        <div class="adm-side-foot">
          <button class="adm-stat" @click="drawer = true">
            <span>生成</span>
            <b>{{ store.modelInfo?.configured ? (store.modelInfo.current || '已接入') : '本地模板' }}</b>
          </button>
          <div class="adm-stat static"><span>词库</span><b>{{ store.health?.word_count ?? '—' }} 词</b></div>
          <div class="adm-stat static"><span>库</span><b>{{ store.health?.database || '—' }}</b></div>
        </div>
      </template>
    </aside>

    <main class="adm-main">
      <header class="adm-head">
        <div>
          <h2>{{ allowed ? title : '没有管理权限' }}</h2>
          <div class="sub">{{ allowed ? sub : '这个页面只对管理员开放' }}</div>
        </div>
        <div class="adm-head-r">
          <button class="adm-btn" v-if="allowed" @click="panelKey++">刷新</button>
          <button class="adm-btn ghost" @click="signOut">退出</button>
        </div>
      </header>

      <section v-if="!allowed" class="adm-card">
        <div class="adm-pad">
          <p class="adm-muted" style="margin:0 0 12px">
            当前账号 <b>{{ store.me?.username }}</b> 不是管理员。退出后用管理员账号登录即可。
          </p>
          <button class="adm-btn solid" @click="signOut">退出登录</button>
        </div>
      </section>

      <template v-else>
        <p class="error" v-if="store.error">{{ store.error }}</p>
        <component :is="panel" :key="store.adminTab + '-' + panelKey" />
      </template>

      <ModelDrawer v-if="drawer" @close="drawer = false" />
    </main>
  </div>
</template>
