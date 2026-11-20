<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const stats = ref(null)
const words = ref([])
const total = ref(0)
const q = ref('')
const form = ref({ word: '', phonetic: '', translation: '', pos: '', tag: 'ky' })
const error = ref('')
const notice = ref('')
const busy = ref(false)

async function load() {
  error.value = ''
  try {
    stats.value = await api('/api/admin/stats')
    const r = await api(`/api/admin/words?q=${encodeURIComponent(q.value)}&limit=30`)
    words.value = r.words
    total.value = r.total
  } catch (e) { error.value = e.message }
}

async function save() {
  if (busy.value || !form.value.word.trim()) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await api('/api/admin/word', { method: 'POST', body: JSON.stringify(form.value) })
    notice.value = `已保存：${form.value.word}`
    form.value = { word: '', phonetic: '', translation: '', pos: '', tag: 'ky' }
    await load()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

onMounted(load)
</script>

<template>
  <main>
    <p class="error" v-if="error">{{ error }}</p>
    <p class="notice" v-if="notice">{{ notice }}</p>

    <section class="card" v-if="stats">
      <h2>词库管理</h2>
      <p class="muted small">管理员只管词库。当前 {{ total }} 词 · {{ stats.users }} 个账号 · {{ stats.plans }} 个日期批次 · {{ stats.articles }} 篇文章。</p>
      <div class="chips">
        <span v-for="s in stats.sources" :key="s.source">{{ s.source }}：{{ s.c }}</span>
      </div>
    </section>

    <section class="card">
      <h2>新增 / 修正词条</h2>
      <div class="admin-form">
        <input v-model="form.word" maxlength="128" placeholder="单词（必填）" />
        <input v-model="form.phonetic" maxlength="128" placeholder="音标" />
        <input v-model="form.pos" maxlength="64" placeholder="词性" />
        <input v-model="form.tag" maxlength="128" placeholder="标签，默认 ky" />
        <input v-model="form.translation" maxlength="2000" placeholder="中文释义（必填，多个义项用分号隔开）" />
      </div>
      <button class="primary" :disabled="busy || !form.word || !form.translation" @click="save">保存</button>
      <p class="hint">同一个词再次保存会覆盖释义并标记 <code>verified=1</code>，不会影响任何已有的学习进度或短文快照。</p>
    </section>

    <section class="card">
      <h2>词条检索</h2>
      <div class="plan-row">
        <input v-model="q" placeholder="按拼写或中文释义搜索" @keyup.enter="load" />
        <button class="primary" @click="load">搜索</button>
      </div>
      <div class="word-row" v-for="w in words" :key="w.id">
        <div><strong>{{ w.word }}</strong> <span class="muted small">{{ w.phonetic }} {{ w.pos }}</span>
          <p class="muted small">{{ w.translation }}</p></div>
        <span class="chip">{{ w.source || '-' }}<span v-if="w.verified"> · 已核对</span></span>
      </div>
      <div v-if="!words.length" class="empty">没有匹配的词条。</div>
    </section>
  </main>
</template>
