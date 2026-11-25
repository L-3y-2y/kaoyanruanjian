<script setup>
import { onMounted, ref } from 'vue'
import { api, playWord } from '../api.js'
import { openChat } from '../store.js'

const batches = ref([])
const scenes = ref([])
const selectedScene = ref('default')
const activePlanId = ref(null)
const data = ref(null)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const activeWord = ref(null)
const activeWordArticle = ref(null)
const cloze = ref({})
// Reveal is per-view only: click to mark, refresh/leave to reset.
const shown = ref({})

async function loadBatches() {
  batches.value = (await api('/api/articles/batches')).batches
  scenes.value = (await api('/api/articles/scenes')).scenes
}

async function openBatch(planId) {
  loading.value = true
  error.value = ''
  activeWord.value = null
  try {
    activePlanId.value = planId
    shown.value = {}
    cloze.value = {}
    data.value = await api(`/api/articles/daily?plan_id=${planId}&scene=${encodeURIComponent(selectedScene.value)}`)
  } catch (e) { error.value = e.message; data.value = null }
  finally { loading.value = false }
}

async function reveal(article) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const r = await api('/api/articles/reveal', {
      method: 'POST', body: JSON.stringify({ article_id: article.id })
    })
    article.segments = r.segments
    shown.value[article.id] = true
    ensureCloze(article)
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

