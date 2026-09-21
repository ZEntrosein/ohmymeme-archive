<script setup lang="ts">
import { computed, ref, watch, nextTick } from 'vue'
import type { MenuItem, MenuTrigger } from '../composables/useContextMenu'

const props = defineProps<{
  visible: boolean
  x: number
  y: number
  items: MenuItem[]
  trigger: MenuTrigger
  submenuVisible: boolean
  submenuItems: MenuItem[]
  submenuX: number
  submenuY: number
}>()

const emit = defineEmits<{
  action: [action: string, trigger: MenuTrigger]
  'show-submenu': [items: MenuItem[], x: number, y: number]
  'hide-submenu': []
  close: []
}>()

const menuEl = ref<HTMLElement>()
const submenuEl = ref<HTMLElement>()
const submenuSearchEl = ref<HTMLInputElement>()
const pos = ref({ x: props.x, y: props.y })
const subPos = ref({ x: props.submenuX, y: props.submenuY })
const submenuQuery = ref('')

const submenuSearchable = computed(() =>
  props.submenuItems.some(item => item.action.startsWith('subgroup-')),
)

const filteredSubmenuItems = computed(() => {
  const query = submenuQuery.value.trim().toLocaleLowerCase()
  if (!query) return props.submenuItems
  return props.submenuItems.filter(item => (
    item.action === '__new-subgroup__'
    || item.label.toLocaleLowerCase().includes(query)
  ))
})

const hasMatchingCollection = computed(() =>
  filteredSubmenuItems.value.some(item => item.action.startsWith('subgroup-')),
)

// 把菜单位置钳制在窗口可见范围内（越界时翻转/内移）
function clamp(el: HTMLElement | undefined, x: number, y: number) {
  if (!el) return { x, y }
  const w = el.offsetWidth
  const h = el.offsetHeight
  const vw = window.innerWidth
  const vh = window.innerHeight
  let left = x
  let top = y
  if (left + w > vw) left = Math.max(4, vw - w - 4)
  if (top + h > vh) top = Math.max(4, vh - h - 4)
  if (left < 4) left = 4
  if (top < 4) top = 4
  return { x: left, y: top }
}

watch(() => [props.visible, props.items, props.x, props.y], async () => {
  if (props.visible) {
    await nextTick()
    pos.value = clamp(menuEl.value, props.x, props.y)
  }
})

watch(() => [props.submenuVisible, props.submenuItems, props.submenuX, props.submenuY], async () => {
  if (props.submenuVisible) {
    await nextTick()
    subPos.value = clamp(submenuEl.value, props.submenuX, props.submenuY)
  }
})

watch(() => props.submenuVisible, async visible => {
  submenuQuery.value = ''
  if (!visible || !submenuSearchable.value) return
  await nextTick()
  submenuSearchEl.value?.focus()
})

// 点击子菜单项切换展开/收起；子菜单位于该项右缘，避免锚在鼠标点漂移
function onItemClick(e: MouseEvent, item: MenuItem) {
  if (item.action === 'add-to-subgroup') {
    if (props.submenuVisible) {
      emit('hide-submenu')
    } else {
      const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
      emit('show-submenu', [], r.right + 4, r.top)
    }
    return
  }
  emit('action', item.action, {} as MenuTrigger)
}

function onSubmenuSearchKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.preventDefault()
    e.stopPropagation()
    if (submenuQuery.value) submenuQuery.value = ''
    else emit('hide-submenu')
    return
  }
  if (e.key !== 'Enter') return
  const first = filteredSubmenuItems.value.find(
    item => item.action.startsWith('subgroup-') && !item.disabled,
  )
  if (!first) return
  e.preventDefault()
  e.stopPropagation()
  emit('action', first.action, {} as MenuTrigger)
}
</script>

