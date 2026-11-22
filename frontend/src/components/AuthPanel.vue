<script setup>
import { ref } from 'vue'
import { api } from '../api.js'
import { store, logout } from '../store.js'

const emit = defineEmits(['done', 'cancel'])
const mode = ref('login')          // login | register | admin
const username = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')

async function doLogout() {
  await logout()
}

async function submit() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const path = mode.value === 'register' ? '/api/auth/register'
      : mode.value === 'admin' ? '/api/auth/admin-login' : '/api/auth/login'
    const r = await api(path, {
      method: 'POST',
      body: JSON.stringify({ username: username.value.trim(), password: password.value })
    })
    if (r.inherited_local_progress) {
      notice.value = '注册成功，本机已有的学习进度已自动归到这个账号。'
    }
    password.value = ''
    emit('done', r.user)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="auth-wrap">
    <section class="card auth-card">
      <button class="auth-close" @click="emit('cancel')" aria-label="返回">✕</button>
      <h2>{{ mode === 'register' ? '创建账号' : mode === 'admin' ? '管理员登录' : '登录' }}</h2>
      <p class="muted small">
        进度、短文、阅读答卷全部按账号隔离保存。首次注册会自动接管本机已有的学习进度，不会丢数据。
      </p>

      <div class="auth-modes">
        <button :class="{ active: mode === 'login' }" @click="mode = 'login'">登录</button>
        <button :class="{ active: mode === 'register' }" @click="mode = 'register'">注册</button>
        <button :class="{ active: mode === 'admin' }" @click="mode = 'admin'">管理员</button>
      </div>

      <label class="field">用户名
        <input v-model="username" autocomplete="username" placeholder="中英文、数字、下划线，2～32 位"
               @keyup.enter="submit" />
      </label>
      <label class="field">密码
        <input v-model="password" type="password"
               :autocomplete="mode === 'register' ? 'new-password' : 'current-password'"
               placeholder="至少 8 位" @keyup.enter="submit" />
      </label>

      <p class="error" v-if="error">{{ error }}</p>
      <p class="notice" v-if="notice">{{ notice }}</p>

      <div class="actions">
        <button class="primary" :disabled="busy || !username || password.length < 8" @click="submit">
          {{ mode === 'register' ? '注册并登录' : '登录' }}
        </button>
        <button class="ghost" @click="emit('cancel')">返回</button>
      </div>
      <div class="auth-logout" v-if="store.me && !store.me.local">
        当前登录：<strong>{{ store.me.username }}</strong>
        <button class="ghost tiny" @click="doLogout">退出登录</button>
      </div>
      <p class="hint">
        没有预置管理员账号，也没有默认密码。本机模式直接点「词库管理」即可用；需要正式管理员登录时，双击根目录的 <code>setup_admin.bat</code> 自己设置账号密码。
      </p>
    </section>
  </div>
</template>