async function regenerate(article) {
  if (busy.value) return
  if (!window.confirm('这篇短文会用大模型重新生成，正文和词义快照都会换成新版本。确定继续？')) return
  busy.value = true
  error.value = ''
  try {
    const r = await api('/api/articles/regenerate', {
      method: 'POST', body: JSON.stringify({ article_id: article.id })
    })
    article.title = r.title || article.title
    article.generator = r.generator
    delete shown.value[article.id]
    delete cloze.value[article.id]
    const fresh = await api(`/api/articles/daily?plan_id=${activePlanId.value}&scene=${encodeURIComponent(selectedScene.value)}`)
    const match = fresh.articles.find(x => x.id === article.id)
    if (match) { article.body = match.body; article.marked_count = match.marked_count }
    activeWord.value = null
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

function hide(article) {
  delete shown.value[article.id]
  activeWord.value = null
}

function cleanMeaning(text) {
  return String(text || '').split(';')[0].split('，')[0].trim()
}

function ensureCloze(article) {
  if (!shown.value[article.id] || cloze.value[article.id]) return
  const segs = article.segments || []
  const marked = segs.map((s, i) => ({ ...s, i })).filter(s => s.word && s.word.is_target)
  if (!marked.length) return
  const pool = [...new Set(marked.map(s => cleanMeaning(s.word.translation)).filter(Boolean))]
  const items = marked.slice(0, Math.min(5, marked.length)).map((m, idx) => {
    const full = segs.map((s, i) => s.word ? `{{${i}}}` : s.text).join('')
    const token = `{{${m.i}}}`
    const pos = full.indexOf(token)
    const starts = [full.lastIndexOf('.', pos), full.lastIndexOf('!', pos), full.lastIndexOf('?', pos)]
    const start = Math.max(0, ...starts.map(x => x + 1))
    const ends = [full.indexOf('.', pos), full.indexOf('!', pos), full.indexOf('?', pos)].filter(x => x >= 0)
    const end = ends.length ? Math.min(...ends) + 1 : full.length
    const sentence = full.slice(start, end).replace(token, '______').trim()
    const right = cleanMeaning(m.word.translation) || m.word.surface
    const others = pool.filter(x => x && x !== right)
    const opts = [right, ...others.slice(0, 3)]
    while (opts.length < 4) opts.push('（暂无干扰项）')
    const shift = (idx + m.i) % 4
    const ordered = opts.slice(shift).concat(opts.slice(0, shift))
    return { sentence, options: ordered, answer: right, picked: null, checked: false }
  })
  cloze.value[article.id] = { items, checked: false }
}

function pick(item, opt) {
  if (item.checked) return
  item.picked = opt
}

function checkCloze(article) {
  const c = cloze.value[article.id]
  if (!c) return
  c.items.forEach(i => { i.checked = true })
  c.checked = true
}

function clozeScore(article) {
  const c = cloze.value[article.id]
  return c ? c.items.filter(i => i.picked === i.answer).length : 0
}

function sceneLabel(id) {
  return scenes.value.find(s => s.id === id)?.label || id
}

function back() {
  activePlanId.value = null
  data.value = null
  activeWord.value = null
  shown.value = {}
  cloze.value = {}
}

onMounted(async () => {
  loading.value = true
  try { await loadBatches() } catch (e) { error.value = e.message }
  finally { loading.value = false }
})
</script>

<template>
  <main>
    <p class="error" v-if="error">{{ error }}</p>

    <section v-if="loading" class="card muted">正在读取…</section>

    <template v-else-if="!activePlanId">
      <section class="card">
        <h2>每日短文</h2>
        <p class="muted">学完当天批次的 5 轮后即可看短文；按批次永久保存，重复进入看到的是同一组。</p>
        <p class="muted small">选一个场景再点「阅读短文」，大模型会按这个场景写。已经生成过的批次不会重写。</p>
        <div class="scene-picker">
          <label v-for="s in scenes" :key="s.id">
            <input type="radio" :value="s.id" v-model="selectedScene" /> {{ s.label }}
          </label>
        </div>
        <div v-if="!batches.length" class="empty">还没有学完 5 轮的批次。先去「学习」把新词学完。</div>
        <div class="batch" v-for="b in batches" :key="b.plan_id">
          <div>
            <strong>{{ b.study_date }}</strong>
            <span class="stage mastered">已学完</span>
            <p class="muted small">{{ b.word_count }} 词 · {{ b.article_count ? '已生成 ' + b.article_count + ' 篇' : '尚未生成' }}</p>
          </div>
          <div class="batch-actions">
            <button class="primary" @click="openBatch(b.plan_id)">
              {{ b.article_count ? '阅读短文' : '生成短文' }}
            </button>
          </div>
        </div>
      </section>
    </template>

    <template v-else-if="data">
      <button class="ghost back" @click="back">← 返回批次列表</button>

      <div class="notice" :class="{ warn: data.mode === 'sample' }">
        {{ data.summary }}
      </div>

      <section class="card article-card" v-for="a in data.articles" :key="a.id">
        <div class="article-head">
          <h2>{{ a.title }}</h2>
          <span class="chip">{{ sceneLabel(a.scene) }} · {{ a.mode === 'full' ? '全放' : '抽词' }} · 已标注 {{ a.marked_count }} 处</span>
        </div>

        <div v-if="!shown[a.id]" class="prose">{{ a.body }}</div>
        <div v-else class="prose"><template v-for="(seg, i) in a.segments" :key="i"><button
          v-if="seg.word" class="kw" :class="seg.word.is_target ? 'target' : 'other'"
          @click="activeWord = seg.word; activeWordArticle = a.id">{{ seg.text }}</button><span
          v-else>{{ seg.text }}</span></template></div>

        <div class="article-foot">
          <button v-if="!shown[a.id]" class="primary" :disabled="busy" @click="reveal(a)">
            显示全部单词释义（今日词高亮）
          </button>
          <button v-else class="ghost" :disabled="busy" @click="hide(a)">隐藏 · 回到纯文本</button>
          <button class="ghost" :disabled="busy" @click="regenerate(a)">用大模型重新生成</button>
          <button class="ghost" @click="openChat({ source_type: 'article', source_id: a.id, title: a.title })">问 AI · 这篇文章</button>
          <span class="muted small">生成方式：{{ a.generator }}</span>
        </div>

        <div v-if="shown[a.id] && cloze[a.id]" class="cloze">
          <h3>语境小测 · 选词义</h3>
          <p class="muted small">从同一篇文章的词里挑干扰项，先选再核对，答错会显示释义。</p>
          <div v-for="(q, qi) in cloze[a.id].items" :key="qi" class="question">
            <p class="q-stem">{{ qi + 1 }}. {{ q.sentence }}</p>
            <div class="options">
              <label v-for="(opt, oi) in q.options" :key="oi"
                     :class="{ picked: q.picked === opt, locked: q.checked,
                               right: q.checked && opt === q.answer,
                               wrong: q.checked && q.picked === opt && opt !== q.answer }"
                     @click="pick(q, opt)">{{ opt }}</label>
            </div>
            <p v-if="q.checked" class="answer small">正确答案：{{ q.answer }}</p>
          </div>
          <div class="actions">
            <button v-if="!cloze[a.id].checked" class="primary" @click="checkCloze(a)">核对答案</button>
            <span v-else class="chip">答对 {{ clozeScore(a) }} / {{ cloze[a.id].items.length }}</span>
          </div>
        </div>

        <div v-if="a.uncovered?.length" class="uncovered">
          <strong>本次未收录词（{{ a.uncovered.length }}）</strong>
          <p class="muted small">这些词没有编进本组短文，可在复习板块单独复习；抽词会轮转，长期每个词都会被抽到。</p>
          <div class="chips"><span v-for="w in a.uncovered" :key="w">{{ w }}</span></div>
        </div>
      </section>

      <div v-if="activeWord" class="meaning-card">
        <button class="close" @click="activeWord = null">×</button>
        <div class="meaning-title">{{ activeWord.surface }} · {{ activeWord.translation || '（该词暂无释义快照）' }}</div>
        <p class="muted small">{{ activeWord.pos }} {{ activeWord.phonetic }}</p>
        <button class="ghost" @click="playWord(activeWord.surface)">播放读音</button>
        <button class="ghost" @click="openChat({ source_type: 'article', source_id: activeWordArticle, title: '词义追问 · ' + activeWord.surface })">问 AI · 追问这个词</button>
        <p class="hint">{{ activeWord.is_target ? '这是今日所学词的释义快照。' : '该释义来自内置词典。' }}</p>
      </div>
    </template>
  </main>
</template>
