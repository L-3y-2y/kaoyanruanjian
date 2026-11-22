<script setup>
import { store, openModel, logout, toggleTheme } from '../store.js'
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <div class="logo">研</div>
      <div>
        <h1>考研背单词</h1>
        <p class="sub">五轮递进 · 日期批次复习 · 短文与周阅读</p>
      </div>
    </div>
    <div class="header-right">
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
        <button class="ghost tiny" @click="logout">切换账号</button>
        <button class="theme-toggle" @click="toggleTheme">
          {{ store.theme === 'light' ? '🌙 深色' : '☀️ 浅色' }}
        </button>
      </div>
    </div>
  </header>
</template>
