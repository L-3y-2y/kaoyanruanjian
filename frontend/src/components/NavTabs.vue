<script setup>
import { ref } from 'vue'
import { store, goTab, openChat, switchBook } from '../store.js'

const switching = ref(false)

async function onBookChange(e) {
  switching.value = true
  try {
    await switchBook(Number(e.target.value))
  } finally {
    switching.value = false
  }
}
</script>

<template>
  <nav class="tabs">
    <button :class="{ active: store.activeTab === 'learn' }" @click="goTab('learn')">今日学习</button>
    <button :class="{ active: store.activeTab === 'review' }" @click="goTab('review')">
      复习板块 <span class="badge" v-if="store.batchesCount">{{ store.batchesCount }}</span>
    </button>
    <button :class="{ active: store.activeTab === 'articles' }" @click="goTab('articles')">每日短文</button>
    <button :class="{ active: store.activeTab === 'reading' }" @click="goTab('reading')">周阅读</button>
    <button v-if="store.me.local" :class="{ active: store.activeTab === 'wordbank' }" @click="goTab('wordbank')">词库管理</button>
    <button v-if="store.me.is_admin" class="admin-tab" :class="{ active: store.activeTab === 'admin' }" @click="goTab('admin')">
      管理后台<span class="admin-dot"></span>
    </button>
    <button v-if="store.health?.llm_configured" @click="openChat(null)">英语 AI</button>
    <label class="tab-book" v-if="store.books.length" title="切换书籍">
      <span>书籍</span>
      <select :value="store.currentBook?.id" :disabled="switching" @change="onBookChange">
        <option v-for="b in store.books" :key="b.id" :value="b.id">{{ b.name }}</option>
      </select>
    </label>
    <span class="pool" v-if="store.pool">未掌握池 {{ store.pool.available }} / {{ store.pool.total }}</span>
  </nav>
</template>
