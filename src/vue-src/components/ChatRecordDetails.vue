<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { api, createBackdropClickGuard, focusNextDetailInput, handleDetailSaveShortcut, rememberFocus, restoreFocus, trapTabFocus } from '../utils/api'

interface ChatMessageView {
  path: string
  depth: number
  sender: string
  sender_id: string
  time: number | string
  preview: string
  segment_types: string[]
  media?: ChatMediaView[]
  quotes?: ChatQuoteView[]
}

interface ChatMediaView {
  kind: string
  index: number
  slot?: number
  url: string
  alt?: string
  quoted?: boolean
}

interface ChatQuoteView {
  sender: string
  time: number | string
  preview: string
  media?: ChatMediaView[]
}

interface TagRecord {
  id?: number
  name: string
  category: string
}

interface CollectionRecord {
  id: number
  name: string
  path: string
}

interface CategoryRecord {
  id: number
  name: string
  path: string
  parent_id?: number | null
  children?: CategoryRecord[]
}

interface ChatRecordDetail {
  id: number
  title: string
  name: string
  media_folder: string
  genre_label: string
  node_count: number
  record_count: number
  media_count: number
  max_depth: number
  message_type: string
  peer_id: string
  peer_label: string
  source_message_id: string
  source_message_seq: string
  source_locations: {
    source_type: string
    source_self_id: string
    source_peer_id: string
    source_real_seq: string
    source_time?: string | number
  }[]
  captured_at: string
  created_at: string
  updated_at: string
  messages: ChatMessageView[]
  send_command: string
  tags: string[]
  tag_records: TagRecord[]
  all_tags: string[]
  all_tag_records: TagRecord[]
  collections: CollectionRecord[]
  all_collections: CollectionRecord[]
  category: CategoryRecord | null
  all_categories: CategoryRecord[]
  blocked_message_paths: string[]
  blocked_message_count: number
}

const emit = defineEmits<{
  copied: []
  saved: [recordId: number]
  requestCreateCollection: []
  requestCreateCategory: []
  requestDelete: [recordId: number]
}>()
const visible = ref(false)
const backdrop = createBackdropClickGuard(close)
const loading = ref(false)
const error = ref('')
const detail = ref<ChatRecordDetail | null>(null)
const boxEl = ref<HTMLElement>()
const closeEl = ref<HTMLButtonElement>()
let requestGeneration = 0
let currentRecordId: number | null = null
const saving = ref(false)
const selectedTags = ref<TagRecord[]>([])
const selectedCollectionIds = ref<number[]>([])
const selectedCategoryId = ref<number | null>(null)
const categoryQuery = ref('')
const collectionQuery = ref('')
const tagQuery = ref('')
const tagName = ref('')
const tagCategory = ref('')
const failedMedia = ref<Set<string>>(new Set())
const loadedMedia = ref<Set<string>>(new Set())
const blockedMessagePaths = ref<string[]>([])

const blockedMessageCount = computed(() => blockedMessagePaths.value.length)

const tagGroups = computed(() => {
  const groups = new Map<string, TagRecord[]>()
  for (const tag of detail.value?.all_tag_records || []) {
    const category = tag.category || '未分类'
    const items = groups.get(category) || []
    items.push(tag)
    groups.set(category, items)
  }
  return [...groups.entries()].map(([category, tags]) => ({ category, tags }))
})

const filteredTagGroups = computed(() => {
  const query = tagQuery.value.trim().toLocaleLowerCase()
  if (!query) return tagGroups.value
  return tagGroups.value
    .map(group => ({
      category: group.category,
      tags: group.tags.filter(tag => (
        tag.name.toLocaleLowerCase().includes(query)
        || group.category.toLocaleLowerCase().includes(query)
      )),
    }))
    .filter(group => group.tags.length > 0)
})

const categoryOptions = computed(() => {
  const result: Array<{ id: number; label: string }> = []
  const walk = (items: CategoryRecord[], depth = 0) => {
    for (const item of items || []) {
      if (item.id > 0) result.push({ id: item.id, label: `${'　'.repeat(depth)}${item.name}` })
      walk(item.children || [], depth + 1)
    }
  }
  walk(detail.value?.all_categories || [])
  return result
})

const filteredCategoryOptions = computed(() => {
  const query = categoryQuery.value.trim().toLocaleLowerCase()
  if (!query) return categoryOptions.value
  return categoryOptions.value.filter(item => (
    item.label.toLocaleLowerCase().includes(query)
  ))
})

const filteredCollectionOptions = computed(() => {
  const query = collectionQuery.value.trim().toLocaleLowerCase()
  const options = detail.value?.all_collections || []
  if (!query) return options
  return options.filter(item => (
    (item.path || item.name).toLocaleLowerCase().includes(query)
  ))
})

