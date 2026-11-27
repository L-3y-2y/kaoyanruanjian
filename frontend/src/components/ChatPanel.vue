<script setup>
import { nextTick, ref } from 'vue'
import { api } from '../api.js'
import { store } from '../store.js'
import PracticeCard from './PracticeCard.vue'

const CARD_MARKER = '[[PRACTICE_CARD]]'

function parseCard(content) {
  if (typeof content !== 'string' || !content.startsWith(CARD_MARKER)) return null
  try { return JSON.parse(content.slice(CARD_MARKER.length)) } catch { return null }
}

function escapeHtml(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}
// 轻量 Markdown 渲染：把模型偶尔输出的 **加粗** 等符号转成正常排版，避免出现「星 星」。
function renderMarkdown(s) {
  let t = escapeHtml(s)
  t = t.replace(/`([^`]+)`/g, '<code>$1</code>')
  t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  t = t.replace(/\*([^*]+)\*/g, '<em>$1</em>')
  t = t.replace(/\n/g, '<br>')
  return t
}

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
  // 立即显示用户消息，避免等待 AI 回复期间看不到自己发的问题。
  messages.value.push({ role: 'user', content: text })
  await scrollDown()
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
      messages.value.push({ role: 'assistant', content: r.reply?.content || '' })
    } else {
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
    // 首条失败时移除乐观消息、恢复输入，允许重试。
    if (!sessionId.value) {
      messages.value.pop()
      input.value = text
    }
  } finally {
    busy.value = false
  }
}

async function quickAsk(text) {
  if (busy.value) return
  input.value = text
  await send()
}

async function generateCard(kind) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  const label = kind === 'cloze' ? '选词填空' : '阅读理解'
  try {
    const body = {
      kind,
      source_type: props.context?.source_type || 'general',
      source_id: props.context?.source_id || null
    }
    if (sessionId.value) body.session_id = sessionId.value
    const r = await api('/api/chat/practice', {
      method: 'POST',
      body: JSON.stringify(body)
    })
    sessionId.value = r.session_id
    title.value = r.title || title.value
    if (!messages.value.length) messages.value.push({ role: 'user', content: `出一篇考研风格${label}题` })
    else messages.value.push({ role: 'user', content: `再来一篇${label}` })
    messages.value.push({ role: 'assistant', content: '', card: r.card })
    if (r.turn_count >= maxTurns.value) error.value = `已达 ${maxTurns.value} 轮上限，可以关闭后开始新的答疑。`
    await scrollDown()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

function askAbout(text) {
  input.value = text
  document.querySelector('.chat-input input')?.focus()
}
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <section class="card chat-panel">
      <button class="chat-close" @click="emit('close')" aria-label="关闭">✕</button>
      <div class="article-head">
        <div>
          <h2>英语 AI · {{ title }}<span class="book-tag">{{ store.currentBook?.name }}</span></h2>
          <p class="muted small">中翻英、英翻中、查词、问语法、自由聊天都可以；点「交互」按钮用今天学的词出考研题，直接点选作答、即时判分看解析。从短文或题目进来时会优先结合那段内容。</p>
        </div>
      </div>

      <div class="chat-log" ref="scroller">
        <div v-if="!messages.length" class="chat-empty muted">
          中文英文都可以聊。比如「『坚持』英语怎么说」「abandon 是什么意思」「这句语法怎么分析」。想刷题就点下面的「选词填空 · 交互」或「阅读理解 · 交互」。
        </div>
        <div v-for="(m, i) in messages" :key="i" class="chat-msg" :class="m.role">
          <span class="chat-who">{{ m.role === 'user' ? '你' : 'AI 老师' }}</span>
          <PracticeCard v-if="m.card || parseCard(m.content)" :card="m.card || parseCard(m.content)" @ask="askAbout" />
          <div v-else class="chat-bubble" v-html="renderMarkdown(m.content)"></div>
        </div>
        <div v-if="busy" class="chat-msg assistant">
          <span class="chat-who">AI 老师</span>
          <div class="chat-bubble muted">正在思考…</div>
        </div>
      </div>

      <p class="error" v-if="error">{{ error }}</p>
      <div class="chat-quick">
        <button type="button" :disabled="busy" @click="generateCard('cloze')">选词填空 · 交互</button>
        <button type="button" :disabled="busy" @click="generateCard('reading')">阅读理解 · 交互</button>
        <button type="button" :disabled="busy" @click="quickAsk('出一道考研汉译英练习：先只给一句中文，等我翻译成英文后再给参考译文并批改')">汉译英练习</button>
        <button type="button" :disabled="busy" @click="quickAsk('出一道考研英译汉练习：先只给一句英文，等我翻译成中文后再给参考译文并批改')">英译汉练习</button>
        <button type="button" :disabled="busy" @click="quickAsk('出一道考研英语大作文题目（图画/图表，160-200 词），只给题目和提纲，不要范文；等我写完再批改')">大作文</button>
        <button type="button" :disabled="busy" @click="quickAsk('出一道考研英语小作文题目（书信/通知，约 100 词），只给题目要求；等我写完再批改')">小作文</button>
      </div>
      <div class="chat-input">
        <input v-model="input" :disabled="busy" placeholder="中翻英 / 英翻中 / 查词 / 问语法…"
               @keyup.enter="send" />
        <button class="primary" :disabled="busy || !input.trim()" @click="send">发送</button>
      </div>
      <p class="hint">这是对练习，不写入你的学习进度，也不计入五轮状态。随时可关闭。</p>
    </section>
  </div>
</template>
