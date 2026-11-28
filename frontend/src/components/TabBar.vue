<script setup>
import { store, goTab, openMore } from '../store.js'

// 移动优先：底部四个主入口 + 「我的」面板（AI/词库/后台/账号/书籍都收进面板）
const tabs = [
  { key: 'learn', label: '学习' },
  { key: 'review', label: '复习' },
  { key: 'articles', label: '短文' },
  { key: 'reading', label: '阅读' },
]
</script>

<template>
  <nav class="tabbar">
    <div class="tabbar-inner">
      <button v-for="t in tabs" :key="t.key" :class="{ on: store.activeTab === t.key }"
              @click="goTab(t.key)">
        <span class="tico">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
               stroke-linecap="round" stroke-linejoin="round">
            <template v-if="t.key === 'learn'">
              <rect x="3" y="5" width="18" height="14" rx="3" />
              <path d="M7.5 10h6M7.5 14h9" />
            </template>
            <template v-else-if="t.key === 'review'">
              <path d="M20.5 12a8.5 8.5 0 1 1-2.5-6" />
              <path d="M20.5 3.5v4.5H16" />
            </template>
            <template v-else-if="t.key === 'articles'">
              <path d="M6 3.5h7.5L18 8v12.5H6z" />
              <path d="M13.5 3.5V8H18" />
              <path d="M8.5 12h7M8.5 15.5h5" />
            </template>
            <template v-else>
              <path d="M12 4 3.5 8.2 12 12.4l8.5-4.2z" />
              <path d="M3.5 12.4 12 16.6l8.5-4.2" />
            </template>
          </svg>
          <span class="tbadge" v-if="t.key === 'review' && store.batchesCount">{{ store.batchesCount }}</span>
        </span>
        {{ t.label }}
      </button>

      <button :class="{ on: store.moreOpen }" @click="openMore">
        <span class="tico">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
               stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="8.4" r="3.6" />
            <path d="M5 20c1.7-3.7 4.1-5.4 7-5.4s5.3 1.7 7 5.4" />
          </svg>
        </span>
        我的
      </button>
    </div>
  </nav>
</template>
