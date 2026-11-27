<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api.js'
import { store, goTab } from '../store.js'

const mode = ref('example') // example | immersion | scene
const items = ref([])
const idx = ref(0)
const answer = ref('')
const feedback = ref(null)
const loading = ref(false)
const error = ref('')
const showMeaning = ref(false)

const current = computed(() => items.value[idx.value] || null)
const modeLabel = computed(() => ({
  example: '微情景例句', immersion: '短文沉浸', scene: '图片联想',
}[mode.value]))

function norm(s) {
  return String(s || '').trim().toLowerCase().replace(/\s+/g, ' ')
}

function masked(sentence, word) {
  const esc = String(word || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return String(sentence || '').replace(new RegExp(`\\b${esc}\\w*\\b`, 'i'), '______')
}

const choices = computed(() => {
  if (!current.value || !items.value.length) return []
  const right = current.value.word
  const others = items.value.filter(x => x.word !== right).map(x => x.word)
  const list = [right, ...others.slice(0, 3)]
  // deterministic shuffle seeded by the word so it stays stable across renders
  const seed = [...right].reduce((a, c) => a + c.charCodeAt(0), 0)
  for (let i = list.length - 1; i > 0; i--) {
    const j = (seed + i * 7) % (i + 1)
    ;[list[i], list[j]] = [list[j], list[i]]
  }
  return list
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const r = await api('/api/scenario/bundle?limit=5')
    items.value = r.items || []
    idx.value = 0
    resetStep()
    if (!items.value.length) error.value = '当前书籍暂时没有可练的词，先去「今日学习」建计划吧。'
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function resetStep() {
  answer.value = ''
  feedback.value = null
  showMeaning.value = false
}

function check() {
  if (!current.value || feedback.value !== null) return
  feedback.value = norm(answer.value) === norm(current.value.word)
}

function next() {
  if (idx.value < items.value.length - 1) idx.value += 1
  else idx.value = 0
  resetStep()
}

watch(mode, () => { resetStep() })
onMounted(load)
</script>

<template>
  <main class="wrap">
    <section class="card">
      <h2>情景背词<span class="book-tag">{{ store.currentBook?.name }}</span></h2>
      <p class="muted">同一个词，三种记忆路径：短句里用一次、画面里记一次、整篇短文里读一次。练习不改动学习进度。</p>
      <div class="mode-tabs">
        <button :class="{ active: mode === 'example' }" @click="mode = 'example'">微情景例句</button>
        <button :class="{ active: mode === 'immersion' }" @click="mode = 'immersion'">短文沉浸</button>
        <button :class="{ active: mode === 'scene' }" @click="mode = 'scene'">图片联想</button>
      </div>
    </section>

    <p class="error" v-if="error">{{ error }}</p>
    <p class="muted" v-if="loading">正在准备情景练习…</p>

    <!-- B · 短文沉浸：复用已有短文页 -->
    <section class="card" v-if="mode === 'immersion'">
      <h3>短文沉浸</h3>
      <p class="muted">今日词全部嵌进一篇情景短文：先纯文本通读，再点「显示全部单词释义」让每个词可点，最后做语境小测。</p>
      <button class="primary" @click="goTab('articles')">去每日短文 →</button>
    </section>

    <!-- A · 微情景例句 -->
    <section class="card" v-if="mode === 'example' && current">
      <p class="muted small">第 {{ idx + 1 }} / {{ items.length }} 词 · 例句填空</p>
      <p class="scenario-sentence">{{ masked(current.example?.sentence, current.word) }}</p>
      <div class="answer-row">
        <input v-model="answer" :disabled="feedback !== null"
               placeholder="填入挖空的单词" @keyup.enter="check" />
        <button class="primary" :disabled="feedback !== null" @click="check">检查</button>
      </div>
      <p v-if="feedback === true" class="ok-note">✓ 填对了，就是「{{ current.word }}」</p>
      <p v-if="feedback === false" class="bad-note">✗ 再想想，正确答案是「{{ current.word }}」</p>
      <button class="ghost tiny" v-if="feedback !== null" @click="showMeaning = !showMeaning">
        {{ showMeaning ? '收起释义' : '看例句翻译' }}
      </button>
      <p v-if="showMeaning" class="muted">{{ current.example?.translation }}</p>
      <div class="scenario-foot">
        <button class="ghost" @click="next">下一个词 →</button>
      </div>
    </section>

    <!-- C · 图片联想 -->
    <section class="card" v-if="mode === 'scene' && current">
      <p class="muted small">第 {{ idx + 1 }} / {{ items.length }} 词 · 看图猜词</p>
      <div class="scene-image">
        <img :src="current.scene?.image_url" :alt="current.scene?.scene_text" />
      </div>
      <p class="scene-desc">{{ current.scene?.scene_text }}</p>
      <div class="choice-grid">
        <button v-for="c in choices" :key="c" :disabled="feedback !== null"
                :class="{ right: feedback !== null && c === current.word,
                          wrong: feedback !== null && c === answer && c !== current.word }"
                @click="answer = c; check()">{{ c }}</button>
      </div>
      <p v-if="feedback === true" class="ok-note">✓ 答对了！「{{ current.word }}」{{ current.translation }}</p>
      <p v-if="feedback === false" class="bad-note">✗ 再看一眼画面，答案是「{{ current.word }}」</p>
      <div class="scenario-foot">
        <button class="ghost" @click="next">下一个词 →</button>
      </div>
    </section>
  </main>
</template>
