<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { store, logout } from '../../store.js'

// 管理端右上角的「身份 / 切换」入口：一个弹层里给出出口——
// 要么退出管理员去学员端登录，要么退出回到管理端登录页。管理端不能是死胡同。
const open = ref(false)
const busy = ref(false)
const root = ref(null)

const isLocal = computed(() => !!store.me?.local)
const name = computed(() => {
  if (!store.me) return '未登录'
  if (isLocal.value && !store.me.is_admin) return '本机用户'
  return store.me.username || '未登录'
})
const role = computed(() => (store.me?.is_admin ? '管理员' : isLocal.value ? '本机模式' : '访客'))
const initial = computed(() => (store.me?.is_admin ? '管' : isLocal.value ? '本' : name.value.slice(0, 1).toUpperCase()))

function toggle() { open.value = !open.value }
function close() { open.value = false }

// 去学员端。管理员保留登录态：后端给他的学习数据挂了独立的影子学员行，
// 不会串到同 id 的学员。App.vue 靠 ky-view-as 放行一次。
function toLearner() {
  close()
  try { sessionStorage.setItem('ky-view-as', 'user') } catch {}
  window.location.href = '/'
}

// 退出登录回到学员端登录框（带「管理员」页签），而不是管理端那套独立登录页。
async function signOut() {
  if (busy.value) return
  busy.value = true
  await logout()
  try { sessionStorage.removeItem('ky-view-as') } catch {}
  window.location.href = '/?auth=1'
}

function onDocClick(e) {
  if (open.value && root.value && !root.value.contains(e.target)) close()
}
function onKey(e) {
  if (e.key === 'Escape') close()
}
onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div class="adm-account" ref="root">
    <button class="adm-account-btn" type="button" :class="{ on: open }"
            :aria-expanded="open" aria-haspopup="menu" @click="toggle">
      <span class="adm-ava">{{ initial }}</span>
      <span class="adm-account-tx">
        <b>{{ name }}</b>
        <em>{{ role }}</em>
      </span>
      <i class="adm-caret">▾</i>
    </button>

    <transition name="adm-pop">
      <div v-if="open" class="adm-pop" role="menu">
        <div class="adm-pop-h">
          <span class="adm-ava lg">{{ initial }}</span>
          <div>
            <b>{{ name }}</b>
            <span>{{ role }} · 已登录</span>
          </div>
        </div>

        <button class="adm-pop-row" type="button" role="menuitem" @click="toLearner">
          <i>◧</i>
          <span>
            <b>前往学员端</b>
            <em>{{ isLocal ? '回到背单词页面' : '用当前管理员账号进入背单词页面' }}</em>
          </span>
        </button>

        <template v-if="!isLocal">
          <div class="adm-pop-sep"></div>
          <button class="adm-pop-row" type="button" role="menuitem" :disabled="busy" @click="signOut">
            <i>⏻</i>
            <span><b>退出登录</b><em>退出后回到学员端登录框</em></span>
          </button>
        </template>

        <p class="adm-pop-foot" v-else>
          本机模式免登录，换账号请在学员端「我的 → 切换账号」里操作。
        </p>
      </div>
    </transition>
  </div>
</template>
