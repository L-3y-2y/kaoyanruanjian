<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api.js'
import { store } from '../../store.js'

const rows = ref([])
const summary = ref({ total: 0, stable: 0, wobble: 0, lost: 0 })
const loading = ref(false)
const error = ref('')
const notice = ref('')
const q = ref('')
const filter = ref('all')      // all / stable / wobble / lost
const sortKey = ref('need')    // need(默认：越需要处理越前) / today / mastered / due / errors

const STATE = {
  stable: { label: '稳定', cls: 'ok' },
  wobble: { label: '波动', cls: 'warn' },
  lost:   { label: '掉队', cls: 'bad' },
  never:  { label: '未开始', cls: '' },
}

const FILTERS = computed(() => ([
  { k: 'all', t: '全部', n: summary.value.total },
  { k: 'stable', t: '稳定', n: summary.value.stable },
  { k: 'wobble', t: '波动', n: summary.value.wobble },
  { k: 'lost', t: '掉队', n: summary.value.lost },
]))

const list = computed(() => {
  let out = rows.value
  if (filter.value !== 'all') {
    out = out.filter(r => (filter.value === 'lost'
      ? (r.state === 'lost' || r.state === 'never')
      : r.state === filter.value))
  }
  const k = q.value.trim().toLowerCase()
  if (k) out = out.filter(r => r.username.toLowerCase().includes(k) || r.book.toLowerCase().includes(k))
  if (sortKey.value === 'need') return out
  const pick = {
    today: r => r.today_items,
    mastered: r => r.mastered,
    due: r => r.due,
    errors: r => r.cloze_errors,
  }[sortKey.value]
  return [...out].sort((a, b) => pick(b) - pick(a))
})

const lostNames = computed(() =>
  rows.value.filter(r => r.state === 'lost' || r.state === 'never').map(r => r.username))

function sortBy(k) {
  sortKey.value = sortKey.value === k ? 'need' : k
}

async function copyLost() {
  notice.value = ''
  if (!lostNames.value.length) { notice.value = '没有掉队的学员。'; return }
  const text = lostNames.value.join('、')
  try {
    await navigator.clipboard.writeText(text)
    notice.value = `已复制 ${lostNames.value.length} 个用户名`
  } catch {
    notice.value = `掉队：${text}`
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const r = await api('/api/admin/learners')
    rows.value = r.learners
    summary.value = r.summary
    store.learnersTotal = r.summary.total
  } catch (e) { error.value = e.message }
  finally { loading.value = false }
}

onMounted(load)
</script>

<template>
  <div>
    <p class="error" v-if="error">{{ error }}</p>
    <p class="adm-note" v-if="notice" style="margin:0 0 10px;color:var(--primary-deep)">{{ notice }}</p>

    <section class="adm-card">
      <div class="adm-bar">
        <span class="lbl">状态</span>
        <div class="adm-chips">
          <button v-for="f in FILTERS" :key="f.k" class="adm-chip"
                  :class="{ on: filter === f.k }" @click="filter = f.k">
            {{ f.t }} <b>{{ f.n }}</b>
          </button>
        </div>
        <input v-model="q" class="adm-input" style="width:190px;margin-left:auto" placeholder="搜用户名 / 书籍" />
        <button class="adm-btn" @click="copyLost" :disabled="!lostNames.length">复制掉队名单</button>
      </div>

      <p class="adm-empty" v-if="loading && !rows.length">正在读取…</p>
      <p class="adm-empty" v-else-if="!list.length">没有符合条件的学员。</p>

      <div class="adm-table-wrap" v-else>
        <table class="adm-table wide">
          <thead>
            <tr>
              <th style="width:150px">学员</th>
              <th>书籍</th>
              <th class="num sort" @click="sortBy('today')">今日{{ sortKey === 'today' ? ' ↓' : '' }}</th>
              <th class="num sort" @click="sortBy('mastered')">已掌握{{ sortKey === 'mastered' ? ' ↓' : '' }}</th>
              <th class="num sort" @click="sortBy('due')">积压{{ sortKey === 'due' ? ' ↓' : '' }}</th>
              <th class="num sort" @click="sortBy('errors')">错题{{ sortKey === 'errors' ? ' ↓' : '' }}</th>
              <th>最近学习</th>
              <th style="width:72px">状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in list" :key="r.id">
              <td><span class="w">{{ r.username }}</span><span class="adm-uid">#{{ r.id }}</span></td>
              <td class="dim">{{ r.book }}</td>
              <td class="num">{{ r.today_items || '—' }}</td>
              <td class="num">{{ r.mastered }}</td>
              <td class="num" :class="{ hot: r.due > 20 }">{{ r.due }}</td>
              <td class="num" :class="{ hot: r.cloze_errors > 5 }">{{ r.cloze_errors }}</td>
              <td class="dim">{{ r.last_at ? r.last_at.slice(0, 16) : '从未' }}</td>
              <td><span class="adm-pill" :class="STATE[r.state].cls">{{ STATE[r.state].label }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <p class="adm-note">
      排序默认按「越需要处理越靠前」，点列头改成按数字排。稳定 = 2 天内有答题，
      波动 = 断 2–3 天，掉队 = 超过 3 天；积压超 20 词、错题超 5 个的行会标红。
    </p>
  </div>
</template>
