<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { rememberFocus, restoreFocus, trapTabFocus } from '../utils/api'

type LargeImportMode = 'keep_original' | 'compressed_only'

const visible = ref(false)
const count = ref(1)
const mode = ref<LargeImportMode>('keep_original')
const remember = ref(false)
const dialog = ref<HTMLElement | null>(null)
let resolveChoice: ((value: { mode: LargeImportMode; remember: boolean } | null) => void) | null = null

async function open(fileCount = 1) {
  rememberFocus()
  count.value = Math.max(1, Number(fileCount) || 1)
  mode.value = 'keep_original'
  remember.value = false
  visible.value = true
  await nextTick()
  dialog.value?.focus()
  return await new Promise<{ mode: LargeImportMode; remember: boolean } | null>(resolve => {
    resolveChoice = resolve
  })
}

function finish(value: { mode: LargeImportMode; remember: boolean } | null) {
  visible.value = false
  const resolve = resolveChoice
  resolveChoice = null
  resolve?.(value)
  restoreFocus()
}

function confirm() {
  finish({ mode: mode.value, remember: remember.value })
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.stopPropagation()
    finish(null)
  } else if (dialog.value) {
    trapTabFocus(dialog.value, e)
  }
}

defineExpose({ open })
</script>

<template>
  <div v-if="visible" class="large-import-overlay" @click.self="finish(null)">
    <div
      ref="dialog"
      class="large-import-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="large-import-title"
      tabindex="-1"
      @keydown="onKeydown"
    >
      <h2 id="large-import-title">发现超过 20 MB 的文件</h2>
      <p>本次有 {{ count }} 个大文件，请选择归档方式。</p>
      <label class="large-import-option">
        <input v-model="mode" type="radio" value="keep_original">
        <span>
          <strong>保留原件并制作压缩版</strong>
          <small>档案保留原文件，预览、复制和拖拽默认使用压缩版。</small>
        </span>
      </label>
      <label class="large-import-option">
        <input v-model="mode" type="radio" value="compressed_only">
        <span>
          <strong>只保存压缩版</strong>
          <small>不把原文件收入档案，可显著节省空间，但之后无法取回原件。</small>
        </span>
      </label>
      <label class="large-import-remember">
        <input v-model="remember" type="checkbox">
        <span>保持选择，以后不再询问</span>
      </label>
      <div class="large-import-actions">
        <button class="btn btn-ghost" type="button" @click="finish(null)">取消</button>
        <button class="btn btn-primary" type="button" @click="confirm">继续导入</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.large-import-overlay {
  position: fixed;
  inset: 0;
  z-index: 560;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.48);
}

.large-import-dialog {
  width: min(460px, calc(100vw - 40px));
  padding: 22px;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  outline: none;
  background: var(--surface);
  box-shadow: var(--shadow-lg);
}

h2 {
  margin: 0 0 7px;
  color: var(--fg);
  font-size: 17px;
}

p {
  margin: 0 0 14px;
  color: var(--fg-secondary);
  font-size: 13px;
}

.large-import-option {
  display: flex;
  gap: 10px;
  margin-top: 9px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  cursor: pointer;
}

.large-import-option:has(input:checked) {
  border-color: var(--primary);
  background: color-mix(in srgb, var(--primary) 9%, transparent);
}

.large-import-option input {
  margin-top: 3px;
}

.large-import-option span {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.large-import-option strong {
  color: var(--fg);
  font-size: 13px;
}

.large-import-option small {
  color: var(--fg-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.large-import-remember {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 14px 1px 0;
  color: var(--fg-secondary);
  font-size: 12px;
  cursor: pointer;
}

.large-import-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 18px;
}
</style>