<template>
  <div v-if="visible" class="ctx-overlay" @click="emit('close')" @contextmenu.prevent="emit('close')">
    <div ref="menuEl" class="ctx-menu" :style="{ left: pos.x + 'px', top: pos.y + 'px' }">
      <button
        v-for="item in items"
        :key="item.action"
        class="ctx-item"
        :class="{ danger: item.danger, disabled: item.disabled, 'has-submenu': item.action === 'add-to-subgroup' }"
        :disabled="item.disabled"
        @click.stop="onItemClick($event, item)"
      >
        {{ item.label }}
      </button>
    </div>
    <div v-if="submenuVisible" ref="submenuEl" class="ctx-submenu" :style="{ left: subPos.x + 'px', top: subPos.y + 'px' }">
      <div v-if="submenuSearchable" class="ctx-submenu-search" @click.stop>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-4-4" />
        </svg>
        <input
          ref="submenuSearchEl"
          v-model="submenuQuery"
          type="search"
          placeholder="搜索分组..."
          aria-label="搜索分组"
          autocomplete="off"
          spellcheck="false"
          @keydown="onSubmenuSearchKeydown"
        >
      </div>
      <div class="ctx-submenu-options">
        <button
          v-for="item in filteredSubmenuItems"
          :key="item.action"
          class="ctx-item"
          :class="{ disabled: item.disabled }"
          :disabled="item.disabled"
          @click.stop="onItemClick($event, item)"
        >
          {{ item.label }}
        </button>
        <div v-if="submenuSearchable && !hasMatchingCollection" class="ctx-submenu-empty">
          没有匹配的分组
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ctx-overlay {
  position: fixed;
  inset: 0;
  z-index: 700;
}

.ctx-menu, .ctx-submenu {
  position: fixed;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 4px;
  min-width: 160px;
  box-shadow: var(--shadow-lg);
  z-index: 701;
  animation: ctxFadeIn 0.1s ease;
}

/* 子菜单列出全部分组树时可能超过视口高度，需可滚动 */
.ctx-submenu {
  width: min(260px, calc(100vw - 8px));
  max-height: 60vh;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  overflow: hidden;
  scrollbar-width: thin;
}

.ctx-submenu-search {
  position: relative;
  flex: 0 0 auto;
  padding: 4px 4px 7px;
  border-bottom: 1px solid var(--border);
}

.ctx-submenu-search svg {
  position: absolute;
  left: 13px;
  top: 50%;
  width: 14px;
  height: 14px;
  transform: translateY(-58%);
  fill: none;
  stroke: var(--muted);
  stroke-width: 2;
  stroke-linecap: round;
  pointer-events: none;
}

.ctx-submenu-search input {
  width: 100%;
  box-sizing: border-box;
  padding: 7px 9px 7px 30px;
  border: 1px solid var(--border);
  border-radius: 5px;
  outline: none;
  background: var(--bg);
  color: var(--fg);
  font-family: inherit;
  font-size: 12px;
  line-height: 1.4;
}

.ctx-submenu-search input:focus { border-color: var(--primary); }

.ctx-submenu-search input::-webkit-search-cancel-button { cursor: pointer; }

.ctx-submenu-options {
  min-height: 0;
  overflow-y: auto;
  padding-top: 3px;
  scrollbar-width: thin;
}

.ctx-submenu-empty {
  padding: 10px 12px;
  color: var(--muted);
  font-size: 11px;
  text-align: center;
}

@keyframes ctxFadeIn {
  from { opacity: 0; transform: scale(0.95); }
  to { opacity: 1; transform: scale(1); }
}

.ctx-item {
  display: block;
  width: 100%;
  padding: 7px 12px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--fg);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
  transition: background 0.1s;
}

.ctx-item:hover {
  background: var(--surface-2);
}

.ctx-item.danger {
  color: var(--danger);
}

.ctx-item.danger:hover {
  background: rgba(239, 68, 68, 0.1);
}

.ctx-item.has-submenu::after {
  content: '▸';
  float: right;
  color: var(--muted);
  font-size: 11px;
  margin-left: 12px;
}

.ctx-item.disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.ctx-item.disabled:hover {
  background: transparent;
}
</style>
