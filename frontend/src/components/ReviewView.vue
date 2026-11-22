<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, playWord } from '../api.js'
import { refreshHeader as onChanged, goTab } from '../store.js'

const stats = ref({ due: 0, pool: 0, mastered: 0 })
const state = ref(null)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const input = ref('')
const shownAnswer = ref('')
const revealed = ref(false)

const subStepLabel = computed(() => {
  const s = state.value?.sub_step
  return s === 1 ? '① 认不认识' : s === 2 ? '② 听音默写' : s === 3 ? '③ 汉译英默写' : ''
})
const progressText = computed(() => {
  const s = state.value
  if (!s) return ''
  return `第 ${(s.review_stage || 0) + 1} 次复习`
})

function resetTransient() {
  input.value = ''
  shownAnswer.value = ''
  revealed.value = false
}
function onInput() {
  if (shownAnswer.value) shownAnswer.value = ''
}

async function loadStats() {
  stats.value = await api('/api/review/stats')
}
async function loadReview() {
  state.value = await api('/api/state?mode=review')
  resetTransient()
}
async function refresh() {
  loading.value = true
  error.value = ''
  try {
    await Promise.all([loadStats(), loadReview()])
  } catch (e) { error.value = e.message }
  finally { loading.value = false }
}

async function submit(payload) {
  if (busy.value || !state.value?.progress_id) return
  busy.value = true
  error.value = ''
  try {
    const r = await api('/api/answer', {
      method: 'POST',
      body: JSON.stringify({ progress_id: state.value.progress_id, ...payload })
    })
    if (r.reveal) { revealed.value = true; return }
    if (r.correct === false) { shownAnswer.value = r.answer; input.value = ''; return }
    if (r.state) { state.value = r.state; resetTransient() }
    await loadStats()
    onChanged()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

// 「熟知」：这个词我已经会了，直接标记背熟，从复习队列移除。
async function markKnown() {
  if (busy.value || !state.value?.progress_id) return
  busy.value = true
  error.value = ''
  try {
    await api('/api/word/known', {
      method: 'POST',
      body: JSON.stringify({ progress_id: state.value.progress_id })
    })
    state.value = await api('/api/state?mode=review')
    resetTransient()
    await loadStats()
    onChanged()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

onMounted(refresh)
</script>

<template>
  <main>
    <section v-if="loading" class="card muted">正在读取服务端状态…</section>
    <p class="error" v-if="error">{{ error }}</p>

    <section class="card">
      <h2>复习（艾宾浩斯遗忘曲线）</h2>
      <p class="muted">学过的词按 1 → 2 → 4 → 7 → 15 → 30 天自动排期；每次复习过三轮（认识 → 听写 → 默写），三轮都过才进入下一个间隔，全部间隔走完才算背熟。</p>
      <div class="review-stats">
        <div class="stat"><strong>{{ stats.due }}</strong><span>今日到期</span></div>
        <div class="stat"><strong>{{ stats.pool }}</strong><span>复习池中</span></div>
        <div class="stat"><strong>{{ stats.mastered }}</strong><span>已背熟</span></div>
      </div>
    </section>

    <section v-if="state?.kind === 'review'" class="card quiz">
      <div class="quiz-head">
        <span class="round">{{ subStepLabel }}</span>
        <span class="muted">{{ progressText }}</span>
        <button class="known-btn" :disabled="busy" @click="markKnown">熟知 · 跳过</button>
      </div>

      <template v-if="state.sub_step === 1">
        <div class="word-big">{{ state.word }}</div>
        <div class="reveal" v-if="revealed">
          <span class="phonetic">{{ state.phonetic || '' }}</span>
          <span>{{ state.pos || '' }} {{ state.translation }}</span>
          <button class="ghost" @click="playWord(state.speak_text)">播放读音</button>
        </div>
        <div class="actions">
          <button v-if="!revealed" :disabled="busy" @click="submit({ action: 'r_unknown' })">不认识</button>
          <button class="primary" :disabled="busy" @click="submit({ action: 'r_known' })">认识</button>
        </div>
        <p class="hint" v-if="!revealed">先看英文。点「不认识」看答案（这个词回到第 1 天重来），点「认识」进入听写。</p>
      </template>

      <template v-else-if="state.sub_step === 2">
        <p class="prompt">听读音，默写这个单词</p>
        <button class="ghost speak" @click="playWord(state.speak_text)">播放读音（可重播）</button>
        <input v-model="input" @input="onInput" @keyup.enter="submit({ action: 'r_spell', value: input })"
               autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="默写英文单词" />
        <div class="actions"><button class="primary" :disabled="busy" @click="submit({ action: 'r_spell', value: input })">提交</button></div>
        <p class="answer" v-if="shownAnswer">正确答案：{{ shownAnswer }}</p>
      </template>

      <template v-else-if="state.sub_step === 3">
        <p class="prompt">看汉语，默写英文</p>
        <div class="word-cn">{{ state.translation }} <small>{{ state.pos || '' }}</small></div>
        <input v-model="input" @input="onInput" @keyup.enter="submit({ action: 'r_spell', value: input })"
               autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="默写英文单词" />
        <div class="actions"><button class="primary" :disabled="busy" @click="submit({ action: 'r_spell', value: input })">提交</button></div>
        <p class="answer" v-if="shownAnswer">正确答案：{{ shownAnswer }}</p>
      </template>
    </section>

    <section v-else-if="state?.kind === 'review_empty'" class="card done">
      <h2>今天没有需要复习的词</h2>
      <p>复习池里还有 {{ stats.pool }} 个词，到期的词都复习完后会自动排到下一个间隔。</p>
      <p class="muted">目前已背熟 {{ stats.mastered }} 个词，继续保持！</p>
    </section>
  </main>
</template>
