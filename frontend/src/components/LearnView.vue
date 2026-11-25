<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, playWord, roundName } from '../api.js'
import { refreshHeader as onChanged, loadTodayStats, goTab } from '../store.js'

const pool = ref(null)
const state = ref(null)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const countN = ref(10)
const input = ref('')
const shownAnswer = ref('')
const revealed = ref(false)
const r4Phase = ref('write')
const standardAnswer = ref('')
const correctFlash = ref(false)
let flashTimer = null

const progressPct = computed(() => {
  const s = state.value
  if (!s) return 0
  // 词级进度优先，保证和滑轴选的数量一致
  if (s.word_total) return Math.round((s.word_done / s.word_total) * 100)
  return s.total ? Math.round((s.done / s.total) * 100) : 0
})
const currentRound = computed(() => roundName[state.value?.kind] || '')

function onProgressChange() {
  onChanged()
  loadTodayStats()
}

// 学习数量滑轴：1～100，上限不超过「未掌握池」剩余词数。
const maxN = computed(() => {
  const avail = pool.value?.available
  return Math.max(1, Math.min(100, avail ?? 100))
})
watch(maxN, (m) => { if (countN.value > m) countN.value = m })

function resetTransient() {
  input.value = ''
  shownAnswer.value = ''
  revealed.value = false
  r4Phase.value = 'write'
  standardAnswer.value = ''
}
function onInput() {
  if (shownAnswer.value) shownAnswer.value = ''
}

