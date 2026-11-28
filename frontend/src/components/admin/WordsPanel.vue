<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api.js'

const words = ref([])
const total = ref(0)
const q = ref('')
const loading = ref(false)
const error = ref('')
const notice = ref('')

const editId = ref(null)       // 正在行内编辑的词 id
const draft = ref({})          // 编辑草稿
const adding = ref(false)      // 新增表单是否展开
const fresh = ref({ word: '', phonetic: '', translation: '', pos: '', tag: 'ky' })
const busy = ref(false)

const TAGS = [
  { v: 'ky', t: '考研' }, { v: 'cet4', t: '四级' },
  { v: 'cet6', t: '六级' }, { v: 'gk', t: '高考' },
]

const shown = computed(() => words.value.length)

// 一个词可能挂多个考试标签（cet6 toefl gre ...），全展开会把行撑成两行。
function tagText(t) {
  if (!t) return '—'
  const list = String(t).trim().split(/\s+/)
  return list.length <= 2 ? list.join(' ') : list.slice(0, 2).join(' ') + ' +' + (list.length - 2)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const r = await api(`/api/admin/words?q=${encodeURIComponent(q.value.trim())}&limit=40`)
    words.value = r.words
    total.value = r.total
  } catch (e) { error.value = e.message }
  finally { loading.value = false }
}

function startEdit(w) {
  adding.value = false
  editId.value = w.id
  draft.value = {
    word: w.word, phonetic: w.phonetic || '', translation: w.translation || '',
    pos: w.pos || '', tag: w.tag || 'ky',
  }
  notice.value = ''
}

function cancel() {
  editId.value = null
  draft.value = {}
}

async function saveEdit() {
  if (busy.value) return
  if (!draft.value.translation?.trim()) { error.value = '释义不能为空'; return }
  busy.value = true
  error.value = ''
  try {
    await api('/api/admin/word', { method: 'POST', body: JSON.stringify(draft.value) })
    notice.value = `已保存：${draft.value.word}`
    editId.value = null
    await load()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

async function saveNew() {
  if (busy.value) return
  if (!fresh.value.word.trim() || !fresh.value.translation.trim()) {
    error.value = '单词与释义都不能为空'
    return
  }
  busy.value = true
  error.value = ''
  try {
    await api('/api/admin/word', { method: 'POST', body: JSON.stringify(fresh.value) })
    notice.value = `已新增：${fresh.value.word}`
    fresh.value = { word: '', phonetic: '', translation: '', pos: '', tag: 'ky' }
    adding.value = false
    q.value = ''
    await load()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

onMounted(load)
</script>

<template>
  <div>
    <p class="error" v-if="error">{{ error }}</p>
    <p class="adm-note" v-if="notice" style="margin:0 0 10px;color:var(--primary-deep)">{{ notice }}</p>

    <section class="adm-card">
      <div class="adm-bar">
        <input v-model="q" class="adm-input" style="width:280px"
               placeholder="拼写或中文释义，回车搜索"
               @keydown.enter="load" />
        <button class="adm-btn" @click="load">搜索</button>
        <button class="adm-btn ghost" v-if="q" @click="q = ''; load()">清除</button>
        <span class="lbl" style="margin-left:auto">
          {{ q ? `匹配 ${shown} 条` : `最近 ${shown} 条` }} · 全库 {{ total }} 词
        </span>
        <button class="adm-btn solid" @click="adding = !adding; editId = null">
          {{ adding ? '收起' : '新增词条' }}
        </button>
      </div>

      <!-- 新增：默认收起，因为管理端最常用的动作是「改一条错的」 -->
      <div v-if="adding" class="adm-pad" style="border-bottom:1px solid var(--line);background:var(--surface)">
        <div class="adm-edit-grid">
          <input v-model="fresh.word" class="adm-input" placeholder="单词（必填）" />
          <input v-model="fresh.phonetic" class="adm-input" placeholder="音标" />
          <input v-model="fresh.translation" class="adm-input" placeholder="中文释义（必填，多个义项用分号）" />
          <select v-model="fresh.tag" class="adm-input">
            <option v-for="t in TAGS" :key="t.v" :value="t.v">{{ t.t }}</option>
          </select>
          <div class="adm-edit-actions">
            <button class="adm-btn solid" :disabled="busy" @click="saveNew">保存</button>
          </div>
        </div>
      </div>

      <p class="adm-empty" v-if="loading && !words.length">正在读取…</p>
      <p class="adm-empty" v-else-if="!words.length">没有匹配的词条。换个拼写或释义再试。</p>

      <div class="adm-table-wrap" v-else>
        <table class="adm-table">
          <thead>
            <tr>
              <th style="width:150px">词</th>
              <th style="width:150px">音标</th>
              <th>释义</th>
              <th style="width:60px">词性</th>
              <th style="width:104px">标签</th>
              <th style="width:90px">来源</th>
              <th style="width:80px"></th>
            </tr>
          </thead>
          <tbody>
            <template v-for="w in words" :key="w.id">
              <tr v-if="editId !== w.id">
                <td class="w">{{ w.word }}</td>
                <td class="dim">{{ w.phonetic || '—' }}</td>
                <td>{{ w.translation || '（无释义）' }}</td>
                <td class="dim">{{ w.pos || '—' }}</td>
                <td class="dim" :title="w.tag || ''">{{ tagText(w.tag) }}</td>
                <td><span class="adm-pill">{{ w.source || 'unknown' }}</span></td>
                <td><button class="adm-link" @click="startEdit(w)">编辑</button></td>
              </tr>

              <tr v-else class="adm-edit-row">
                <td colspan="7">
                  <div class="adm-edit-grid">
                    <input class="adm-input" :value="draft.word" disabled />
                    <input v-model="draft.phonetic" class="adm-input" placeholder="音标" />
                    <input v-model="draft.translation" class="adm-input" placeholder="中文释义" />
                    <input v-model="draft.tag" class="adm-input" placeholder="标签（ky / cet4 cet6）" />
                    <div class="adm-edit-actions">
                      <button class="adm-btn ghost" @click="cancel">取消</button>
                      <button class="adm-btn solid" :disabled="busy" @click="saveEdit">保存</button>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </section>

    <p class="adm-note">
      保存会覆盖同名词条并标记「已核对」，不影响任何已有的学习进度与短文快照。
      标签是空格分隔的考试标签（如 <code>cet4 cet6</code>），决定这个词会出现在哪几本书里；
      改词形请用「新增词条」，它按拼写匹配，不是在原行上改名。
    </p>
  </div>
</template>
