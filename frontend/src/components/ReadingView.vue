<script setup>
import { inject, onMounted, reactive, ref } from 'vue'
import { api } from '../api.js'

const openChat = inject('openChat', () => {})

const weeks = ref([])
const weekKey = ref('')
const data = ref(null)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const selected = reactive({})
const subjective = reactive({})

async function loadWeeks() {
  const r = await api('/api/reading/weeks')
  weeks.value = r.weeks
  if (!weekKey.value) weekKey.value = r.current
}

async function loadReading() {
  if (!weekKey.value) return
  loading.value = true
  error.value = ''
  try {
    data.value = await api(`/api/reading?week_key=${weekKey.value}`)
  } catch (e) { error.value = e.message; data.value = null }
  finally { loading.value = false }
}

async function publishNow() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await api('/api/reading/publish-now', { method: 'POST' })
    await loadReading()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

async function submit(q) {
  if (busy.value) return
  const value = q.ai_graded ? (subjective[q.id] || '') : (selected[q.id] || '')
  if (!value.trim()) { error.value = '请先作答'; return }
  busy.value = true
  error.value = ''
  try {
    await api('/api/reading/answer', {
      method: 'POST',
      body: JSON.stringify({ question_id: q.id, week_key: weekKey.value, value })
    })
    await loadReading()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

function reasonOf(q) {
  if (!q.ai_result) return ''
  try { return JSON.parse(q.ai_result).reason || q.ai_result }
  catch { return q.ai_result }
}

const qtypeLabel = {
  main_idea: '主旨题', detail: '细节题', vocabulary: '词义猜测',
  inference: '推理题', subjective: '简答题 · AI 判分'
}

onMounted(async () => {
  try { await loadWeeks() } catch (e) { error.value = e.message }
  await loadReading()
})
</script>

<template>
  <main>
    <p class="error" v-if="error">{{ error }}</p>

    <section class="card" v-if="weeks.length">
      <div class="week-row">
        <h2>每周阅读理解</h2>
        <select v-model="weekKey" @change="loadReading">
          <option v-for="w in weeks" :key="w" :value="w">{{ w }}</option>
        </select>
      </div>
      <p class="muted small">固定 2 篇、每周五 18:00 发布、周日晚截止。题目、答卷与成绩按「账号 + 周次」保存，逾期可补做但不计成绩。</p>
    </section>

    <section v-if="loading" class="card muted">正在读取本周阅读…</section>

    <template v-else-if="data">
      <section v-if="data.status === 'not_published'" class="card done">
        <h2>本周阅读尚未发布</h2>
        <p>发布时间：{{ data.publish_at }}（周五 18:00）；作答截止：{{ data.deadline }}。</p>
        <p class="muted">后端定时任务会在发布时间到达后自动生成，打开这个页面也会兜底补生成。</p>
        <button class="primary" :disabled="busy" @click="publishNow">提前生成本周（试用）</button>
      </section>

      <template v-else>
        <div class="notice" :class="{ warn: data.late }">
          <span v-if="data.late">已过截止时间，现在可以补做，但本次不计入成绩。</span>
          <span v-else>发布 {{ data.publish_at }} · 截止 {{ data.deadline }}</span>
          <span class="score-chip">已答 {{ data.summary.answered }}/{{ data.summary.total }} · 客观题正确 {{ data.summary.correct }} · 得分 {{ data.summary.score }}</span>
        </div>

        <section class="card article-card" v-for="a in data.articles" :key="a.id">
          <div class="article-head">
            <h2>{{ a.title }}</h2>
            <div>
              <button class="ghost tiny" @click="openChat({ source_type: 'article', source_id: a.id, title: a.title })">问 AI · 这篇文章</button>
              <span class="chip">{{ a.mode === 'full' ? '全放' : '抽词' }}</span>
            </div>
          </div>
          <div class="prose">{{ a.body }}</div>
          <div v-if="a.uncovered?.length" class="uncovered">
            <strong>本周未收录词（{{ a.uncovered.length }}）</strong>
            <div class="chips"><span v-for="w in a.uncovered" :key="w">{{ w }}</span></div>
          </div>

          <div class="question" v-for="q in a.questions" :key="q.id">
            <div class="q-head">
              <span class="chip">{{ qtypeLabel[q.qtype] }}</span>
              <span class="muted small">第 {{ q.sort_no }} 题</span>
              <button class="ghost tiny" @click="openChat({ source_type: 'question', source_id: q.id, title: '第 ' + q.sort_no + ' 题答疑' })">问 AI</button>
            </div>
            <p class="q-stem">{{ q.stem }}</p>

            <template v-if="q.options">
              <div class="options">
                <label v-for="(text, key) in q.options" :key="key"
                       :class="{ picked: selected[q.id] === key, locked: q.answered }">
                  <input type="radio" :name="'q' + q.id" :value="key"
                         v-model="selected[q.id]" :disabled="q.answered || busy" />
                  <strong>{{ key }}</strong> {{ text }}
                </label>
              </div>
            </template>
            <textarea v-else v-model="subjective[q.id]" :disabled="q.answered || busy"
                      placeholder="用中文作答，提交后由 AI 判分并给出理由"></textarea>

            <div v-if="!q.answered" class="actions">
              <button class="primary" :disabled="busy" @click="submit(q)">提交答案</button>
            </div>
            <div v-else class="result" :class="{ wrong: q.correct === 0 }">
              <strong>{{ q.correct === 1 ? '回答正确' : '回答错误' }}</strong>
              <span> · 得分 {{ q.score }}</span>
              <span v-if="q.scored === 0">（不计成绩）</span>
              <p class="muted small" v-if="q.correct_option">标准答案：{{ q.correct_option }}</p>
              <p class="muted small" v-if="q.standard_answer && q.ai_graded">参考答案要点：{{ q.standard_answer }}</p>
              <p class="muted small" v-if="reasonOf(q)">判分理由：{{ reasonOf(q) }}</p>
              <p class="muted small">你的答案：{{ q.user_answer }}</p>
            </div>
          </div>
        </section>
      </template>
    </template>
  </main>
</template>