const selectedCollectionItems = computed(() => {
  const ids = new Set(selectedCollectionIds.value)
  return (detail.value?.all_collections || []).filter(item => ids.has(Number(item.id)))
})

const selectedCategoryLabel = computed(() => {
  if (!selectedCategoryId.value) return '未分类'
  return categoryOptions.value.find(item => item.id === selectedCategoryId.value)?.label || '已选类别'
})

function tagKey(tag: TagRecord) {
  return `${tag.category || ''}\u001f${tag.name}`.toLocaleLowerCase()
}

function hasTag(tag: TagRecord) {
  return selectedTags.value.some(item => tagKey(item) === tagKey(tag))
}

function toggleTag(tag: TagRecord) {
  const key = tagKey(tag)
  const index = selectedTags.value.findIndex(item => tagKey(item) === key)
  if (index >= 0) selectedTags.value.splice(index, 1)
  else selectedTags.value.push({ name: tag.name, category: tag.category || '' })
}

function addTag() {
  const name = tagName.value.trim()
  if (!name) return
  const tag = { name, category: tagCategory.value.trim() }
  if (!hasTag(tag)) selectedTags.value.push(tag)
  tagName.value = ''
}

function removeTag(tag: TagRecord) {
  const index = selectedTags.value.findIndex(item => tagKey(item) === tagKey(tag))
  if (index >= 0) selectedTags.value.splice(index, 1)
}

function isCollectionSelected(id: number) {
  return selectedCollectionIds.value.includes(Number(id))
}

function toggleCollection(id: number) {
  const value = Number(id)
  if (isCollectionSelected(value)) {
    selectedCollectionIds.value = selectedCollectionIds.value.filter(item => item !== value)
  } else {
    selectedCollectionIds.value = [...selectedCollectionIds.value, value]
  }
}

function selectCategory(id: number | null) {
  selectedCategoryId.value = id
}

function hydrateManagement(value: ChatRecordDetail) {
  selectedTags.value = (value.tag_records || []).map(tag => ({
    name: tag.name,
    category: tag.category || '',
  }))
  selectedCollectionIds.value = (value.collections || []).map(item => Number(item.id))
  selectedCategoryId.value = value.category?.id ? Number(value.category.id) : null
  blockedMessagePaths.value = [...(value.blocked_message_paths || [])]
  tagName.value = ''
  tagCategory.value = ''
}

function blockingAncestorPath(path: string) {
  return blockedMessagePaths.value.find(item => (
    item !== path && path.startsWith(`${item}/`)
  )) || ''
}

function isMessageBlocked(path: string) {
  return blockedMessagePaths.value.includes(path) || Boolean(blockingAncestorPath(path))
}

function toggleMessageBlocked(path: string) {
  if (blockingAncestorPath(path)) return
  if (blockedMessagePaths.value.includes(path)) {
    blockedMessagePaths.value = blockedMessagePaths.value.filter(item => item !== path)
    return
  }
  blockedMessagePaths.value = [
    ...blockedMessagePaths.value.filter(item => !item.startsWith(`${path}/`)),
    path,
  ]
}

function clearBlockedMessages() {
  blockedMessagePaths.value = []
}

function mediaKey(media: ChatMediaView) {
  return `${media.index}:${media.url}`
}

function mediaVisible(media: ChatMediaView) {
  return Boolean(media.url) && !failedMedia.value.has(mediaKey(media))
}

function mediaLoaded(media: ChatMediaView) {
  return loadedMedia.value.has(mediaKey(media))
}

function onMediaLoaded(media: ChatMediaView) {
  const next = new Set(loadedMedia.value)
  next.add(mediaKey(media))
  loadedMedia.value = next
  if (failedMedia.value.has(mediaKey(media))) {
    const visible = new Set(failedMedia.value)
    visible.delete(mediaKey(media))
    failedMedia.value = visible
  }
}

function onMediaError(media: ChatMediaView) {
  const next = new Set(failedMedia.value)
  next.add(mediaKey(media))
  failedMedia.value = next
  const loaded = new Set(loadedMedia.value)
  loaded.delete(mediaKey(media))
  loadedMedia.value = loaded
}

function messagePreview(message: ChatMessageView) {
  const original = message.preview || ''
  const media = (message.media || []).filter(item => !item.quoted)
  if (!original || media.length === 0) return original || '[空消息]'

  // 仅移除已经成功加载的媒体占位符；slot 表示该媒体在当前消息中的
  // 媒体段顺序，因此图片与视频/语音混排时也不会误删别的占位符。
  let mediaSlot = 0
  return original
    .replace(/\[(图片|语音|视频|文件)\]/g, token => {
      const item = media.find(mediaItem => mediaItem.slot === mediaSlot)
      mediaSlot += 1
      return item && mediaLoaded(item) ? '' : token
    })
    .trim()
}

