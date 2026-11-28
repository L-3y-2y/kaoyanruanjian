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
const filter = ref('all')     // all / stable / wobble / lost
const sortKey = ref('idle')   // idle / mastered / due / errors / today
const desc = ref(true)

const STATE = {
  stable: { label: '稳定', cls: 'ok' },
  wobble: { label: '波动', cls: 'warn' },
  lost:   { label: '掉队', cls: 'bad' },
  never:  { label: '未开始', cls: 'mut' },
}

const list = computed(() => {
  let out = rows.value
  if (filter.value !== 'all') {
    out = out.filter(r => (filter.value === 'lost'
      ? (r.state === 'lost' || r.state === 'never')
      : r.state === filter.value))
  }
  const k = q.value.trim().toLowerCase()
  if (k) out = out.filter(r => r.username.toLowerCase().includes(k) || r.book.toLowerCase().includes(k))
  const pick = {
    idle: r => r.days_idle ?? 999,
    mastered: r => r.mastered,
    due: r => r.due,
    errors: r => r.cloze_errors,
    today: r => r.today_items,
  }[sortKey.value]
  out = [...out].sort((a, b) => desc.value ? pick(b) - pick(a) : pick(a) - pick(b))
  return out
})

function share(n) {
  const t = summary.value.total || 0
  return t ? Math.round(n / t * 100) + '%' : '—'
}

const lostNames = computed(() =>
  rows.value.filter(r => r.state === 'lost' || r.state === 'never').map(r => r.username))

function sortBy(k) {
  if (sortKey.value === k) desc.value = !desc.value
  else { sortKey.value = k; desc.value = true }
}
function arrow(k) {
  return sortKey.value === k ? (desc.value ? ' ↓' : ' ↑') : ''
}

async function copyLost() {
  notice.value = ''
  if (!lostNames.value.length) { notice.value = '没有掉队学员。'; return }
  try {
    await navigator.clipboard.writeText(lostNames.value.join('、'))
    notice.value = `已复制 ${lostNames.value.length} 个用户名，可以逐个提醒。`
  } catch {
    notice.value = `掉队学员：${lostNames.value.join('、')}`
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
    <p class="notice" v-if="notice">{{ notice }}</p>

    <div class="adm-kpis" style="grid-template-columns:repeat(4,1fr)">
      <button v-for="c in [
        { k: 'all', t: '全部学员', v: summary.total, cls: '' },
        { k: 'stable', t: '稳定学习', v: summary.stable, cls: 'ok' },
        { k: 'wobble', t: '中途波动', v: summary.wobble, cls: 'warn' },
        { k: 'lost', t: '掉队 / 未开始', v: summary.lost, cls: 'bad' },
      ]" :key="c.k" class="adm-kpi adm-kpi-btn" :class="{ on: filter === c.k }"
          @click="filter = c.k">
        <div class="adm-kpi-l"><span>{{ c.t }}</span></div>
        <div class="adm-kpi-n">{{ c.v }}<small>人</small></div>
        <div class="adm-kpi-s">
          {{ c.k === 'all' ? '点卡片按状态筛选' : `占比 ${share(c.v)}` }}
        </div>
      </button>
    </div>

    <section class="adm-card">
      <div class="adm-card-h">
        <h3>学员进度</h3>
        <div style="display:flex;gap:8px;align-items:center">
          <input v-model="q" class="adm-search" placeholder="搜索用户名 / 书籍" />
          <button class="adm-btn" @click="load">刷新</button>
          <button class="adm-btn" :disabled="!lostNames.length" @click="copyLost">复制掉队名单</button>
        </div>
      </div>

      <p class="adm-muted" v-if="loading && !rows.length">正在读取…</p>
      <p class="adm-muted" v-else-if="!list.length">没有符合条件的学员。</p>

      <div class="adm-table-wrap" v-else>
        <table class="adm-table">
          <thead>
            <tr>
              <th>学员</th>
              <th>当前书籍</th>
              <th class="num" @click="sortBy('today')">今日答题{{ arrow('today') }}</th>
              <th class="num" @click="sortBy('mastered')">已掌握{{ arrow('mastered') }}</th>
              <th class="num" @click="sortBy('due')">复习积压{{ arrow('due') }}</th>
              <th class="num" @click="sortBy('errors')">语境错题{{ arrow('errors') }}</th>
              <th @click="sortBy('idle')">最近学习{{ arrow('idle') }}</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in list" :key="r.id">
              <td><b>{{ r.username }}</b><span class="adm-uid">#{{ r.id }}</span></td>
              <td class="adm-muted">{{ r.book }}</td>
              <td class="num">{{ r.today_items || '—' }}</td>
              <td class="num">{{ r.mastered }}</td>
              <td class="num" :class="{ hot: r.due > 20 }">{{ r.due }}</td>
              <td class="num" :class="{ hot: r.cloze_errors > 5 }">{{ r.cloze_errors }}</td>
              <td class="adm-muted">
                {{ r.last_at ? r.last_at.slice(5, 16) : '从未学习' }}
              </td>
              <td><span class="adm-pill" :class="STATE[r.state].cls">{{ STATE[r.state].label }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>

      <p class="adm-tip">
        「稳定」= 最近 2 天内有答题；「波动」= 断 2–3 天；「掉队」= 超过 3 天没来。
        复习积压超过 20 词、语境错题超过 5 个的行会标红，优先处理这两类人。
      </p>
    </section>
  </div>
</template>
