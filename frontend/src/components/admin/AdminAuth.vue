<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../../api.js'
import { loadMe, store } from '../../store.js'

const username = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')

async function submit() {
  if (busy.value) return
  error.value = ''
  if (!username.value.trim() || !password.value) {
    error.value = '请填写用户名和密码'
    return
  }
  busy.value = true
  try {
    await api('/api/auth/admin-login', {
      method: 'POST',
      body: JSON.stringify({ username: username.value.trim(), password: password.value }),
    })
    await loadMe()
    if (!store.me?.is_admin) error.value = '登录成功但该账号没有管理权限'
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

onMounted(() => { store.error = '' })
</script>

<template>
  <div class="adm-login">
    <form class="adm-login-card" @submit.prevent="submit">
      <div class="lg">
        <i>研</i>
        <div><b>管理控制台</b><span>考研英语 · 后台</span></div>
      </div>

      <label for="adm-u">管理员</label>
      <input id="adm-u" v-model="username" class="adm-input" autocomplete="username"
             placeholder="管理员用户名" @keydown.enter.prevent="submit" />
      <label for="adm-p">密码</label>
      <input id="adm-p" v-model="password" class="adm-input" type="password"
             autocomplete="current-password" placeholder="密码" @keydown.enter.prevent="submit" />

      <p class="err" v-if="error">{{ error }}</p>
      <button class="adm-btn solid" type="submit" :disabled="busy">
        {{ busy ? '登录中…' : '登录' }}
      </button>

      <p class="tip">
        这里只接受管理员账号。没有账号时，在服务器上运行
        <code>scripts/create_admin.py</code> 创建，密码由你自己设定。
      </p>
    </form>
  </div>
</template>
