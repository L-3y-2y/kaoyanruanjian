<script setup>
import { computed, reactive, ref } from 'vue'

const props = defineProps({
  card: { type: Object, required: true }
})
const emit = defineEmits(['ask'])

const answers = reactive({})      // blank no / question no -> chosen letter
const submitted = ref(false)
const expanded = ref(false)

const isCloze = computed(() => props.card.kind === 'cloze')
const items = computed(() => isCloze.value ? props.card.blanks : props.card.questions)
const options = computed(() => isCloze.value ? props.card.options : null)
const bankLetters = computed(() => Object.keys(options.value || {}))

// Split passage into text and blank slots for inline dropdowns.
const passageParts = computed(() => {
  if (!isCloze.value) return []
  const parts = []
  const re = /__(\d+)__/g
  let last = 0
  let m
  while ((m = re.exec(props.card.passage)) !== null) {
    if (m.index > last) parts.push({ text: props.card.passage.slice(last, m.index) })
    parts.push({ blank: Number(m[1]) })
    last = m.index + m[0].length
  }
  if (last < props.card.passage.length) parts.push({ text: props.card.passage.slice(last) })
  return parts
})

function choice(no) {
  return answers[no] || ''
}
function isCorrect(item) {
  return String(answers[item.no] || '').toUpperCase() === String(item.answer || '').toUpperCase()
}
const answeredCount = computed(() => items.value.filter(i => answers[i.no]).length)
const correctCount = computed(() => submitted.value ? items.value.filter(isCorrect).length : 0)

function submit() {
  if (answeredCount.value < items.value.length) return
  submitted.value = true
  expanded.value = true
}
function retry() {
  for (const k of Object.keys(answers)) delete answers[k]
  submitted.value = false
  expanded.value = false
}
function askAbout(item) {
  const label = isCloze.value ? `第 ${item.no} 空` : `第 ${item.no} 题`
  emit('ask', `这道「${props.card.title}」的${label}，正确答案是 ${item.answer}。${item.explanation} 请用英文举例再讲解一下这个考点。`)
}
</script>

<template>
  <div class="practice-card">
    <div class="pc-head">
      <strong>{{ card.title }}</strong>
      <span class="chip">{{ isCloze ? '选词填空 · 10 空' : '阅读理解 · 5 题' }}</span>
    </div>

    <div v-if="isCloze" class="pc-bank">
      <span v-for="k in bankLetters" :key="k"><b>{{ k }}</b> {{ options[k] }}</span>
    </div>

    <p class="pc-passage">
      <template v-if="isCloze">
        <template v-for="(p, i) in passageParts" :key="i"><span>{{ p.text }}</span><select
          v-if="p.blank" v-model="answers[p.blank]" :disabled="submitted"
          :class="{ ok: submitted && answers[p.blank] === items.find(x => x.no === p.blank)?.answer,
                    bad: submitted && answers[p.blank] && answers[p.blank] !== items.find(x => x.no === p.blank)?.answer }">
          <option value="">—</option>
          <option v-for="k in bankLetters" :key="k" :value="k">{{ k }}</option>
        </select></template>
      </template>
      <template v-else>{{ card.passage }}</template>
    </p>

    <div v-if="!isCloze" class="pc-questions">
      <div v-for="q in items" :key="q.no" class="pc-q">
        <p class="pc-stem">{{ q.no }}. {{ q.stem }}</p>
        <div class="pc-options">
          <label v-for="(text, k) in q.options" :key="k"
                 :class="{ picked: answers[q.no] === k,
                           ok: submitted && k === q.answer,
                           bad: submitted && answers[q.no] === k && k !== q.answer }">
            <input type="radio" :name="'pc' + q.no" :value="k" v-model="answers[q.no]" :disabled="submitted" />
            <b>{{ k }}</b> {{ text }}
          </label>
        </div>
        <div v-if="submitted" class="pc-explain" :class="{ good: isCorrect(q), wrong: !isCorrect(q) }">
          <span>{{ isCorrect(q) ? '正确' : '错误' }} · 答案 {{ q.answer }}</span>
          <p>{{ q.explanation }}</p>
          <button class="ghost tiny" @click="askAbout(q)">就这题问 AI</button>
        </div>
      </div>
    </div>

    <div v-if="submitted && isCloze" class="pc-answers">
      <div v-for="b in items" :key="b.no" class="pc-explain" :class="{ good: isCorrect(b), wrong: !isCorrect(b) }">
        <span>{{ b.no }}. 你选 {{ answers[b.no] || '—' }} · 答案 {{ b.answer }} · {{ isCorrect(b) ? '正确' : '错误' }}</span>
        <p>{{ b.explanation }}</p>
        <button class="ghost tiny" @click="askAbout(b)">就这空问 AI</button>
      </div>
    </div>

    <div class="pc-actions">
      <template v-if="!submitted">
        <button class="primary" :disabled="answeredCount < items.length" @click="submit">
          提交答案（{{ answeredCount }}/{{ items.length }}）
        </button>
      </template>
      <template v-else>
        <strong class="pc-score">得分 {{ correctCount }} / {{ items.length }}</strong>
        <button class="ghost tiny" @click="retry">再做一次</button>
      </template>
    </div>
  </div>
</template>
