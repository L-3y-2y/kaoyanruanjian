// 学习时长计时器：页面可见且已登录时每秒累计，每 15 秒上报一次，
// 切到后台/关闭时立即冲刷，避免丢失。时长统计到服务端，跨设备可见。
import { api } from './api.js'
import { store } from './store.js'

let pending = 0
let timerId = null
let sending = false

function active() {
  return store.me != null && document.visibilityState === 'visible'
}

function flush() {
  if (sending || pending <= 0) return
  const delta = pending
  pending = 0
  sending = true
  api('/api/study/heartbeat', {
    method: 'POST',
    body: JSON.stringify({ seconds: delta })
  })
    .catch(() => { pending += delta }) // 失败则重新累计，下次再上报
    .finally(() => { sending = false })
}

function tick() {
  if (!active()) return
  pending += 1
  store.todaySeconds += 1
  if (pending >= 15) flush()
}

function onVisibility() {
  if (document.visibilityState === 'hidden') flush()
}

export function startStudyTimer() {
  if (timerId) return
  timerId = setInterval(tick, 1000)
  document.addEventListener('visibilitychange', onVisibility)
  window.addEventListener('pagehide', flush)
}

export function stopStudyTimer() {
  if (timerId) clearInterval(timerId)
  timerId = null
  document.removeEventListener('visibilitychange', onVisibility)
  window.removeEventListener('pagehide', flush)
}