function directMedia(message: ChatMessageView) {
  return (message.media || []).filter(item => !item.quoted)
}

function quotePreview(quote: ChatQuoteView) {
  const original = quote.preview || ''
  const media = quote.media || []
  let mediaSlot = 0
  return original.replace(/\[(图片|语音|视频|文件)\]/g, token => {
    const item = media.find(mediaItem => mediaItem.slot === mediaSlot)
    mediaSlot += 1
    return item && mediaLoaded(item) ? '' : token
  }).trim()
}

async function open(recordId: number) {
  backdrop.reset()
  const generation = ++requestGeneration
  currentRecordId = Number(recordId)
  rememberFocus()
  visible.value = true
  loading.value = true
  error.value = ''
  detail.value = null
  categoryQuery.value = ''
  collectionQuery.value = ''
  tagQuery.value = ''
  failedMedia.value = new Set()
  loadedMedia.value = new Set()
  try {
    const result = await api('get_chat_record_details', recordId)
    if (generation !== requestGeneration) return
    if (!result?.ok) {
      error.value = result?.error || '聊天记录读取失败'
      return
    }
    detail.value = result
    hydrateManagement(result)
    await nextTick()
    closeEl.value?.focus()
  } catch (_) {
    if (generation === requestGeneration) error.value = '聊天记录读取失败'
  } finally {
    if (generation === requestGeneration) loading.value = false
  }
}

async function saveManagement() {
  if (!detail.value || saving.value) return
  saving.value = true
  error.value = ''
  try {
    const result = await api('update_chat_record_details', detail.value.id, {
      name: detail.value.name,
      media_folder: detail.value.media_folder,
      tags: selectedTags.value,
      collection_ids: selectedCollectionIds.value,
      category_id: selectedCategoryId.value,
      blocked_message_paths: blockedMessagePaths.value,
    })
    if (!result?.ok) {
      error.value = result?.error || '保存失败'
      return
    }
    detail.value.name = result.name || detail.value.name
    detail.value.media_folder = result.media_folder || detail.value.media_folder
    detail.value.tag_records = result.tag_records || []
    detail.value.tags = result.tags || []
    detail.value.all_tags = result.all_tags || detail.value.all_tags
    detail.value.all_tag_records = result.all_tag_records || detail.value.all_tag_records
    detail.value.collections = result.collections || []
    detail.value.all_collections = result.all_collections || detail.value.all_collections
    detail.value.category = result.category || null
    detail.value.all_categories = result.all_categories || detail.value.all_categories
    detail.value.blocked_message_paths = result.blocked_message_paths || []
    detail.value.blocked_message_count = Number(result.blocked_message_count || 0)
    hydrateManagement(detail.value)
    emit('saved', detail.value.id)
  } catch (_) {
    error.value = '保存失败'
  } finally {
    saving.value = false
  }
}

function close() {
  backdrop.reset()
  requestGeneration++
  visible.value = false
  loading.value = false
  restoreFocus()
}

function requestDelete() {
  if (detail.value) emit('requestDelete', detail.value.id)
}

async function copyCommand() {
  if (!detail.value?.send_command) return
  const value = detail.value.send_command
  try {
    await navigator.clipboard.writeText(value)
  } catch (_) {
    const input = document.createElement('textarea')
    input.value = value
    input.style.position = 'fixed'
    input.style.opacity = '0'
    document.body.appendChild(input)
    input.select()
    document.execCommand('copy')
    input.remove()
  }
  emit('copied')
}

function formatTime(value: number | string) {
  if (!value) return ''
  const numeric = Number(value)
  const date = Number.isFinite(numeric) && numeric > 0
    ? new Date(numeric < 1e12 ? numeric * 1000 : numeric)
    : new Date(String(value))
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString()
}

// 标签名称的 Enter 仍用于添加，空输入则交给详情页跳转到下一项。
function onNewTagKeydown(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229 || e.key !== 'Enter' || !tagName.value.trim()) return
  e.preventDefault()
  addTag()
}

function onKeydown(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) return
  if (boxEl.value && focusNextDetailInput(boxEl.value, e)) return
  if (e.key === 'Escape') {
    e.preventDefault()
    e.stopPropagation()
    if (!e.repeat) close()
  } else if (boxEl.value) {
    trapTabFocus(boxEl.value, e)
  }
}

function reload() {
  if (currentRecordId != null) return open(currentRecordId)
}

