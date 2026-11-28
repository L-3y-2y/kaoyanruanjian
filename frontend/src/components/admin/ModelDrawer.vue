<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api.js'
import { store } from '../../store.js'

const info = ref({ configured: false, current: '', models: [] })
const busy = ref('')
const testing = ref(false)
const test = ref(null)
const error = ref('')

const fallback = computed(() => !info.value.configured)

async function load() {
  try {
    info.value = await api('/api/llm/models')
    store.modelInfo = info.value
  } catch (e) { error.value = e.message }
}

async function choose(m) {
  if (busy.value || m.id === info.value.current) return
  busy.value = m.id
  error.value = ''
  try {
    const r = await api('/api/llm/model', { method: 'POST', body: JSON.stringify({ model: m.id }) })
    info.value.current = r.model
    store.modelInfo = { ...info.value, current: r.model }
  } catch (e) { error.value = e.message }
  finally { busy.value = '' }
}

async function run() {
  if (testing.value) return
  testing.value = true
  test.value = null
  error.value = ''
  try { test.value = await api('/api/llm/test', { method: 'POST' }) }
  catch (e) { error.value = e.message }
  finally { testing.value = false }
}

onMounted(load)
</script>

<template>
  <div class="adm-drawer-mask" @click.self="$emit('close')">
    <aside class="adm-drawer">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
        <div>
          <h3>生成模型</h3>
          <p class="adm-muted">只允许在服务端预置的模型里切换，地址与密钥留在服务端。</p>
        </div>
        <button class="adm-btn ghost" @click="$emit('close')">关闭</button>
      </div>

      <p class="adm-muted" v-if="fallback" style="color:var(--warn)">
        还没配置 Key，所有生成都会走本地模板。在 backend/.env 填 LLM_API_KEY 后重启后端。
      </p>

      <button v-for="m in info.models" :key="m.id" class="adm-model"
              :class="{ on: m.id === info.current }"
              :disabled="!!busy || fallback" @click="choose(m)">
        <span class="n">{{ m.id }}</span>
        <span class="d">{{ m.note }}</span>
        <span v-if="m.id === info.current" class="adm-pill ok">使用中</span>
        <span v-else-if="busy === m.id" class="adm-pill">切换中</span>
      </button>

      <div style="display:flex;gap:6px;margin:10px 0 12px">
        <button class="adm-btn solid" :disabled="testing || fallback" @click="run">
          {{ testing ? '测试中…' : '测试当前模型' }}
        </button>
        <button class="adm-btn" @click="load">重新读取</button>
      </div>

      <p v-if="test" class="adm-pill" :class="test.ok ? 'ok' : 'bad'" style="display:block;padding:6px 9px;line-height:1.6">
        {{ test.ok
          ? `可用 · ${test.model} · ${test.latency_ms}ms · 回复「${test.reply}」`
          : `不可用 · ${test.error}` }}
      </p>
      <p class="adm-muted" v-if="error" style="color:var(--primary-deep)">{{ error }}</p>

      <h3 style="margin:18px 0 6px;font-size:12.5px">运行规则</h3>
      <div class="adm-kv"><b>生成方式</b><span>{{ fallback ? '本地模板' : 'LLM 现写 + 相似度去重' }}</span></div>
      <div class="adm-kv"><b>失败处理</b><span>重试 3 次，仍失败回退本地模板</span></div>
      <div class="adm-kv"><b>并发</b><span>3 路（免费额度易限流）</span></div>
      <div class="adm-kv"><b>重复句</b><span>比对历史句子，重复即重写</span></div>
      <p class="adm-note">
        额度用完不会报错，只会静默降级到本地模板——句子质量会下降但界面看不出来。
        看到套模板的句式，先在这里跑一次测试。
      </p>
    </aside>
  </div>
</template>
