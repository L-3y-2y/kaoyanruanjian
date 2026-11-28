<script setup>
import { computed } from 'vue'
import {
  store, closeMore, goTab, openChat, openModel, openSwitchAccount,
  toggleTheme, switchBook, logout,
} from '../store.js'

const books = computed(() => store.books || [])

const who = computed(() => {
  if (store.me?.local) return '本机模式'
  return store.me?.username || '我的'
})

function pick(tab) {
  closeMore()
  goTab(tab)
}
function ask() {
  closeMore()
  openChat(null)
}
function account() {
  closeMore()
  openSwitchAccount()
}
function admin() {
  // 管理端是独立页面（/admin）：宽屏侧边栏，不进底栏，也不占学员端一个 tab。
  // 清掉「以管理员身份浏览学员端」的放行标记，回管理端就按管理端规则来。
  try { sessionStorage.removeItem('ky-view-as') } catch {}
  closeMore()
  window.location.href = '/admin'
}
function theme() {
  toggleTheme()
  closeMore()
}
async function onBook(e) {
  await switchBook(Number(e.target.value))
}
</script>

<template>
  <div class="sheet-backdrop" @click.self="closeMore">
    <div class="sheet">
      <div class="grab"></div>
      <h3>{{ who }}</h3>
      <p class="sheet-sub">
        <template v-if="store.me?.is_admin">管理员账号</template>
        <template v-else-if="store.me?.local">数据存在这台机器上</template>
        <template v-else>普通账号</template>
        <template v-if="store.pool"> · 未掌握 {{ store.pool.available }} / {{ store.pool.total }}</template>
      </p>

      <div class="sheet-book">
        <span class="ic" style="width:30px;height:30px;border-radius:9px;display:grid;place-items:center;background:var(--primary-soft);color:var(--primary-deep);font-size:12px;font-weight:700">书</span>
        <select :value="store.currentBook?.id" @change="onBook">
          <option v-for="b in books" :key="b.id" :value="b.id">{{ b.name }}</option>
        </select>
      </div>

      <button class="sheet-row" v-if="store.health?.llm_configured" @click="ask">
        <span class="ic">AI</span>
        <span class="tx"><b>英语 AI</b><span>带上下文提问，答疑与造句</span></span>
        <span class="ar">›</span>
      </button>

      <button class="sheet-row" v-if="store.me?.local" @click="pick('wordbank')">
        <span class="ic">词</span>
        <span class="tx"><b>词库管理</b><span>增删改单词与释义</span></span>
        <span class="ar">›</span>
      </button>

      <button class="sheet-row" v-if="store.me?.is_admin || store.me?.local" @click="admin">
        <span class="ic">管</span>
        <span class="tx"><b>管理控制台</b><span>学员看板、词库、模型 —— 独立页面</span></span>
        <span class="ar">›</span>
      </button>

      <button class="sheet-row" v-if="store.me?.is_admin || store.me?.local" @click="openModel">
        <span class="ic">模</span>
        <span class="tx">
          <b>生成模型</b>
          <span>{{ store.health?.llm_configured ? (store.health?.llm_model || '已接入') : '本地生成' }}</span>
        </span>
        <span class="ar">›</span>
      </button>

      <button class="sheet-row" @click="theme">
        <span class="ic">{{ store.theme === 'light' ? '夜' : '日' }}</span>
        <span class="tx">
          <b>外观</b>
          <span>{{ store.theme === 'light' ? '当前浅色，切换到深色' : '当前深色，切换到浅色' }}</span>
        </span>
        <span class="ar">›</span>
      </button>

      <button class="sheet-row" @click="account">
        <span class="ic">账</span>
        <span class="tx"><b>切换账号</b><span>注册后进度自动归到名下</span></span>
        <span class="ar">›</span>
      </button>

      <button class="sheet-row" v-if="store.me && !store.me.local" @click="logout">
        <span class="ic">退</span>
        <span class="tx"><b>退出登录</b><span>回到本机模式</span></span>
        <span class="ar">›</span>
      </button>

      <p class="sheet-note" v-if="store.health">
        已连接 {{ store.health.database }} · 词库 {{ store.health.word_count }} 词 · {{ store.health.server_date }}
        <template v-if="store.me?.local"><br>当前是本机模式：进度只存在这台机器的账号里，注册后会自动归到你名下。</template>
      </p>
    </div>
  </div>
</template>
