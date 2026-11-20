<script setup>
import { ref } from 'vue'
import { api } from '../api.js'

const emit = defineEmits(['close', 'changed'])
const props = defineProps({
  info: { type: Object, required: true }   // { configured, current, models }
})

const models = ref(props.info.models || [])
const current = ref(props.info.current || '')
const busy = ref(false)
const testing = ref(false)
const testResult = ref(null)
const error = ref('')

async function choose(model) {
  if (busy.value || model.id === current.value) return
  busy.value = true
  error.value = ''
  testResult.value = null
  try {
    const r = await api('/api/llm/model', {
      method: 'POST', body: JSON.stringify({ model: model.id })
    })
    current.value = r.model
    models.value = models.value.map(m => ({ ...m, current: m.id === r.model }))
    emit('changed', r.model)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function test() {
  if (testing.value) return
  testing.value = true
  testResult.value = null
  error.value = ''
  try {
    testResult.value = await api('/api/llm/test', { method: 'POST' })
  } catch (e) {
    error.value = e.message
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <section class="card model-panel">
      <div class="article-head">
        <h2>生成模型</h2>
        <button class="ghost tiny" @click="emit('close')">关闭</button>
      </div>
      <p class="muted small">
        只允许在服务端预置的模型里切换，接口地址和密钥始终留在服务端，不会发送到浏览器。切换立即生效，无需重启。
      </p>

      <p v-if="!info.configured" class="notice">
        还没配置 Key。请先在 <code>backend/.env</code> 填 <code>LLM_API_KEY</code> 后重启，否则生成会走本地模板。
      </p>

      <div class="model-list">
        <button v-for="m in models" :key="m.id" class="model-item"
                :class="{ active: m.id === current }" :disabled="busy || !info.configured"
                @click="choose(m)">
          <span class="model-name">{{ m.id }}</span>
          <span class="model-note">{{ m.note }}</span>
          <span v-if="m.id === current" class="model-current">使用中</span>
        </button>
      </div>

      <div class="article-foot">
        <button class="primary" :disabled="testing || busy || !info.configured" @click="test">
          {{ testing ? '测试中…' : '测试当前模型' }}
        </button>
        <span class="muted small">发一句极简请求，确认模型此刻真的能回答</span>
      </div>
      <p v-if="testResult" class="test-result" :class="testResult.ok ? 'ok' : 'bad'">
        {{ testResult.ok
          ? `可用 · ${testResult.model} · ${testResult.latency_ms}ms · 回复「${testResult.reply}」`
          : `不可用 · ${testResult.error}` }}
      </p>
      <p class="error" v-if="error">{{ error }}</p>
    </section>
  </div>
</template>
