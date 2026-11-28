<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api.js'
import { store } from '../../store.js'

const ov = ref(null)
const learners = ref(null)
const loading = ref(false)
const error = ref('')

const kpi = computed(() => ov.value?.kpi || {})
const hot = computed(() => ov.value?.hot || [])
const lost = computed(() => learners.value?.summary?.lost || 0)
const lostNames = computed(() =>
  (learners.value?.learners || [])
    .filter(r => r.state === 'lost' || r.state === 'never')
    .map(r => r.username))

// 「需要处理」由真实数据推导：没人开始、复习积压、某词反复错。
// 三件都没有时这一栏就写「没有」——不硬凑指标。
const todos = computed(() => {
  const out = []
  if (lost.value) {
    out.push({
      tag: '学员', cls: 'a',
      title: lostNames.value.length === 1
        ? `${lostNames.value[0]} 掉队或未开始`
        : `${lost.value} 名学员掉队`,
      sub: lost.value > 1
        ? lostNames.value.slice(0, 3).join('、') + (lostNames.value.length > 3 ? ' 等' : '')
        : '超过 3 天没答题，或注册后从未开始',
      go: '看名单', to: 'learners',
    })
  }
  if (kpi.value.due_backlog) {
    out.push({
      tag: '复习', cls: 'b',
      title: `${kpi.value.due_backlog} 个词今天到期未复习`,
      sub: '积压越多，第二天忘得越快，优先催这一项',
      go: '看学员', to: 'learners',
    })
  }
  if (hot.value.length) {
    const top = hot.value[0]
    out.push({
      tag: '内容', cls: '',
      title: `${top.word} 语境填空错 ${top.errors} 次`,
      sub: top.translation || '释义缺失，建议补全',
      go: '改词条', to: 'words',
    })
  }
  return out
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [a, b] = await Promise.all([
      api('/api/admin/overview?days=14'),
      api('/api/admin/learners'),
    ])
    ov.value = a
    learners.value = b
    store.learnersTotal = b.summary.total
  } catch (e) { error.value = e.message }
  finally { loading.value = false }
}

onMounted(load)
</script>

<template>
  <div>
    <p class="error" v-if="error">{{ error }}</p>
    <p class="adm-empty" v-if="loading && !ov">正在读取…</p>

    <template v-if="ov">
      <div class="adm-strip">
        <div class="adm-st">
          <div class="k"><span>今日活跃</span><span class="adm-up">近 7 天 {{ kpi.week_active }} 人</span></div>
          <div class="v">{{ kpi.today_users }}<small>人</small></div>
        </div>
        <div class="adm-st">
          <div class="k"><span>今日答题</span></div>
          <div class="v">{{ kpi.today_items }}<small>次</small></div>
        </div>
        <div class="adm-st">
          <div class="k"><span>复习积压</span><span class="adm-dn" v-if="kpi.due_backlog">需处理</span></div>
          <div class="v">{{ kpi.due_backlog }}<small>词</small></div>
        </div>
        <div class="adm-st">
          <div class="k"><span>掉队 / 未开始</span><span class="adm-dn" v-if="lost">需处理</span></div>
          <div class="v">{{ lost }}<small>人</small></div>
        </div>
      </div>

      <section class="adm-card">
        <div class="adm-card-h">
          <h3>需要处理</h3>
          <span class="n">{{ todos.length ? todos.length + ' 项' : '无' }}</span>
        </div>
        <p class="adm-empty" v-if="!todos.length">
          今天没有需要处理的：活跃、积压、错词都正常。
        </p>
        <div v-for="t in todos" :key="t.title" class="adm-todo">
          <span class="tag" :class="t.cls">{{ t.tag }}</span>
          <div class="tx"><b>{{ t.title }}</b><span>{{ t.sub }}</span></div>
          <button class="go" @click="store.adminTab = t.to">{{ t.go }} →</button>
        </div>
      </section>

      <section class="adm-card">
        <div class="adm-card-h">
          <h3>高错误词</h3>
          <span class="n">近 14 天 · 语境填空</span>
        </div>
        <p class="adm-empty" v-if="!hot.length">还没有语境填空错题。</p>
        <div class="adm-table-wrap" v-else>
          <table class="adm-table">
            <thead>
              <tr><th>词</th><th>释义</th><th class="num">错</th><th class="num opt">作答</th><th class="opt">最近一次</th></tr>
            </thead>
            <tbody>
              <tr v-for="h in hot" :key="h.word">
                <td class="w">{{ h.word }}</td>
                <td class="dim">{{ h.translation || '（无释义）' }}</td>
                <td class="num hot">{{ h.errors }}</td>
                <td class="num opt">{{ h.attempts }}</td>
                <td class="dim opt">{{ h.last_at || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <p class="adm-note">
        这一页只回答一个问题：<b>今天有没有事情需要我做</b>。没有就关掉，不用往下滚。
      </p>
    </template>
  </div>
</template>
