<script setup>
import { onMounted } from 'vue'
import { store, initTheme, loadMe, refreshHeader, loadModelInfo, loadTodayStats, loadBooks, closeChat, closeModel, onAuthDone, onModelChanged } from './store.js'
import { startStudyTimer } from './studyTimer.js'
import AuthPanel from './components/AuthPanel.vue'
import TopBar from './components/TopBar.vue'
import TabBar from './components/TabBar.vue'
import MoreSheet from './components/MoreSheet.vue'
import LearnView from './components/LearnView.vue'
import ReviewView from './components/ReviewView.vue'
import ArticlesView from './components/ArticlesView.vue'
import ReadingView from './components/ReadingView.vue'
import WordBank from './components/WordBank.vue'
import AdminShell from './components/AdminShell.vue'
import AdminAuth from './components/admin/AdminAuth.vue'
import ModelPanel from './components/ModelPanel.vue'
import ChatPanel from './components/ChatPanel.vue'

onMounted(async () => {
  initTheme()
  await loadMe()
  await refreshHeader()
  await loadModelInfo()
  if (!store.adminMode) {
    await loadBooks()
    await loadTodayStats()
    startStudyTimer()
  }
})
</script>

<template>
  <div :class="store.adminMode ? 'adm-root' : 'app'">
    <!-- 管理端：独立登录页 + 独立外壳，不挂学员端的任何组件 -->
    <template v-if="store.adminMode">
      <AdminAuth v-if="!store.me" />
      <AdminShell v-else />
    </template>

    <template v-else>
      <AuthPanel v-if="store.showAuth || !store.me" @done="onAuthDone" @cancel="store.showAuth = false" />
      <ModelPanel v-if="store.showModel" :info="store.modelInfo" @close="closeModel" @changed="onModelChanged" />
      <ChatPanel v-if="store.chatOpen" :context="store.chatContext" @close="closeChat" />
      <MoreSheet v-if="store.moreOpen" />

      <template v-if="store.me && !store.showAuth">
        <TopBar />

        <div class="notice" v-if="store.me.local">
          当前是<strong>本机模式</strong>，进度存在这台机器上；点「我的 → 切换账号」注册，进度会自动归到你名下。
        </div>
        <p class="error" v-if="store.error">{{ store.error }}</p>

        <LearnView v-if="store.activeTab === 'learn'" :key="'learn-' + store.currentBook?.id" />
        <ReviewView v-else-if="store.activeTab === 'review'" :key="'review-' + store.currentBook?.id" />
        <ArticlesView v-else-if="store.activeTab === 'articles'" :key="'articles-' + store.currentBook?.id" />
        <ReadingView v-else-if="store.activeTab === 'reading'" :key="'reading-' + store.currentBook?.id" />
        <WordBank v-else-if="store.activeTab === 'wordbank'" />

        <footer>
          进度、短文快照与答卷只存在服务端 MySQL 的 {{ store.health?.database || 'kaoyanruanjian' }} 库里；
          刷新、换设备都会接着上次继续。短文与周阅读不计入五轮，也不改动两级状态。
        </footer>

        <TabBar />
      </template>
    </template>
  </div>
</template>
