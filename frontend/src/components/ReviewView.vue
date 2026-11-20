<script setup>
import { computed, inject, onMounted, ref } from 'vue'
import { api, roundName, stageLabel } from '../api.js'

const onChanged = inject('onChanged', () => {})
const goTab = inject('goTab', () => {})

const batches = ref([])
const due = ref({ enabled: false, words: [] })
const state = ref(null)
const activePlanId = ref(null)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const input = ref('')
const shownAnswer = ref('')
const r4Phase = ref('write')
const standardAnswer = ref('')

const progressPct = computed(() => {
  const s = state.value
  return s && s.total ? Math.round((s.done / s.total) * 100) : 0
})
const currentRound = computed(() => roundName[state.value?.kind] || '')

function resetTransient() {
  input.value = ''
  shownAnswer.value = ''
  r4Phase.value = 'write'
  standardAnswer.value = ''
}
function onInput() {
  if (shownAnswer.value) shownAnswer.value = ''
}

async function loadBatches() {
  batches.value = (await api('/api/review-batches')).batches
  due.value = await api('/api/review/due')
}
async function loadReview(planId) {
  activePlanId.value = planId
  state.value = await api(`/api/state?mode=review&plan_id=${planId}`)
  resetTransient()
}
async function refresh() {
  loading.value = true
  error.value = ''
  try {
    await loadBatches()
    if (activePlanId.value) await loadReview(activePlanId.value)
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
    if (r.standard_answer != null) {
      standardAnswer.value = r.standard_answer
      r4Phase.value = 'assess'
      return
    }
    if (r.correct === false) { shownAnswer.value = r.answer; input.value = ''; return }
    if (r.state) { state.value = r.state; resetTransient() }
    await loadBatches()
    onChanged()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

async function restart(planId) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const r = await api('/api/review/restart', {
      method: 'POST', body: JSON.stringify({ plan_id: planId })
    })
    activePlanId.value = planId
    state.value = r.state
    resetTransient()
    await loadBatches()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

function back() {
  activePlanId.value = null
  state.value = null
  refresh()
}

onMounted(refresh)
</script>

<template>
  <main>
    <section v-if="loading" class="card muted">正在读取服务端状态…</section>
    <p class="error" v-if="error">{{ error }}</p>

    <template v-if="!activePlanId">
      <section class="card">
        <h2>按学习日期归档的批次</h2>
        <p class="muted">每天一组，永久保存，随时进入。默认没有到期提醒，也不会自动开始。</p>
        <div v-if="due.enabled && due.words.length" class="due-card">
          <strong>今日该复习 {{ due.words.length }} 词</strong>
          <p class="muted small">可选功能：按 1/2/4/7/15/30 天间隔排期，答错缩短间隔。点某个词会进入它所在的日期批次，原复习方式完全不变。</p>
          <div class="chips">
            <button v-for="w in due.words" :key="w.progress_id" class="chip due-word"
                    @click="loadReview(w.daily_plan_id)">
              {{ w.word }} · {{ w.study_date }}
            </button>
          </div>
        </div>
        <div v-if="!batches.length" class="empty">还没有可复习的批次。先去「今日学习」完成 R1～R3。</div>
        <div class="batch" v-for="b in batches" :key="b.plan_id">
          <div>
            <strong>{{ b.study_date }}</strong>
            <span class="stage" :class="b.stage">{{ stageLabel[b.stage] }}</span>
            <p class="muted small">{{ b.word_count }} 词 · R4/R5 已完成 {{ b.done }} / {{ b.total }}</p>
          </div>
          <div class="batch-actions">
            <button class="primary" @click="loadReview(b.plan_id)">
              {{ b.stage === 'mastered' ? '回看' : '进入复习' }}
            </button>
            <button class="ghost" v-if="b.stage === 'mastered'" @click="restart(b.plan_id)">重做</button>
          </div>
        </div>
      </section>
    </template>

    <template v-else>
      <button class="ghost back" @click="back">← 返回批次列表</button>

      <section v-if="state?.kind === 'review_done'" class="card done">
        <h2>这个批次已经真正学完</h2>
        <p>R4～R5 全部走完，{{ state.word_count }} 个词升级为「真正学完」。批次仍保留在列表里，可回看、可重做。</p>
        <p class="muted">现在可以去「每日短文」看这个批次生成的短文了；短文与阅读不会改动任何轮次状态。</p>
        <div class="actions">
          <button class="primary" @click="goTab('articles')">去看每日短文</button>
          <button class="ghost" @click="back">返回批次列表</button>
        </div>
      </section>

      <section v-else-if="state?.kind?.startsWith('r')" class="card quiz">
        <div class="quiz-head">
          <span class="round">{{ currentRound }}</span>
          <span class="muted">第 {{ state.cycle_no }} / {{ state.cycle_count }} 组</span>
        </div>
        <div class="bar"><div :style="{ width: progressPct + '%' }"></div></div>
        <p class="muted small">已完成 {{ state.done }} / {{ state.total }}</p>

        <template v-if="state.kind === 'r4'">
          <p class="prompt">看英文，写汉语意思</p>
          <div class="word-big">{{ state.word }}</div>
          <template v-if="r4Phase === 'write'">
            <input v-model="input" @keyup.enter="submit({ action: 'r4_reveal', value: input })"
                   placeholder="写出你理解的汉语意思" />
            <div class="actions">
              <button class="primary" :disabled="busy" @click="submit({ action: 'r4_reveal', value: input })">写完，看标准答案</button>
            </div>
          </template>
          <template v-else>
            <p class="answer">标准答案：{{ standardAnswer }}</p>
            <p class="prompt">自己评一下记住了没有</p>
            <div class="actions">
              <button class="primary" :disabled="busy" @click="submit({ action: 'self_assess', value: input, assessment: 'remembered' })">记住了</button>
              <button :disabled="busy" @click="submit({ action: 'self_assess', value: input, assessment: 'forgotten' })">没记住</button>
            </div>
            <p class="hint">「没记住」只做标记与统计，不会让你重填，也不改变本批次流程。</p>
          </template>
        </template>

        <template v-else-if="state.kind === 'r5'">
          <p class="prompt">看汉语，默写英文</p>
          <div class="word-cn">{{ state.translation }} <small>{{ state.pos || '' }}</small></div>
          <input v-model="input" @input="onInput" @keyup.enter="submit({ action: 'spell', value: input })"
                 autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="默写英文单词" />
          <div class="actions"><button class="primary" :disabled="busy" @click="submit({ action: 'spell', value: input })">提交</button></div>
          <p class="answer" v-if="shownAnswer">正确答案：{{ shownAnswer }}</p>
        </template>
      </section>
    </template>
  </main>
</template>
