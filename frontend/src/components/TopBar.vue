<script setup>
import { computed } from 'vue'
import { store, openMore, toggleTheme } from '../store.js'

const logoText = computed(() => ({
  ky: '研', cet4: '四', cet6: '六', gk: '高',
}[store.currentBook?.code] || '研'))

const initial = computed(() => {
  const name = store.me?.username || '我'
  return store.me?.is_admin ? '管' : name.slice(0, 1).toUpperCase()
})

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
        <p class="sub">
          今日 <strong>{{ durationText }}</strong> · 已背 <strong>{{ store.todayLearned }}</strong> 词
        </p>
      </div>
    </div>
    <div class="header-right">
      <button class="theme-toggle" @click="toggleTheme">
        {{ store.theme === 'light' ? '深色' : '浅色' }}
      </button>
      <button class="avatar-btn" @click="openMore">{{ initial }}</button>
    </div>
  </header>
</template>
