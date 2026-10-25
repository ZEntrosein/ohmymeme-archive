<script setup lang="ts">
import { ref, computed } from 'vue'
import type { Collection } from '../types'

const props = defineProps<{
  node: Collection
  activeId: number | null
  depth: number
  collapsed: boolean
  dropKind: 'collection' | 'category'
  dropTargetKey: string
  hierarchyDragId?: number | null
}>()

const emit = defineEmits<{
  select: [id: number]
  'folder-context': [e: MouseEvent, id: number, name: string]
  'node-hover': [id: number | null]
  'node-pointerdown': [e: PointerEvent, id: number]
}>()

const expanded = ref(props.depth < 2)
const hasChildren = computed(() => !!(props.node.children && props.node.children.length > 0))
// 折叠态头像：取分组名首 1-2 字符
const avatarText = computed(() => {
  const name = (props.node.name || '').trim()
  return name ? Array.from(name).slice(0, 2).join('') : '?'
})

function toggleExpand(e: MouseEvent) {
  e.stopPropagation()
  expanded.value = !expanded.value
}

function onChildContext(e: MouseEvent, id: number, name: string) {
  emit('folder-context', e, id, name)
}

function onMoreClick(e: MouseEvent) {
  emit('folder-context', e, props.node.id, props.node.name)
}
</script>

<template>
  <div class="tree-node">
    <div
      class="tree-row"
      :class="{
        active: activeId === node.id,
        'library-drop-target': dropTargetKey === dropKind + ':' + node.id,
        'hierarchy-dragging': hierarchyDragId === node.id,
      }"
      :style="{ paddingLeft: (8 + depth * 14) + 'px' }"
      :data-library-drop-kind="node.direct_category_id == null ? dropKind : undefined"
      :data-library-drop-id="node.direct_category_id == null ? node.id : undefined"
      :data-library-drop-name="node.direct_category_id == null ? node.name : undefined"
      :data-hierarchy-kind="dropKind"
      :data-hierarchy-id="node.id"
      role="treeitem"
      tabindex="0"
      :aria-selected="activeId === node.id"
      :title="node.direct_category_id != null ? node.name + '\n仅显示直接属于当前类别、未归入子类别的内容' : dropKind === 'collection' && node.id === -5 ? node.name + '\n进入随机页；在此页再次点击可换一批' : node.name"
      @click="emit('select', node.id)"
      @pointerdown="emit('node-pointerdown', $event, node.id)"
      @dragstart.prevent
      @mouseenter="emit('node-hover', node.id)"
      @mouseleave="emit('node-hover', null)"
      @contextmenu="emit('folder-context', $event, node.id, node.name)"
      @keydown.enter.prevent="emit('select', node.id)"
      @keydown.space.prevent="emit('select', node.id)"
    >
      <span
        v-if="hasChildren && !collapsed"
        class="tree-toggle"
        :class="{ expanded }"
        role="button"
        tabindex="0"
        :aria-expanded="expanded"
        @click="toggleExpand"
        @keydown.enter.prevent="toggleExpand"
        @keydown.space.prevent="toggleExpand"
      >▶</span>
      <span v-else-if="!collapsed" class="tree-toggle leaf"></span>
      <span class="tree-icon">
        <svg v-if="!collapsed && dropKind === 'collection' && node.id === -5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="m18 3 3 3-3 3M18 15l3 3-3 3M3 6h3c5 0 7 12 12 12h3M3 18h3c2 0 4-2 6-6s4-6 6-6h3"/></svg>
        <template v-else-if="collapsed">
          <span class="tree-avatar" :title="node.name">{{ avatarText }}</span>
        </template>
        <svg v-else-if="node.id === -4 || node.direct_category_id != null" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>
        <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/></svg>
      </span>
      <span v-if="!collapsed" class="tree-label">{{ node.name }}</span>
      <span v-if="!collapsed" class="tree-count">{{ node.count || 0 }}</span>
      <button
        v-if="!collapsed && node.direct_category_id == null && !(dropKind === 'collection' && node.id === -5)"
        class="tree-more"
        :aria-label="dropKind === 'category' ? '类别操作' : '分组操作'"
        @click.stop="onMoreClick"
      >⋯</button>
    </div>

    <div v-if="hasChildren && expanded && !collapsed" class="tree-children">
      <CollectionTreeNode
        v-for="child in node.children"
        :key="child.id"
        :node="child"
        :active-id="activeId"
        :depth="depth + 1"
        :collapsed="collapsed"
        :drop-kind="dropKind"
        :drop-target-key="dropTargetKey"
        :hierarchy-drag-id="hierarchyDragId"
        @select="emit('select', $event)"
        @node-hover="emit('node-hover', $event)"
        @folder-context="onChildContext"
        @node-pointerdown="(e, id) => emit('node-pointerdown', e, id)"
      />
    </div>
  </div>
</template>

<style scoped>
.tree-node {
  margin-bottom: 1px;
}

.tree-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background 0.1s;
  min-width: 0;
}

.tree-row:hover {
  background: var(--surface-2);
}

.tree-row.active {
  background: var(--primary-light);
  color: var(--primary);
}

.tree-row.hierarchy-dragging {
  opacity: 0.45;
  cursor: grabbing;
}

.tree-row.library-drop-target {
  background: var(--primary-light);
  color: var(--primary-strong);
  outline: 2px solid var(--primary);
  outline-offset: -2px;
}

.tree-toggle {
  width: 14px;
  height: 14px;
  font-size: 9px;
  color: var(--muted);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: transform 0.15s ease;
  user-select: none;
}

.tree-toggle.expanded {
  transform: rotate(90deg);
}

.tree-toggle.leaf {
  cursor: default;
}

.tree-icon {
  font-size: 13px;
  flex-shrink: 0;
}

.tree-avatar {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  background: var(--surface-2);
  color: var(--fg-secondary);
  font-size: 11px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  user-select: none;
}

.tree-row.active .tree-avatar {
  background: var(--primary-light);
  color: var(--primary-strong);
}

.tree-more {
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--muted);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.1s;
}

.tree-row:hover .tree-more,
.tree-more:focus-visible {
  opacity: 1;
}

.tree-more:hover {
  background: var(--surface-2);
  color: var(--fg);
}

.tree-label {
  flex: 1;
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.tree-count {
  font-size: 11px;
  color: var(--muted);
  flex-shrink: 0;
}

.tree-row.active .tree-count {
  color: var(--primary);
}
</style>