defineExpose({ open, close, reload, isOpen: () => visible.value })
</script>

<template>
  <div
    v-if="visible"
    class="chat-details-overlay"
    @pointerdown.capture="backdrop.onPointerDown"
    @pointerup.capture="backdrop.onPointerUp"
    @pointercancel.capture="backdrop.reset"
    @click="backdrop.onClick"
  >
    <div ref="boxEl" class="chat-details-box" role="dialog" aria-modal="true" aria-labelledby="chat-details-title" @keydown.capture="handleDetailSaveShortcut($event, saveManagement)" @keydown="onKeydown">
      <header class="chat-details-header">
        <div>
          <div id="chat-details-title" class="chat-details-kicker">聊天记录详情</div>
          <h2>{{ detail?.name || detail?.title || '正在读取…' }}</h2>
        </div>
        <button ref="closeEl" class="icon-btn" type="button" aria-label="关闭" @click="close">×</button>
      </header>

      <div v-if="loading" class="chat-details-state">正在读取聊天记录…</div>
      <div v-else-if="error" class="chat-details-state error">{{ error }}</div>
      <div v-else-if="detail" class="chat-details-content">
        <aside class="chat-details-meta">
          <span class="chat-genre-pill">{{ detail.genre_label }}</span>
          <dl>
            <div><dt>消息数</dt><dd>{{ detail.node_count }}</dd></div>
            <div><dt>嵌套层级</dt><dd>{{ detail.max_depth }}</dd></div>
            <div><dt>媒体段</dt><dd>{{ detail.media_count }}</dd></div>
            <div><dt>发送时屏蔽</dt><dd>{{ blockedMessageCount }} 段</dd></div>
            <div><dt>来源</dt><dd>{{ detail.peer_label }}</dd></div>
            <div><dt>会话 ID</dt><dd>{{ detail.peer_id || '未知' }}</dd></div>
            <div><dt>消息 ID</dt><dd>{{ detail.source_message_id }}</dd></div>
            <div><dt>保存时间</dt><dd>{{ formatTime(detail.updated_at) }}</dd></div>
          </dl>
          <details v-if="detail.source_locations?.length" class="chat-source-locations">
            <summary>消息位置（{{ detail.source_locations.length }}）</summary>
            <div class="chat-source-location-list">
              <div v-for="location in detail.source_locations" :key="`${location.source_self_id}:${location.source_type}:${location.source_peer_id}:${location.source_real_seq}`">
                <span>{{ location.source_type === 'group' ? '群聊' : '私聊' }} {{ location.source_peer_id }}</span>
                <span>账号 {{ location.source_self_id }} · 序号 {{ location.source_real_seq }}</span>
              </div>
            </div>
          </details>
          <section class="chat-management" aria-label="聊天记录分类管理">
            <div class="chat-record-identity">
              <label class="chat-field">
                <span>名称</span>
                <input
                  v-model="detail.name"
                  class="chat-search-input"
                  type="text"
                  maxlength="200"
                  autocomplete="off"
                  :disabled="saving"
                >
              </label>
              <label class="chat-field">
                <span>文件名（媒体文件夹）</span>
                <input
                  v-model="detail.media_folder"
                  class="chat-search-input"
                  type="text"
                  maxlength="255"
                  autocomplete="off"
                  spellcheck="false"
                  :disabled="saving"
                >
              </label>
              <p>保存时同步重命名实际媒体文件夹，并更新记录中的媒体路径。</p>
            </div>
            <p v-if="error" class="chat-management-error">{{ error }}</p>
            <div class="chat-field chat-picker-field">
              <div class="chat-field-label">
                <span>类别</span>
                <strong>{{ selectedCategoryLabel }}</strong>
              </div>
              <input v-model="categoryQuery" class="chat-search-input" type="search" placeholder="检索类别" autocomplete="off" spellcheck="false">
              <div class="chat-option-list" role="listbox" aria-label="类别列表">
                <button
                  type="button"
                  class="chat-option"
                  :class="{ selected: selectedCategoryId === null }"
                  :aria-selected="selectedCategoryId === null"
                  @click="selectCategory(null)"
                >
                  <span class="chat-option-mark" aria-hidden="true">{{ selectedCategoryId === null ? '✓' : '' }}</span>
                  <span>未分类</span>
                </button>
                <button
                  v-for="item in filteredCategoryOptions"
                  :key="item.id"
                  type="button"
                  class="chat-option"
                  :class="{ selected: selectedCategoryId === item.id }"
                  :aria-selected="selectedCategoryId === item.id"
                  @click="selectCategory(item.id)"
                >
                  <span class="chat-option-mark" aria-hidden="true">{{ selectedCategoryId === item.id ? '✓' : '' }}</span>
                  <span>{{ item.label }}</span>
                </button>
                <span v-if="filteredCategoryOptions.length === 0" class="chat-picker-empty">没有匹配的类别</span>
              </div>
            </div>
            <button class="chat-create-link" type="button" @click="emit('requestCreateCategory')">＋ 新建类别</button>

            <div class="chat-field chat-picker-field">
              <div class="chat-field-label">
                <span>分组</span>
                <strong>{{ selectedCollectionIds.length }} 个已选</strong>
              </div>
              <input v-model="collectionQuery" class="chat-search-input" type="search" placeholder="检索分组" autocomplete="off" spellcheck="false">
              <div v-if="selectedCollectionItems.length" class="chat-selection-summary" aria-label="已选分组">
                <button
                  v-for="item in selectedCollectionItems"
                  :key="`selected-${item.id}`"
                  type="button"
                  class="chat-selection-chip"
                  :title="`移除分组：${item.path || item.name}`"
                  @click="toggleCollection(item.id)"
                >{{ item.path || item.name }} ×</button>
              </div>
              <div class="chat-option-list chat-collection-list" role="group" aria-label="分组列表">
                <label
                  v-for="item in filteredCollectionOptions"
                  :key="item.id"
                  class="chat-option"
                  :class="{ selected: isCollectionSelected(item.id) }"
                >
                  <input type="checkbox" :checked="isCollectionSelected(item.id)" @change="toggleCollection(item.id)">
                  <span class="chat-option-mark" aria-hidden="true">{{ isCollectionSelected(item.id) ? '✓' : '' }}</span>
                  <span>{{ item.path || item.name }}</span>
                </label>
                <span v-if="filteredCollectionOptions.length === 0" class="chat-picker-empty">没有匹配的分组</span>
              </div>
            </div>
            <button class="chat-create-link" type="button" @click="emit('requestCreateCollection')">＋ 新建分组</button>

            <div class="chat-field chat-picker-field">
              <div class="chat-field-label">
                <span>标签</span>
                <strong>{{ selectedTags.length }} 个已选</strong>
              </div>
              <input v-model="tagQuery" class="chat-search-input" type="search" placeholder="检索标签名称或种类" autocomplete="off" spellcheck="false">
              <div class="chat-selected-tags">
                <button v-for="tag in selectedTags" :key="tagKey(tag)" class="chat-tag selected" type="button" @click="removeTag(tag)">
                  {{ tag.category ? `${tag.category}: ` : '' }}{{ tag.name }} ×
                </button>
                <span v-if="selectedTags.length === 0" class="chat-empty-hint">未添加标签</span>
              </div>
              <div class="chat-tag-suggestions-list">
                <div v-for="group in filteredTagGroups" :key="group.category" class="chat-tag-suggestions">
                  <small>{{ group.category }}</small>
                  <button
                    v-for="tag in group.tags"
                    :key="tagKey(tag)"
                    class="chat-tag"
                    :class="{ selected: hasTag(tag) }"
                    type="button"
                    :aria-pressed="hasTag(tag)"
                    @click="toggleTag(tag)"
                  >{{ tag.name }}</button>
                </div>
                <span v-if="filteredTagGroups.length === 0" class="chat-picker-empty">
                  {{ tagQuery.trim() ? '没有匹配的标签' : '暂无标签' }}
                </span>
              </div>
              <div class="chat-tag-new">
                <input v-model="tagCategory" type="text" placeholder="标签种类">
                <input v-model="tagName" type="text" placeholder="新标签" @keydown="onNewTagKeydown">
                <button class="btn btn-sm" type="button" @click="addTag">添加</button>
              </div>
            </div>
          </section>
          <div class="chat-command-help">
            <span>在目标 QQ 会话中发送</span>
            <code>{{ detail.send_command }}</code>
            <button class="btn btn-primary" type="button" @click="copyCommand">复制发送命令</button>
            <button class="btn btn-danger" type="button" @click="requestDelete">删除聊天记录</button>
          </div>
        </aside>

        <section class="chat-message-list" aria-label="聊天记录内容">
          <div class="chat-filter-toolbar">
            <div>
              <strong>选择性发送</strong>
              <span>勾选并保存后，模式二、三、四会在组装时略过该段；模式一仍原样转发。</span>
            </div>
            <button
              v-if="blockedMessageCount"
              class="btn btn-sm"
              type="button"
              @click="clearBlockedMessages"
            >取消全部勾选</button>
          </div>
          <article
            v-for="(message, index) in detail.messages"
            :key="message.path || index"
            class="chat-message"
            :class="{ blocked: isMessageBlocked(message.path) }"
            :style="{ marginLeft: Math.min(message.depth, 4) * 18 + 'px' }"
          >
            <div class="chat-message-head">
              <div class="chat-message-identity">
                <strong>{{ message.sender }}</strong>
                <time v-if="message.time">{{ formatTime(message.time) }}</time>
              </div>
              <label
                class="chat-block-toggle"
                :class="{ inherited: Boolean(blockingAncestorPath(message.path)) }"
                :title="blockingAncestorPath(message.path) ? '该段已随上层记录屏蔽' : '仅影响模式二、三、四'"
              >
                <input
                  type="checkbox"
                  :checked="isMessageBlocked(message.path)"
                  :disabled="Boolean(blockingAncestorPath(message.path))"
                  @change="toggleMessageBlocked(message.path)"
                >
                <span class="chat-block-mark" aria-hidden="true">{{ isMessageBlocked(message.path) ? '✓' : '' }}</span>
                <span>{{ blockingAncestorPath(message.path) ? '随上层屏蔽' : '发送时屏蔽' }}</span>
              </label>
            </div>
            <p v-if="messagePreview(message)">{{ messagePreview(message) }}</p>
            <div
              v-for="(quote, quoteIndex) in message.quotes || []"
              :key="`${message.path}:quote:${quoteIndex}`"
              class="chat-quoted-message"
            >
              <div class="chat-quoted-head">
                <span>引用 {{ quote.sender }}</span>
                <time v-if="quote.time">{{ formatTime(quote.time) }}</time>
              </div>
              <p v-if="quotePreview(quote)">{{ quotePreview(quote) }}</p>
              <div v-if="quote.media?.length" class="chat-message-media" aria-label="被引用消息图片">
                <template v-for="media in quote.media" :key="mediaKey(media)">
                  <a
                    v-if="media.kind === 'image' && mediaVisible(media)"
                    class="chat-media-link"
                    :href="media.url"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <img
                      :src="media.url"
                      :alt="media.alt || '被引用的聊天记录图片'"
                      loading="lazy"
                      decoding="async"
                      @load="onMediaLoaded(media)"
                      @error="onMediaError(media)"
                    >
                  </a>
                  <video
                    v-else-if="media.kind === 'video' && mediaVisible(media)"
                    class="chat-media-video"
                    :src="media.url"
                    controls
                    preload="metadata"
                    @loadedmetadata="onMediaLoaded(media)"
                    @error="onMediaError(media)"
                  />
                </template>
              </div>
            </div>
            <div v-if="directMedia(message).length" class="chat-message-media" aria-label="消息图片">
              <template v-for="media in directMedia(message)" :key="mediaKey(media)">
                <a
                  v-if="media.kind === 'image' && mediaVisible(media)"
                  class="chat-media-link"
                  :href="media.url"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <img
                    :src="media.url"
                    :alt="media.alt || '聊天记录图片'"
                    loading="lazy"
                    decoding="async"
                    @load="onMediaLoaded(media)"
                    @error="onMediaError(media)"
                  >
                </a>
                <video
                  v-else-if="media.kind === 'video' && mediaVisible(media)"
                  class="chat-media-video"
                  :src="media.url"
                  controls
                  preload="metadata"
                  @loadedmetadata="onMediaLoaded(media)"
                  @error="onMediaError(media)"
                />
              </template>
            </div>
            <span v-if="message.depth" class="nested-label">第 {{ message.depth + 1 }} 层记录</span>
          </article>
          <div v-if="detail.messages.length === 0" class="chat-details-state">归档结构完整，但没有可预览的消息节点。</div>
        </section>
      </div>
      <div class="chat-management-actions">
        <span class="chat-shortcuts">Enter 下一项 · Ctrl+Enter 保存</span>
        <button class="btn btn-secondary" type="button" title="关闭详情，返回主页面（Esc）" :disabled="saving" @click="close">关闭</button>
        <button v-if="detail && !loading" class="btn btn-primary" type="button" title="保存更改（Ctrl+Enter / Ctrl+S）" :disabled="saving" @click="saveManagement">
          {{ saving ? '保存中…' : '保存更改' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-details-overlay {
  position: fixed;
  inset: 0;
  z-index: 245;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 28px;
  background: rgba(0, 0, 0, .58);
}

.chat-details-box {
  width: min(920px, 94vw);
  height: min(720px, 88vh);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
}

.chat-details-header {
  flex-shrink: 0;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 20px;
  border-bottom: 1px solid var(--border);
}

.chat-details-header h2 {
  margin: 3px 0 0;
  color: var(--fg);
  font-size: 18px;
}

.chat-details-kicker {
  color: var(--muted);
  font-size: 11px;
}

.chat-details-content {
  min-height: 0;
  flex: 1;
  display: grid;
  grid-template-columns: 250px minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr);
}

.chat-details-meta {
  overflow-y: auto;
  padding: 18px;
  border-right: 1px solid var(--border);
  background: var(--surface-2);
}

.chat-genre-pill {
  display: inline-flex;
  padding: 4px 9px;
  border-radius: 999px;
  color: var(--primary-strong);
  background: var(--primary-light);
  border: 1px solid var(--primary);
  font-size: 11px;
  font-weight: 600;
}

.chat-details-meta dl { margin: 16px 0; }
.chat-details-meta dl div { margin-bottom: 11px; }
.chat-details-meta dt { color: var(--muted); font-size: 10px; }
.chat-details-meta dd { margin: 2px 0 0; color: var(--fg); font-size: 12px; word-break: break-all; }

.chat-details-header h2,
.chat-details-meta dt,
.chat-details-meta dd,
.chat-source-location-list span,
.chat-command-help > span,
.chat-command-help code,
.chat-message-identity strong,
.chat-message-identity time,
.chat-quoted-head span,
.chat-quoted-head time,
.chat-message p,
.nested-label {
  -webkit-user-select: text;
  user-select: text;
  cursor: text;
}

.chat-management {
  display: flex;
  flex-direction: column;
  gap: 7px;
  margin: 16px 0;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}

.chat-management-actions {
  flex-shrink: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 20px;
  border-top: 1px solid var(--border);
}
.chat-shortcuts { margin-right: auto; color: var(--muted); font-size: 11px; }
.chat-record-identity { display: flex; flex-direction: column; gap: 7px; margin-bottom: 4px; }
.chat-record-identity p { margin: 0; color: var(--muted); font-size: 9px; line-height: 1.45; }
.chat-management-error { margin: 0; color: var(--danger); font-size: 10px; }
.chat-field { display: flex; flex-direction: column; gap: 4px; color: var(--muted); font-size: 10px; }
.chat-field-label { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.chat-field-label strong { max-width: 68%; overflow: hidden; color: var(--fg-secondary); font-size: 9px; font-weight: 500; text-align: right; text-overflow: ellipsis; white-space: nowrap; }
.chat-search-input, .chat-tag-new input {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  padding: 5px 6px;
  color: var(--fg);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 5px;
  font-size: 11px;
}
.chat-search-input::placeholder, .chat-tag-new input::placeholder { color: var(--muted); }
.chat-search-input:focus, .chat-tag-new input:focus { border-color: var(--primary); outline: none; }
.chat-option-list { max-height: 126px; overflow-y: auto; padding: 2px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); scrollbar-width: thin; }
.chat-collection-list { max-height: 148px; }
.chat-option { position: relative; display: flex; align-items: center; gap: 6px; width: 100%; box-sizing: border-box; min-height: 27px; padding: 4px 6px; color: var(--fg-secondary); background: transparent; border: 0; border-radius: 4px; font: inherit; font-size: 10px; text-align: left; cursor: pointer; }
.chat-option:hover { background: var(--surface-2); }
.chat-option.selected { color: var(--primary-strong); background: var(--primary-light); }
.chat-option input { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.chat-option-mark { display: inline-flex; flex: 0 0 14px; align-items: center; justify-content: center; width: 14px; height: 14px; box-sizing: border-box; color: var(--primary-strong); border: 1px solid var(--border); border-radius: 3px; font-size: 10px; line-height: 1; }
.chat-option.selected .chat-option-mark { border-color: var(--primary); background: var(--primary-light); }
.chat-option > span:last-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chat-picker-empty { display: block; padding: 8px 6px; color: var(--muted); font-size: 10px; text-align: center; }
.chat-selection-summary { display: flex; flex-wrap: wrap; gap: 4px; max-height: 44px; overflow-y: auto; padding: 1px 0; scrollbar-width: thin; }
.chat-selection-chip { max-width: 100%; overflow: hidden; padding: 3px 6px; color: var(--primary-strong); background: var(--primary-light); border: 1px solid var(--primary); border-radius: 999px; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
.chat-create-link { align-self: flex-start; padding: 0; color: var(--primary-strong); background: none; border: 0; font-size: 10px; cursor: pointer; }
.chat-selected-tags, .chat-tag-suggestions { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; }
.chat-selected-tags { max-height: 62px; overflow-y: auto; padding: 1px 0; scrollbar-width: thin; }
.chat-tag-suggestions-list { max-height: 170px; overflow-y: auto; padding: 2px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); scrollbar-width: thin; }
.chat-tag-suggestions { margin: 2px 0 6px; }
.chat-tag-suggestions small { width: 100%; color: var(--muted); font-size: 9px; }
.chat-tag { padding: 3px 6px; color: var(--fg-secondary); background: var(--surface); border: 1px solid var(--border); border-radius: 999px; font-size: 10px; cursor: pointer; }
.chat-tag.selected { color: var(--primary-strong); background: var(--primary-light); border-color: var(--primary); }
.chat-empty-hint { color: var(--muted); font-size: 10px; }
.chat-tag-new { display: grid; grid-template-columns: .8fr 1fr auto; gap: 4px; margin-top: 2px; }

.chat-command-help {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
  color: var(--fg-secondary);
  font-size: 11px;
}

.chat-command-help code {
  padding: 8px;
  overflow-wrap: anywhere;
  color: var(--fg);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
}

.chat-message-list {
  overflow-y: auto;
  padding: 18px 22px;
}

.chat-filter-toolbar {
  position: sticky;
  z-index: 2;
  top: -18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: -18px -22px 14px;
  padding: 12px 22px;
  color: var(--fg-secondary);
  background: color-mix(in srgb, var(--surface) 94%, transparent);
  border-bottom: 1px solid var(--border);
  backdrop-filter: blur(8px);
}
.chat-filter-toolbar > div { display: flex; flex-direction: column; gap: 2px; }
.chat-filter-toolbar strong { color: var(--fg); font-size: 12px; }
.chat-filter-toolbar span { font-size: 10px; line-height: 1.45; }

.chat-message {
  margin-bottom: 10px;
  padding: 10px 12px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 9px;
}
.chat-message.blocked { opacity: .62; border-style: dashed; }
.chat-message.blocked .chat-message-media { filter: grayscale(1); }

.chat-message-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  color: var(--fg);
  font-size: 12px;
}

.chat-message-identity { display: flex; min-width: 0; flex-direction: column; gap: 2px; }
.chat-message-head time { color: var(--muted); font-size: 10px; }
.chat-block-toggle {
  position: relative;
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 5px;
  color: var(--fg-secondary);
  font-size: 10px;
  cursor: pointer;
  user-select: none;
}
.chat-block-toggle.inherited { color: var(--muted); cursor: default; }
.chat-block-toggle input { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.chat-block-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 15px;
  height: 15px;
  box-sizing: border-box;
  color: var(--danger);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 4px;
  font-size: 10px;
  line-height: 1;
}
.chat-block-toggle:hover .chat-block-mark { border-color: var(--danger); }
.chat-block-toggle input:checked + .chat-block-mark { border-color: var(--danger); background: color-mix(in srgb, var(--danger) 12%, var(--surface)); }
.chat-block-toggle input:focus-visible + .chat-block-mark { outline: 2px solid var(--primary); outline-offset: 2px; }
.chat-message p { margin: 6px 0 0; color: var(--fg-secondary); font-size: 12px; line-height: 1.55; white-space: pre-wrap; }
.chat-quoted-message { margin-top: 8px; padding: 8px 10px; background: var(--surface); border-left: 3px solid var(--border); border-radius: 5px; }
.chat-quoted-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--muted); font-size: 10px; }
.chat-quoted-head time { font-size: 9px; }
.chat-quoted-message p { margin-top: 4px; font-size: 11px; }
.chat-message-media { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.chat-media-link { display: block; max-width: 100%; cursor: zoom-in; }
.chat-media-link img { display: block; max-width: min(100%, 420px); max-height: 300px; object-fit: contain; border: 1px solid var(--border); border-radius: 7px; background: var(--surface); }
.chat-media-video { display: block; width: min(100%, 420px); max-height: 300px; border: 1px solid var(--border); border-radius: 7px; background: #000; }
.nested-label { display: inline-block; margin-top: 6px; color: var(--primary-strong); font-size: 9px; }
.chat-source-locations { margin: 12px 0; color: var(--fg-secondary); font-size: 11px; }
.chat-source-locations summary { cursor: pointer; }
.chat-source-location-list { max-height: 160px; overflow-y: auto; margin-top: 8px; }
.chat-source-location-list > div { padding: 6px 0; border-bottom: 1px solid var(--border); }
.chat-source-location-list span { display: block; overflow-wrap: anywhere; }
.chat-source-location-list span + span { margin-top: 3px; color: var(--muted); font-size: 10px; }
.chat-details-state { margin: auto; padding: 24px; color: var(--fg-secondary); }
.chat-details-state.error { color: var(--danger); }

@media (max-width: 720px) {
  .chat-details-content { grid-template-columns: 1fr; grid-template-rows: minmax(0, 230px) minmax(0, 1fr); }
  .chat-details-meta { max-height: 230px; border-right: 0; border-bottom: 1px solid var(--border); }
}
</style>
