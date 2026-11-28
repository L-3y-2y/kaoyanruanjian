<script setup>
import { computed } from 'vue'
import { store, openModel, openSwitchAccount, toggleTheme } from '../store.js'

const logoText = computed(() => ({
  ky: '研', cet4: '四', cet6: '六', gk: '高',
}[store.currentBook?.code] || '研'))

const durationText = computed(() => {
  const s = store.todaySeconds || 0
  if (s < 60) return `${s} 秒`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} 分钟`
  const h = Math.floor(m / 60)
  return `${h} 小时 ${m % 60} 分`
})
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <div class="logo">{{ logoText }}</div>
      <div>
        <h1>{{ store.currentBook?.name || '单词学习' }}</h1>
        <p class="sub">五轮递进 · 艾宾浩斯复习 · 短文与周阅读</p>
      </div>
    </div>
    <div class="header-right">
      <div class="today-chip" v-if="store.me">
        <span>今日学习 <strong>{{ durationText }}</strong></span>
        <span class="sep">·</span>
        <span>已背 <strong>{{ store.todayLearned }}</strong> 词</span>
      </div>
      <div class="db-chip" v-if="store.health">
        <span class="dot"></span>
        已连接 {{ store.health.database }}
        <small>词库 {{ store.health.word_count }} 词 · {{ store.health.server_date }}</small>
        <button v-if="store.me.is_admin || store.me.local" class="model-chip" @click="openModel">
          {{ store.health.llm_configured ? '已接 · ' + (store.health.llm_model || '大模型') : '本地生成' }}
        </button>
        <span v-else class="model-chip static">
          {{ store.health.llm_configured ? '已接 · ' + (store.health.llm_model || '大模型') : '本地生成' }}
        </span>
      </div>
      <div class="account" v-if="store.me">
        <span class="who" :class="{ admin: store.me.is_admin }">
          {{ store.me.is_admin ? '管理员' : (store.me.local ? '本机模式' : '账号') }}：{{ store.me.username }}
        </span>
        <button class="ghost tiny" @click="openSwitchAccount">切换账号</button>
        <button class="theme-toggle" @click="toggleTheme">
          {{ store.theme === 'light' ? '🌙 深色' : '☀️ 浅色' }}
        </button>
      </div>
    </div>
  </header>
</template>
