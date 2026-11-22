export async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    ...options
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    // 会话过期 / 未登录时给出明确引导；登录、注册接口的 401 是「密码错误」，
    // 保留后端返回的具体原因。
    if (res.status === 401 && !path.includes('/auth/')) {
      throw new Error('登录已过期，请重新登录')
    }
    throw new Error(data.detail || `HTTP ${res.status}`)
  }
  return data
}

export function playWord(text) {
  if (!text || !('speechSynthesis' in window)) return
  window.speechSynthesis.cancel()
  const utter = new SpeechSynthesisUtterance(text)
  utter.lang = 'en-US'
  utter.rate = 0.9
  window.speechSynthesis.speak(utter)
}

export const stageLabel = {
  learning: '学习中',
  memorized: '已学完',
  mastered: '已背熟'
}

export const roundName = {
  r1: 'R1 认词',
  r2: 'R2 听写',
  r3: 'R3 默写',
  r4: 'R4 英→中',
  r5: 'R5 中→英'
}
