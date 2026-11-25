<script setup>
defineProps({
  show: { type: Boolean, default: false },
  title: { type: String, default: '请确认' },
  message: { type: String, default: '' },
  confirmText: { type: String, default: '确认' },
  cancelText: { type: String, default: '取消' },
})
const emit = defineEmits(['confirm', 'cancel'])
</script>

<template>
  <div v-if="show" class="modal-backdrop" @click.self="emit('cancel')">
    <div class="card confirm-dialog">
      <h3>{{ title }}</h3>
      <p class="muted confirm-msg">{{ message }}</p>
      <div class="actions">
        <button class="ghost" @click="emit('cancel')">{{ cancelText }}</button>
        <button class="primary" @click="emit('confirm')">{{ confirmText }}</button>
      </div>
    </div>
  </div>
</template>
