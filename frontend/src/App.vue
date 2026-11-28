<script setup>
import { onMounted, ref } from 'vue'
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

// 管理员主动从管理端点「前往学员端」时才允许停留在学员端；直接输网址进来的
// 管理员仍然送回管理端，避免「用管理员登录却掉进背单词页」。
const viewAsUser = ref(false)

onMounted(async () => {
  initTheme()
  try { viewAsUser.value = sessionStorage.getItem('ky-view-as') === 'user' } catch {}

  // /?auth=1：管理端退出登录后落在这里，直接弹学员端登录框（带「管理员」页签）。
  if (new URLSearchParams(location.search).get('auth') === '1') {
    store.showAuth = true
    try { history.replaceState(null, '', '/') } catch {}
  }

  await loadMe()

  // 管理端：只取系统状态，不加载任何学员数据（书籍、今日统计、学习计时都不启动）
  if (store.adminMode) {
    if (!store.me) return
    await refreshHeader()
    await loadModelInfo()
    return
  }

  // 管理员账号默认没有学员身份，直接送进管理端；只有主动点了「前往学员端」
  // 才放行——这时他的学习数据挂在影子学员行上，不会串到别人。
  if (store.me?.is_admin && !viewAsUser.value) {
    window.location.replace('/admin')
    return
  }

  await loadBooks()
  await refreshHeader()
  await loadTodayStats()
  await loadModelInfo()
  startStudyTimer()
})
</script>

<template>
  <div :class="store.adminMode ? 'adm-root' : 'app'">
    <!-- 管理端：独立登录页 + 独立外壳，不挂学员端的任何组件（没有底部 tab、没有学习/复习） -->
    <template v-if="store.adminMode">
      <AdminAuth v-if="!store.me" />
      <AdminShell v-else />
    </template>

    <!-- 学员端 -->
    <template v-else>
      <div class="redirect-hint" v-if="store.me?.is_admin && !viewAsUser">正在进入管理端…</div>

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
    </template>
  </div>
</template>

<style scoped>
.redirect-hint {
  min-height: 60vh; display: grid; place-items: center;
  color: var(--muted); font-size: 13px;
}
</style>
