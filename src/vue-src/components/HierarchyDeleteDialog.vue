<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { rememberFocus, restoreFocus, trapTabFocus } from '../utils/api'

type HierarchyDeleteMode = 'promote' | 'cascade'

const visible = ref(false)
const kindLabel = ref('分组')
const itemName = ref('')
const selected = ref<HierarchyDeleteMode>('promote')
const remember = ref(false)
const boxEl = ref<HTMLElement>()
const cancelEl = ref<HTMLButtonElement>()

let resolveFn: ((value: { mode: HierarchyDeleteMode; remember: boolean } | null) => void) | null = null

async function open(kind: 'collection' | 'category', name: string) {
  if (resolveFn) { resolveFn(null); resolveFn = null }
  kindLabel.value = kind === 'category' ? '类别' : '分组'
  itemName.value = name || `当前${kindLabel.value}`
  selected.value = 'promote'
  remember.value = false
  rememberFocus()
  visible.value = true
  await nextTick()
  cancelEl.value?.focus()
  return new Promise<{ mode: HierarchyDeleteMode; remember: boolean } | null>(resolve => {
    resolveFn = resolve
  })
}

function close(value: { mode: HierarchyDeleteMode; remember: boolean } | null) {
  visible.value = false
  restoreFocus()
  if (resolveFn) { resolveFn(value); resolveFn = null }
}

function confirmDelete() {
  close({ mode: selected.value, remember: remember.value })
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') { e.stopPropagation(); close(null) }
  else if (boxEl.value) trapTabFocus(boxEl.value, e)
}

defineExpose({ open })
</script>

<template>
  <div v-if="visible" class="hierarchy-delete-overlay" @click.self="close(null)">
    <div
      ref="boxEl"
      class="hierarchy-delete-box"
      role="dialog"
      aria-modal="true"
      aria-labelledby="hierarchy-delete-title"
      @keydown="onKeydown"
    >
      <div id="hierarchy-delete-title" class="hierarchy-delete-title">
        删除{{ kindLabel }}“{{ itemName }}”
      </div>
      <div class="hierarchy-delete-subtitle">请选择子级的处理方式。表情包和聊天记录本身不会被删除。</div>

      <label class="delete-option" :class="{ selected: selected === 'promote' }">
        <input v-model="selected" type="radio" value="promote">
        <span>
          <strong>保留子级并提升</strong>
          <small>只删除当前{{ kindLabel }}；直属内容和子{{ kindLabel }}移动到上一级。</small>
        </span>
      </label>
      <label class="delete-option danger-option" :class="{ selected: selected === 'cascade' }">
        <input v-model="selected" type="radio" value="cascade">
        <span>
          <strong>删除全部子级</strong>
          <small>删除当前{{ kindLabel }}及整棵子树；其中内容移动到上一级。</small>
        </span>
      </label>

      <label class="remember-row">
        <input v-model="remember" type="checkbox">
        始终使用此方式，不再询问
      </label>

      <div class="hierarchy-delete-actions">
        <button class="btn btn-danger" @click="confirmDelete">删除</button>
        <button ref="cancelEl" class="btn btn-secondary" @click="close(null)">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.hierarchy-delete-overlay {
  position: fixed;
  inset: 0;
  z-index: 265;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.55);
  animation: fadeIn 0.15s ease;
}

.hierarchy-delete-box {
  width: min(440px, calc(100vw - 32px));
  padding: 20px 22px;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--surface);
  box-shadow: var(--shadow-lg);
}

.hierarchy-delete-title {
  color: var(--fg);
  font-size: 15px;
  font-weight: 650;
  overflow-wrap: anywhere;
}

.hierarchy-delete-subtitle {
  margin: 6px 0 14px;
  color: var(--fg-secondary);
  font-size: 12px;
  line-height: 1.55;
}

.delete-option {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  margin-top: 8px;
  padding: 11px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: border-color 0.12s, background 0.12s;
}

.delete-option:hover {
  border-color: color-mix(in srgb, var(--primary) 65%, var(--border));
  background: var(--surface-2);
}

.delete-option.selected {
  border-color: var(--primary);
  background: color-mix(in srgb, var(--primary) 14%, var(--surface));
}

.delete-option.danger-option.selected {
  border-color: var(--danger);
  background: color-mix(in srgb, var(--danger) 13%, var(--surface));
}

.delete-option input {
  margin-top: 3px;
  accent-color: var(--primary);
}

.delete-option.danger-option input {
  accent-color: var(--danger);
}

.delete-option span {
  display: grid;
  gap: 3px;
}

.delete-option strong {
  color: var(--fg);
  font-size: 13px;
}

.delete-option small {
  color: var(--fg-secondary);
  font-size: 11.5px;
  line-height: 1.5;
}

.delete-option.selected strong {
  color: var(--fg);
}

.delete-option.selected small {
  color: color-mix(in srgb, var(--fg) 72%, var(--surface));
}

.remember-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 14px;
  color: var(--fg-secondary);
  font-size: 12px;
  cursor: pointer;
}

.hierarchy-delete-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 18px;
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
</style>
