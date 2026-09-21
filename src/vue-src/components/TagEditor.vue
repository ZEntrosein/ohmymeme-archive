<script setup lang="ts">
import { ref, computed, nextTick } from 'vue'
import { api, rememberFocus, restoreFocus, trapTabFocus } from '../utils/api'
import type { TagRecord } from '../types'

const emit = defineEmits<{
  confirm: [tags: TagRecord[] | null, memeId: number]
}>()

const visible = ref(false)
const memeId = ref(0)
const allTags = ref<TagRecord[]>([])
const selected = ref<TagRecord[]>([])
const query = ref('')
const newCategory = ref('')
const loading = ref(false)
const batchMode = ref(false)
const boxEl = ref<HTMLElement>()
const inputEl = ref<HTMLInputElement>()

let resolveFn: ((tags: TagRecord[] | null) => void) | null = null

function tagKey(tag: TagRecord) {
  return `${tag.category || ''}\u001f${tag.name}`.toLocaleLowerCase()
}

function isSelected(tag: TagRecord) {
  const key = tagKey(tag)
  return selected.value.some(item => tagKey(item) === key)
}

async function open(id: number): Promise<TagRecord[] | null> {
  memeId.value = id
  batchMode.value = false
  rememberFocus()
  visible.value = true
  loading.value = true
  query.value = ''
  newCategory.value = ''
  selected.value = []
  try {
    const [all, cur] = await Promise.all([
      api('get_tag_records'),
      api('get_meme_tag_records', id),
    ])
    allTags.value = all || []
    selected.value = cur || []
  } catch (_) {
    allTags.value = []
    selected.value = []
  }
  loading.value = false
  await nextTick()
  inputEl.value?.focus()
  return new Promise(resolve => { resolveFn = resolve })
}

// 批量模式：不加载单个表情已有标签，确认后由调用方合并追加
async function openBatch(): Promise<TagRecord[] | null> {
  memeId.value = 0
  batchMode.value = true
  rememberFocus()
  visible.value = true
  loading.value = true
  query.value = ''
  newCategory.value = ''
  selected.value = []
  try {
    allTags.value = (await api('get_tag_records')) || []
  } catch (_) {
    allTags.value = []
  }
  loading.value = false
  await nextTick()
  inputEl.value?.focus()
  return new Promise(resolve => { resolveFn = resolve })
}

function close(tags: TagRecord[] | null) {
  visible.value = false
  restoreFocus()
  emit('confirm', tags, memeId.value)
  if (resolveFn) { resolveFn(tags); resolveFn = null }
}

function toggleTag(tag: TagRecord) {
  const key = tagKey(tag)
  const i = selected.value.findIndex(item => tagKey(item) === key)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push(tag)
}

function addFromInput() {
  const v = query.value.trim()
  if (!v) return
  const existing = allTags.value.find(tag => (
    tag.name.toLocaleLowerCase() === v.toLocaleLowerCase()
    && (tag.category || '').toLocaleLowerCase() === newCategory.value.trim().toLocaleLowerCase()
  ))
  const tag = existing || { name: v, category: newCategory.value.trim() }
  if (!isSelected(tag)) selected.value.push(tag)
  query.value = ''
}

const filteredGroups = computed(() => {
  const q = query.value.trim().toLowerCase()
  const groups = new Map<string, TagRecord[]>()
  for (const tag of allTags.value) {
    if (q && !tag.name.toLowerCase().includes(q) && !tag.category.toLowerCase().includes(q)) continue
    const category = tag.category || '未分类'
    const items = groups.get(category) || []
    items.push(tag)
    groups.set(category, items)
  }
  return [...groups.entries()].map(([category, tags]) => ({ category, tags }))
})

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') { e.preventDefault(); addFromInput() }
}

function onBoxKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(null) }
  else if (boxEl.value) trapTabFocus(boxEl.value, e)
}

function confirm() {
  // 只保存明确添加的标签（回车/点击）；输入框残留文字不自动提交
  close(selected.value.slice())
}

defineExpose({ open, openBatch })
</script>

<template>
  <div v-if="visible" class="tag-editor-overlay" @click.self="close(null)">
    <div class="tag-editor-box" ref="boxEl" role="dialog" aria-modal="true" aria-labelledby="tag-editor-title" @keydown="onBoxKeydown">
      <div id="tag-editor-title" class="tag-editor-title">{{ batchMode ? '批量添加标签' : '编辑标签' }}</div>

      <input
        id="tag-editor-input"
        ref="inputEl"
        v-model="query"
        class="tag-editor-input"
        type="text"
        placeholder="搜索已有标签或输入新标签，回车添加"
        autocomplete="off"
        spellcheck="false"
        @keydown="onKeydown"
      >
      <input
        v-model="newCategory"
        class="tag-editor-input tag-category-input"
        type="text"
        placeholder="新标签的种类（留空为未分类）"
        autocomplete="off"
        spellcheck="false"
      >

      <div class="tag-editor-list">
        <div v-for="group in filteredGroups" :key="group.category" class="tag-editor-group">
          <span class="tag-editor-category">{{ group.category }}</span>
          <span
            v-for="tag in group.tags"
            :key="tagKey(tag)"
            class="tag"
            :class="{ active: isSelected(tag) }"
            role="button"
            tabindex="0"
            :aria-pressed="isSelected(tag)"
            @click="toggleTag(tag)"
            @keydown.enter.prevent="toggleTag(tag)"
            @keydown.space.prevent="toggleTag(tag)"
          >{{ tag.name }}</span>
        </div>
        <div v-if="!loading && filteredGroups.length === 0" class="tag-editor-empty">
          {{ query.trim() ? `无匹配标签，回车创建"${query.trim()}"` : '暂无标签' }}
        </div>
      </div>

      <div v-if="selected.length" class="tag-editor-selected">
        <span
          v-for="tag in selected"
          :key="'sel-' + tagKey(tag)"
          class="tag active"
          role="button"
          tabindex="0"
          aria-pressed="true"
          @click="toggleTag(tag)"
          @keydown.enter.prevent="toggleTag(tag)"
          @keydown.space.prevent="toggleTag(tag)"
        >{{ tag.category ? tag.category + ' · ' : '' }}{{ tag.name }} ×</span>
      </div>

      <div class="tag-editor-actions">
        <button class="btn btn-secondary" @click="close(null)">取消</button>
        <button class="btn btn-primary" @click="confirm">确定</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tag-editor-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
  animation: fadeIn 0.15s ease;
}

.tag-editor-box {
  background: var(--surface);
  border-radius: var(--radius-lg);
  padding: 20px 24px;
  width: 420px;
  border: 1px solid var(--border);
  box-shadow: var(--shadow-lg);
}

.tag-editor-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--fg);
  margin-bottom: 14px;
}

.tag-editor-input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg);
  color: var(--fg);
  font-size: 13px;
  font-family: inherit;
  outline: none;
  box-sizing: border-box;
  margin-bottom: 10px;
  transition: border-color 0.15s;
}

.tag-editor-input:focus {
  border-color: var(--primary);
}

.tag-category-input { margin-top: -4px; }

.tag-editor-list {
  max-height: 200px;
  overflow-y: auto;
  display: block;
  padding: 4px 0;
  margin-bottom: 10px;
}

.tag-editor-group { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.tag-editor-category { color: var(--muted); font-size: 10px; font-weight: 600; }

.tag-editor-empty {
  font-size: 12px;
  color: var(--muted);
  width: 100%;
  text-align: center;
  padding: 8px 0;
}

.tag-editor-selected {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 14px;
}

.tag-editor-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
</style>