async function loadPool() { pool.value = await api('/api/pool') }
async function loadLearn() {
  state.value = await api('/api/state?mode=learn')
  resetTransient()
}
async function refresh() {
  loading.value = true
  error.value = ''
  try {
    await Promise.all([loadPool(), loadLearn()])
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function createPlan() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const r = await api('/api/plan', {
      method: 'POST', body: JSON.stringify({ count: Number(countN.value) })
    })
    state.value = r.state
    resetTransient()
    await loadPool()
    onProgressChange()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

async function applyState(s) {
  state.value = s
  resetTransient()
  await loadPool()
  onProgressChange()
}

async function submit(payload) {
  if (busy.value || !state.value?.progress_id) return
  busy.value = true
  error.value = ''
  const kindBefore = state.value?.kind
  try {
    const r = await api('/api/answer', {
      method: 'POST',
      body: JSON.stringify({ progress_id: state.value.progress_id, ...payload })
    })
    if (r.reveal) { revealed.value = true; busy.value = false; return }
    if (r.standard_answer != null) {
      standardAnswer.value = r.standard_answer
      r4Phase.value = 'assess'
      busy.value = false
      return
    }
    if (r.correct === false) { shownAnswer.value = r.answer; input.value = ''; busy.value = false; return }
    // 听写/默写答对：先给出「默写正确」反馈，再跳下一个词。
    const wasSpell = ['r2', 'r3', 'r5'].includes(kindBefore)
    if (wasSpell && r.state) {
      correctFlash.value = true
      flashTimer = setTimeout(async () => {
        try {
          await applyState(r.state)
        } catch (e) {
          error.value = e.message
        } finally {
          correctFlash.value = false
          busy.value = false
        }
      }, 650)
      return
    }
    await applyState(r.state)
    busy.value = false
  } catch (e) {
    error.value = e.message
    busy.value = false
  }
}

// 「熟知」：这个词我已经会了，直接标记背熟，跳过学习和复习。
async function markKnown() {
  if (busy.value || !state.value?.progress_id) return
  busy.value = true
  error.value = ''
  try {
    await api('/api/word/known', {
      method: 'POST',
      body: JSON.stringify({ progress_id: state.value.progress_id })
    })
    state.value = await api('/api/state?mode=learn')
    resetTransient()
    await loadPool()
    onProgressChange()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

onMounted(refresh)
onBeforeUnmount(() => { if (flashTimer) clearTimeout(flashTimer) })
</script>

<template>
  <main>
    <section v-if="loading" class="card muted">正在读取服务端状态…</section>
    <p class="error" v-if="error">{{ error }}</p>

    <section v-if="state?.kind === 'need_plan'" class="card">
      <h2>今天要背多少个词？</h2>
      <p class="muted">拖动滑轴选择数量，1～100 个任选。新词只从「未掌握池」取，不会和以前的日期批次重复。</p>
      <div class="range-box">
        <div class="range-value"><strong>{{ countN }}</strong> <span>词</span></div>
        <input type="range" class="range" min="1" :max="maxN" v-model.number="countN" />
        <div class="range-ends"><span>1</span><span>{{ maxN }}</span></div>
      </div>
      <div class="actions">
        <button class="primary" :disabled="busy || !pool?.available" @click="createPlan">开始今天的学习</button>
      </div>
      <p class="hint" v-if="pool?.available && pool.available < 100">未掌握池还剩 {{ pool.available }} 个新词，滑轴上限定在这里。</p>
      <p class="hint" v-if="!pool?.available">词库里没有可学的新词了，请联系管理员导入更多考研词。</p>
    </section>

    <section v-else-if="state?.kind === 'learning_done'" class="card done">
      <h2>今天的 {{ state.word_count }} 个词已学完</h2>
      <p>5 轮全部走完，已进入复习池，之后会按遗忘曲线（1/2/4/7/15/30 天）自动安排复习。</p>
      <p class="muted">现在就可以看今天的短文了。</p>
      <div class="actions">
        <button class="primary" @click="goTab('articles')">去看每日短文</button>
        <button class="ghost" @click="goTab('review')">去复习板块</button>
      </div>
    </section>

    <section v-else-if="state?.kind?.startsWith('r')" class="card quiz">
      <div class="quiz-head">
        <span class="round">{{ currentRound }}</span>
        <span class="muted">第 {{ state.cycle_no }} / {{ state.cycle_count }} 组（本组 {{ state.cycle_size }} 词）</span>
        <button class="known-btn" :disabled="busy" @click="markKnown">熟知 · 跳过</button>
      </div>
      <div class="bar"><div :style="{ width: progressPct + '%' }"></div></div>
      <p class="muted small">已完成 {{ state.word_done }} / {{ state.word_total }} 词</p>

      <div v-if="correctFlash" class="correct-flash">✓ 默写正确，做得好！</div>

      <template v-if="state.kind === 'r1'">
        <div class="word-big">{{ state.word }}</div>
        <div class="reveal" v-if="revealed">
          <span class="phonetic">{{ state.phonetic || '' }}</span>
          <span>{{ state.pos || '' }} {{ state.translation }}</span>
          <button class="ghost" @click="playWord(state.speak_text)">播放读音</button>
        </div>
        <div class="actions">
          <button v-if="!revealed" :disabled="busy" @click="submit({ action: 'unknown' })">陌生</button>
          <button class="primary" :disabled="busy" @click="submit({ action: 'known' })">认识</button>
        </div>
        <p class="hint" v-if="!revealed">只显示英文。点「陌生」才展开读音和含义，再点「认识」才过。</p>
      </template>

      <template v-else-if="state.kind === 'r2'">
        <p class="prompt">听读音，拼写这个单词</p>
        <button class="ghost speak" @click="playWord(state.speak_text)">播放读音（可随时重播）</button>
        <input v-model="input" @input="onInput" @keyup.enter="submit({ action: 'spell', value: input })"
               autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="在这里拼写英文" />
        <div class="actions"><button class="primary" :disabled="busy" @click="submit({ action: 'spell', value: input })">提交</button></div>
        <p class="answer" v-if="shownAnswer">正确单词：{{ shownAnswer }}</p>
      </template>

      <template v-else-if="state.kind === 'r3'">
        <p class="prompt">看汉语释义，默写英文</p>
        <div class="word-cn">{{ state.translation }} <small>{{ state.pos || '' }}</small></div>
        <button class="ghost speak" @click="playWord(state.speak_text)">播放读音</button>
        <input v-model="input" @input="onInput" @keyup.enter="submit({ action: 'spell', value: input })"
               autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="默写英文单词" />
        <div class="actions"><button class="primary" :disabled="busy" @click="submit({ action: 'spell', value: input })">提交</button></div>
        <p class="answer" v-if="shownAnswer">正确单词：{{ shownAnswer }}</p>
      </template>

      <template v-else-if="state.kind === 'r4'">
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
  </main>
</template>

<style scoped>
.range-box { margin-top: 20px; }
.range-value { text-align: center; font-size: 15px; color: var(--muted); }
.range-value strong { font-size: 44px; font-weight: 800; color: var(--ink); margin-right: 6px; }
.range-value span { color: var(--muted); }
.range {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 6px;
  border-radius: 6px;
  background: var(--line-strong);
  border: none;
  padding: 0;
  margin: 18px 0 8px;
  cursor: pointer;
}
.range:focus { box-shadow: none; border: none; }
.range::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--primary-grad);
  border: 2px solid #fff;
  box-shadow: 0 2px 8px rgba(79, 94, 240, .35);
  cursor: grab;
}
.range::-moz-range-thumb {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--primary);
  border: 2px solid #fff;
  cursor: grab;
}
.range-ends {
  display: flex;
  justify-content: space-between;
  font-size: 12.5px;
  color: var(--muted);
}
</style>
