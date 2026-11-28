<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api.js'
import { store } from '../../store.js'

const data = ref(null)
const loading = ref(false)
const error = ref('')

const kpi = computed(() => data.value?.kpi || {})

const bars = computed(() => {
  const list = data.value?.daily || []
  const max = Math.max(1, ...list.map(d => d.items))
  return list.map(d => ({
    ...d,
    h: Math.max(d.items ? 8 : 3, Math.round(d.items / max * 100)),
    label: d.date.slice(5).replace('-', '/'),
  }))
})

// 环比：拿最近 7 天与上一个 7 天比，只看方向，不做复杂统计。
const trend = computed(() => {
  const list = data.value?.daily || []
  if (list.length < 4) return null
  const half = Math.floor(list.length / 2)
  const cur = list.slice(-half).reduce((s, d) => s + d.items, 0)
  const prev = list.slice(0, half).reduce((s, d) => s + d.items, 0)
  if (!prev) return null
  const pct = Math.round((cur - prev) / prev * 100)
  return { pct, up: pct >= 0 }
})

// 样本太小时百分比没有意义，宁可不显示，也不给一个「0%」制造恐慌。
const clozeRate = computed(() => {
  const k = kpi.value
  if (!k.cloze_total || k.cloze_total < 10) return null
  return k.cloze_rate
})

const hotMax = computed(() => Math.max(1, ...(data.value?.hot || []).map(h => h.errors)))

const sourceTotal = computed(() =>
  Math.max(1, (data.value?.sources || []).reduce((s, x) => s + Number(x.c), 0)))

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api('/api/admin/overview?days=14')
  } catch (e) { error.value = e.message }
  finally { loading.value = false }
}

onMounted(load)
</script>

<template>
  <div>
    <p class="error" v-if="error">{{ error }}</p>
    <p class="adm-muted" v-if="loading && !data">正在统计…</p>

    <template v-if="data">
      <div class="adm-kpis">
        <div class="adm-kpi">
          <div class="adm-kpi-l"><span>今日活跃</span>
            <span v-if="trend" :class="trend.up ? 'adm-up' : 'adm-down'">
              {{ trend.up ? '+' : '' }}{{ trend.pct }}%
            </span>
          </div>
          <div class="adm-kpi-n">{{ kpi.today_users }}<small>人</small></div>
          <div class="adm-kpi-s">近 7 天 {{ kpi.week_active }} 人来过</div>
        </div>

        <div class="adm-kpi">
          <div class="adm-kpi-l"><span>今日答题</span></div>
          <div class="adm-kpi-n">{{ kpi.today_items }}<small>次</small></div>
          <div class="adm-kpi-s">五轮 + 语境填空合计</div>
        </div>

        <div class="adm-kpi">
          <div class="adm-kpi-l"><span>复习积压</span>
            <span :class="kpi.due_backlog > 0 ? 'adm-down' : 'adm-up'">{{ kpi.due_backlog }}</span>
          </div>
          <div class="adm-kpi-n">{{ kpi.due_backlog }}<small>词</small></div>
          <div class="adm-kpi-s">今天到期但仍未复习</div>
        </div>

        <div class="adm-kpi">
          <div class="adm-kpi-l"><span>语境正确率</span></div>
          <div class="adm-kpi-n">
            {{ clozeRate === null ? '—' : clozeRate }}<small v-if="clozeRate !== null">%</small>
          </div>
          <div class="adm-kpi-s">
            {{ clozeRate === null
              ? `样本不足（近 14 天 ${kpi.cloze_total || 0} 题，满 10 题才统计）`
              : `近 14 天 · 共 ${kpi.cloze_total} 题` }}
          </div>
        </div>
      </div>

      <div class="adm-row2">
        <section class="adm-card">
          <div class="adm-card-h">
            <h3>近 14 天答题量</h3>
            <span class="adm-muted">灰柱为周末；柱高按当天答题次数</span>
          </div>
          <div class="adm-bars">
            <div v-for="b in bars" :key="b.date" class="adm-bar" :title="`${b.date} · ${b.items} 次 / ${b.users} 人`">
              <b :class="{ g: b.weekend }" :style="{ height: b.h + '%' }"></b>
              <span>{{ b.label }}</span>
            </div>
          </div>
          <p class="adm-muted" v-if="!kpi.today_items && !kpi.week_active">
            这 14 天还没有答题记录。学员开始学习后，这里会出现柱状趋势。
          </p>
        </section>

        <section class="adm-card">
          <div class="adm-card-h">
            <h3>语境错词 Top 8</h3>
            <span class="adm-muted">近 14 天填错次数</span>
          </div>
          <p class="adm-muted" v-if="!data.hot.length">还没有语境填空错题。</p>
          <div v-else class="adm-hot">
            <div v-for="h in data.hot" :key="h.word" class="adm-hot-row">
              <span class="w">{{ h.word }}</span>
              <span class="t"><i :style="{ width: Math.max(6, h.errors / hotMax * 100) + '%' }"></i></span>
              <span class="n">{{ h.errors }}</span>
            </div>
          </div>
          <p class="adm-tip" v-if="data.hot.length">
            这些词学员「认得但写不出」，最适合补进下一批短文或加一轮听写。
          </p>
        </section>
      </div>

      <div class="adm-row2">
        <section class="adm-card">
          <div class="adm-card-h">
            <h3>词源分布</h3>
            <span class="adm-muted">按导入来源统计词条</span>
          </div>
          <div class="adm-hot">
            <div v-for="s in data.sources" :key="s.source" class="adm-hot-row">
              <span class="w">{{ s.source }}</span>
              <span class="t"><i :style="{ width: Math.max(4, s.c / sourceTotal * 100) + '%' }"></i></span>
              <span class="n">{{ s.c }}</span>
            </div>
          </div>
        </section>

        <section class="adm-card">
          <div class="adm-card-h">
            <h3>各书在用人数</h3>
            <span class="adm-muted">按当前书籍</span>
          </div>
          <div class="adm-books">
            <div v-for="b in data.books" :key="b.id" class="adm-book">
              <b>{{ b.name }}</b><span>{{ b.users }} 人</span>
            </div>
          </div>
          <div class="adm-mini-actions">
            <button class="adm-btn" @click="store.adminTab = 'learners'">看学员明细</button>
            <button class="adm-btn" @click="load">刷新</button>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>
