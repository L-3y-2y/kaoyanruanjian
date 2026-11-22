<script setup>
import { onMounted } from 'vue'
import { store, initTheme, loadMe, refreshHeader, loadModelInfo, closeChat, closeModel, onAuthDone, onModelChanged } from './store.js'
import AuthPanel from './components/AuthPanel.vue'
import TopBar from './components/TopBar.vue'
import NavTabs from './components/NavTabs.vue'
import LearnView from './components/LearnView.vue'
import ReviewView from './components/ReviewView.vue'
import ArticlesView from './components/ArticlesView.vue'
import ReadingView from './components/ReadingView.vue'
import WordBank from './components/WordBank.vue'
import AdminConsole from './components/AdminConsole.vue'
import ModelPanel from './components/ModelPanel.vue'
import ChatPanel from './components/ChatPanel.vue'

onMounted(async () => {
  initTheme()
  await loadMe()
  await refreshHeader()
  await loadModelInfo()
})
</script>

<template>
  <div class="app">
    <AuthPanel v-if="store.showAuth || !store.me" @done="onAuthDone" @cancel="store.showAuth = false" />

    <ModelPanel v-if="store.showModel" :info="store.modelInfo" @close="closeModel" @changed="onModelChanged" />
    <ChatPanel v-if="store.chatOpen" :context="store.chatContext" @close="closeChat" />

    <template v-if="store.me && !store.showAuth">
      <TopBar />

      <div class="notice" v-if="store.me.local">
        当前是<strong>本机模式</strong>：进度存在本机账号里。点「切换账号」注册，已有进度会自动归到你名下；注册后各账号数据互相隔离。「词库管理」在这里直接可用，无需管理员账号。
      </div>
      <p class="error" v-if="store.error">{{ store.error }}</p>

      <NavTabs />

      <LearnView v-if="store.activeTab === 'learn'" />
      <ReviewView v-else-if="store.activeTab === 'review'" />
      <ArticlesView v-else-if="store.activeTab === 'articles'" />
      <ReadingView v-else-if="store.activeTab === 'reading'" />
      <WordBank v-else-if="store.activeTab === 'wordbank'" />
      <AdminConsole v-else-if="store.activeTab === 'admin'" :me="store.me" :health="store.health" />

      <footer>
        进度、短文快照与答卷只存在服务端 MySQL 的 {{ store.health?.database || 'kaoyanruanjian' }} 库里；
        刷新、换设备都会接着上次继续。短文与周阅读不计入五轮，也不改动两级状态。
      </footer>
    </template>
  </div>
</template>
