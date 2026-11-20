export async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    ...options
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
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
  memorized: '已背下（初步）',
  mastered: '真正学完'
}

export const roundName = {
  r1: 'R1 认词',
  r2: 'R2 听写',
  r3: 'R3 默写',
  r4: 'R4 英→中',
  r5: 'R5 中→英'
}
