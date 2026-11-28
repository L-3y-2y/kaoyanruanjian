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
  error.value = ''
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
  <div>
    <p class="error" v-if="error">{{ error }}</p>

    <div class="adm-row2">
      <section class="adm-card">
        <div class="adm-card-h">
          <h3>生成模型</h3>
          <span class="adm-pill" :class="fallback ? 'warn' : 'ok'">
            {{ fallback ? '本地模板' : '已接入' }}
          </span>
        </div>
        <p class="adm-muted">
          只能在服务端预置的模型里切换，地址与密钥始终留在服务端。切换立即生效，
          影响所有学员的短文、周阅读与语境填空。
        </p>

        <p v-if="fallback" class="adm-warnbox">
          还没配置 Key，所有生成都会走本地模板——句子会出现重复、难度不随书籍变化。
          在 <code>backend/.env</code> 填 <code>LLM_API_KEY</code> 后重启后端。
        </p>

        <div class="adm-models">
          <button v-for="m in info.models" :key="m.id" class="adm-model"
                  :class="{ on: m.id === info.current }"
                  :disabled="!!busy || fallback" @click="choose(m)">
            <span class="n">{{ m.id }}</span>
            <span class="d">{{ m.note }}</span>
            <span v-if="m.id === info.current" class="adm-pill ok">使用中</span>
            <span v-else-if="busy === m.id" class="adm-pill mut">切换中…</span>
          </button>
        </div>

        <div class="adm-mini-actions">
          <button class="adm-btn pri" :disabled="testing || fallback" @click="run">
            {{ testing ? '测试中…' : '测试当前模型' }}
          </button>
          <button class="adm-btn" @click="load">重新读取</button>
        </div>
        <p v-if="test" class="adm-pill" :class="test.ok ? 'ok' : 'bad'" style="margin-top:10px">
          {{ test.ok
            ? `可用 · ${test.model} · ${test.latency_ms}ms · 回复「${test.reply}」`
            : `不可用 · ${test.error}` }}
        </p>
      </section>

      <section class="adm-card">
        <div class="adm-card-h"><h3>降级与限流</h3></div>
        <div class="adm-hot" style="margin-top:2px">
          <div class="adm-hot-row plain">
            <span class="w">当前模型</span>
            <span class="v">{{ info.current || '未配置' }}</span>
          </div>
          <div class="adm-hot-row plain">
            <span class="w">生成方式</span>
            <span class="v">{{ fallback ? '本地模板（无 LLM）' : 'LLM 现写 + 相似度去重' }}</span>
          </div>
          <div class="adm-hot-row plain">
            <span class="w">失败处理</span>
            <span class="v">重试 3 次，仍失败则回退本地模板</span>
          </div>
          <div class="adm-hot-row plain">
            <span class="w">并发</span>
            <span class="v">3 路并行（免费额度易限流）</span>
          </div>
          <div class="adm-hot-row plain">
            <span class="w">缓存</span>
            <span class="v">同词再生成会比对历史句子，重复即重写</span>
          </div>
        </div>
        <p class="adm-tip">
          免费额度用完时不会报错，只会静默降级到本地模板——学员察觉不到，但句子质量会下降。
          如果你在学员端看到明显的套模板句式，先来这里跑一次「测试当前模型」。
        </p>
      </section>
    </div>
  </div>
</template>
