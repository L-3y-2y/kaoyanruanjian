<script setup>
import { nextTick, ref } from 'vue'
import { api } from '../api.js'

const props = defineProps({
  context: { type: Object, default: null }   // {source_type, source_id, title}
})
const emit = defineEmits(['close'])

const sessionId = ref(null)
const title = ref(props.context?.title || '通用英语')
const messages = ref([])
const input = ref('')
const busy = ref(false)
const error = ref('')
const maxTurns = ref(8)
const scroller = ref(null)

async function scrollDown() {
  await nextTick()
  if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
}

async function send() {
  const text = input.value.trim()
  if (!text || busy.value) return
  busy.value = true
  error.value = ''
  input.value = ''
  try {
    if (!sessionId.value) {
      const r = await api('/api/chat/start', {
        method: 'POST',
        body: JSON.stringify({
          source_type: props.context?.source_type || 'general',
          source_id: props.context?.source_id || null,
          first_question: text
        })
      })
      sessionId.value = r.session_id
      title.value = r.title || title.value
      messages.value = [
        { role: 'user', content: text },
        { role: 'assistant', content: r.reply?.content || '' }
      ]
    } else {
      messages.value.push({ role: 'user', content: text })
      const r = await api('/api/chat/ask', {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId.value, question: text })
      })
      messages.value.push({ role: 'assistant', content: r.content })
      if (r.turn_count >= maxTurns.value) error.value = `已达 ${maxTurns.value} 轮上限，可以关闭后开始新的答疑。`
    }
    await scrollDown()
  } catch (e) {
    error.value = e.message
    // If the very first turn failed, allow retry by keeping the input.
    if (!sessionId.value) input.value = text
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <section class="card chat-panel">
      <div class="article-head">
        <div>
          <h2>英语 AI · {{ title }}</h2>
          <p class="muted small">中翻英、英翻中、查单词、问语法、改作文都可以；从短文或题目进来时，我会优先结合那段内容。</p>
        </div>
        <button class="ghost tiny" @click="emit('close')">关闭</button>
      </div>

      <div class="chat-log" ref="scroller">
        <div v-if="!messages.length" class="chat-empty muted">
          中文英文都可以问。比如「『坚持』英语怎么说」「abandon 是什么意思」「这句语法怎么分析」「为什么选 B」。
        </div>
        <div v-for="(m, i) in messages" :key="i" class="chat-msg" :class="m.role">
          <span class="chat-who">{{ m.role === 'user' ? '你' : 'AI 老师' }}</span>
          <div class="chat-bubble">{{ m.content }}</div>
        </div>
        <div v-if="busy" class="chat-msg assistant">
          <span class="chat-who">AI 老师</span>
          <div class="chat-bubble muted">正在思考…</div>
        </div>
      </div>

      <p class="error" v-if="error">{{ error }}</p>
      <div class="chat-input">
        <input v-model="input" :disabled="busy" placeholder="中翻英 / 英翻中 / 查词 / 问语法…"
               @keyup.enter="send" />
        <button class="primary" :disabled="busy || !input.trim()" @click="send">发送</button>
      </div>
      <p class="hint">这是对练习，不写入你的学习进度，也不计入五轮状态。随时可关闭。</p>
    </section>
  </div>
</template>
