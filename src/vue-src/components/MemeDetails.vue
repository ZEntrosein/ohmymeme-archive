<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import type { Meme, TagRecord } from '../types'
import { api, createBackdropClickGuard, focusNextDetailInput, handleDetailSaveShortcut, rememberFocus, restoreFocus, trapTabFocus } from '../utils/api'

interface MemeDetail extends Meme {
  file_size?: number
  compressed_only?: boolean
  created_at?: string
  updated_at?: string
  manual_description: string
  manual_ocr_text: string
  ai_description: string
  ai_ocr_text: string
  tags: TagRecord[]
  all_tags: TagRecord[]
  collections: CollectionDetail[]
  all_collections: CollectionDetail[]
  category: CategoryDetail | null
  all_categories: CategoryDetail[]
  previous_id: number | null
  next_id: number | null
  position: number
  total: number
}

interface CollectionDetail {
  id: number
  name: string
  path: string
}

interface CategoryDetail {
  id: number
  name: string
  path: string
  count?: number
  children?: CategoryDetail[]
}

const emit = defineEmits<{
  saved: [memeId: number]
  'tag-renamed': [oldName: string, newName: string]
  'structure-created': []
}>()

const visible = ref(false)
const backdrop = createBackdropClickGuard(close)
const loading = ref(false)
const saving = ref(false)
const loadError = ref('')
const saveError = ref('')
const dirty = ref(false)
const collectionInput = ref('')
const collectionDropdownOpen = ref(false)
const activeCollectionSuggestion = ref(-1)
const collectionCreateOpen = ref(false)
const collectionCreateName = ref('')
const collectionCreateParentId = ref(0)
const creatingCollection = ref(false)
const categoryInput = ref('')
const categoryDropdownOpen = ref(false)
const activeCategorySuggestion = ref(-1)
const categoryCreateOpen = ref(false)
const categoryCreateName = ref('')
const categoryCreateParentId = ref(0)
const creatingCategory = ref(false)
const tagInput = ref('')
const tagCategoryInput = ref('')
const tagDropdownOpen = ref(false)
const activeSuggestion = ref(-1)
const renameSource = ref<TagRecord | null>(null)
const renameValue = ref('')
const renameCategory = ref('')
const renaming = ref(false)
const detail = ref<MemeDetail | null>(null)
const previewFilename = ref('')
const boxEl = ref<HTMLElement>()
const contentEl = ref<HTMLElement>()
const infoEl = ref<HTMLElement>()
const closeEl = ref<HTMLButtonElement>()
const renameInputEl = ref<HTMLInputElement>()
let requestGeneration = 0

const previewUrl = computed(() => {
  if (!detail.value || !previewFilename.value) return ''
  return `/api/original/${detail.value.id}/${encodeURIComponent(previewFilename.value)}`
})

function tagKey(tag: TagRecord) {
  return `${tag.category || ''}\u001f${tag.name}`.toLocaleLowerCase()
}

function normalizeTagRecords(records: unknown, legacy: unknown): TagRecord[] {
  if (Array.isArray(records)) {
    return records.filter((tag: unknown): tag is TagRecord => (
      typeof tag === 'object' && tag !== null
      && typeof (tag as TagRecord).name === 'string'
      && typeof (tag as TagRecord).category === 'string'
    ))
  }
  if (!Array.isArray(legacy)) return []
  return legacy
    .filter((tag: unknown): tag is string => typeof tag === 'string' && !!tag.trim())
    .map(name => ({ name, category: '' }))
}

const formatName = computed(() => {
  const mime = detail.value?.mime_type || ''
  if (mime.startsWith('image/')) return mime.slice(6).toUpperCase()
  return mime || '未知'
})

function formatFileSize(value?: number) {
  const bytes = Number(value)
  if (!Number.isFinite(bytes) || bytes < 0) return '未知'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

const tagSuggestions = computed(() => {
  if (!detail.value) return []
  const selected = new Set(detail.value.tags.map(tagKey))
  const query = tagInput.value.trim().toLocaleLowerCase()
  return detail.value.all_tags.filter(tag => (
    !selected.has(tagKey(tag))
    && (!query || tag.name.toLocaleLowerCase().includes(query)
      || tag.category.toLocaleLowerCase().includes(query))
  ))
})

const collectionSuggestions = computed(() => {
  if (!detail.value) return []
  const selected = new Set(detail.value.collections.map(item => item.id))
  const query = collectionInput.value.trim().toLocaleLowerCase()
  return detail.value.all_collections.filter(item => (
    !selected.has(item.id)
    && (!query || item.path.toLocaleLowerCase().includes(query))
  ))
})

const canCreateCollection = computed(() => {
  if (!detail.value) return false
  const query = collectionInput.value.trim().toLocaleLowerCase()
  return !!query && !detail.value.all_collections.some(
    item => item.name.trim().toLocaleLowerCase() === query,
  )
})

const collectionParentOptions = computed(() => (
  detail.value?.all_collections.filter(item => item.path === item.name) || []
))

const categoryOptions = computed(() => {
  if (!detail.value) return []
  const out: CategoryDetail[] = []
  const walk = (items: CategoryDetail[]) => {
    for (const item of items) {
      if (item.id > 0) out.push(item)
      if (item.children?.length) walk(item.children)
    }
  }
  walk(detail.value.all_categories || [])
  return out
})

const categorySuggestions = computed(() => {
  const query = categoryInput.value.trim().toLocaleLowerCase()
  const current = detail.value?.category?.id
  return categoryOptions.value.filter(item => (
    item.id > 0
    && item.id !== current
    && (!query || item.name.toLocaleLowerCase().includes(query)
      || item.path.toLocaleLowerCase().includes(query))
  ))
})

const canCreateCategory = computed(() => {
  const query = categoryInput.value.trim().toLocaleLowerCase()
  return !!query && !categoryOptions.value.some(
    item => item.name.trim().toLocaleLowerCase() === query,
  )
})

// 载入一张表情的详情；浏览切图时保留弹窗与焦点。
async function loadDetails(memeId: number, fallback?: Meme) {
  const generation = ++requestGeneration
  loadError.value = ''
  saveError.value = ''
  loading.value = true
  collectionInput.value = ''
  collectionDropdownOpen.value = false
  activeCollectionSuggestion.value = -1
  resetStructureCreateForms()
  categoryInput.value = ''
  categoryDropdownOpen.value = false
  activeCategorySuggestion.value = -1
  tagInput.value = ''
  tagCategoryInput.value = ''
  tagDropdownOpen.value = false
  activeSuggestion.value = -1
  renameSource.value = null
  renameValue.value = ''
  renameCategory.value = ''
  if (fallback) {
    previewFilename.value = fallback.filename
    detail.value = {
      ...fallback,
      manual_description: '',
      manual_ocr_text: '',
      ai_description: '',
      ai_ocr_text: '',
      tags: [],
      all_tags: [],
      collections: [],
      all_collections: [],
      category: null,
      all_categories: [],
      previous_id: null,
      next_id: null,
      position: 1,
      total: 1,
    }
  }
  try {
    const result = await api('get_meme_details', memeId)
    if (generation !== requestGeneration || !visible.value) return false
    if (!result || Number(result.id) !== memeId || !Array.isArray(result.tags)) {
      throw new Error('invalid details')
    }
    detail.value = {
      ...(detail.value || fallback),
      ...result,
      manual_description: typeof result.manual_description === 'string'
        ? result.manual_description : '',
      manual_ocr_text: typeof result.manual_ocr_text === 'string'
        ? result.manual_ocr_text : '',
      ai_description: typeof result.ai_description === 'string'
        ? result.ai_description : '',
      ai_ocr_text: typeof result.ai_ocr_text === 'string'
        ? result.ai_ocr_text : '',
      tags: normalizeTagRecords(result.tag_records, result.tags),
      all_tags: normalizeTagRecords(result.all_tag_records, result.all_tags),
      collections: Array.isArray(result.collections)
        ? result.collections.filter(
          (item: unknown): item is CollectionDetail => (
            typeof item === 'object' && item !== null
            && Number.isInteger((item as CollectionDetail).id)
            && typeof (item as CollectionDetail).path === 'string'
          ),
        )
        : [],
      all_collections: Array.isArray(result.all_collections)
        ? result.all_collections.filter(
          (item: unknown): item is CollectionDetail => (
            typeof item === 'object' && item !== null
            && Number.isInteger((item as CollectionDetail).id)
            && typeof (item as CollectionDetail).path === 'string'
          ),
        )
        : [],
      category: result.category && Number.isInteger(result.category.id)
        ? result.category as CategoryDetail : null,
      all_categories: Array.isArray(result.all_categories)
        ? result.all_categories as CategoryDetail[] : [],
      previous_id: Number.isInteger(result.previous_id) ? result.previous_id : null,
      next_id: Number.isInteger(result.next_id) ? result.next_id : null,
      position: Number(result.position) || 1,
      total: Number(result.total) || 1,
    } as MemeDetail
    previewFilename.value = detail.value.filename
    dirty.value = false
    return true
  } catch (_) {
    if (generation === requestGeneration && visible.value) loadError.value = '详情加载失败'
    return false
  } finally {
    if (generation === requestGeneration) loading.value = false
  }
}

// 打开详情，并按需加载全部可编辑字段。
async function open(target: Meme) {
  backdrop.reset()
  rememberFocus()
  visible.value = true
  dirty.value = false
  await loadDetails(target.id, target)
  await nextTick()
  // 首次打开详情时从信息栏及窄窗口内容顶部开始；切图走 navigate/loadDetails，
  // 不会经过这里，因此会保留用户当前的滚动位置。
  closeEl.value?.focus({ preventScroll: true })
  if (contentEl.value) contentEl.value.scrollTop = 0
  if (infoEl.value) infoEl.value.scrollTop = 0
}

function markDirty() {
  dirty.value = true
  saveError.value = ''
}

function resetStructureCreateForms() {
  collectionCreateOpen.value = false
  collectionCreateName.value = ''
  collectionCreateParentId.value = 0
  creatingCollection.value = false
  categoryCreateOpen.value = false
  categoryCreateName.value = ''
  categoryCreateParentId.value = 0
  creatingCategory.value = false
}

function addCollection(item: CollectionDetail) {
  if (!detail.value || detail.value.collections.some(current => current.id === item.id)) return
  detail.value.collections.push(item)
  detail.value.collections.sort((a, b) => a.path.localeCompare(b.path, 'zh-CN'))
  collectionInput.value = ''
  collectionDropdownOpen.value = false
  activeCollectionSuggestion.value = -1
  markDirty()
}

function removeCollection(index: number) {
  if (!detail.value) return
  detail.value.collections.splice(index, 1)
  markDirty()
}

function beginCollectionCreate() {
  const name = collectionInput.value.trim()
  if (!name) return
  collectionCreateName.value = name
  collectionCreateParentId.value = 0
  collectionCreateOpen.value = true
  collectionDropdownOpen.value = false
  activeCollectionSuggestion.value = -1
}

function cancelCollectionCreate() {
  if (creatingCollection.value) return
  collectionCreateOpen.value = false
  collectionCreateName.value = ''
  collectionCreateParentId.value = 0
}

async function confirmCollectionCreate() {
  if (!detail.value || creatingCollection.value) return
  const name = collectionCreateName.value.trim()
  if (!name) return
  creatingCollection.value = true
  saveError.value = ''
  try {
    const result = await api(
      'create_collection',
      name,
      collectionCreateParentId.value || null,
    )
    if (!result?.ok || !result.collection) {
      saveError.value = result?.error || '创建分组失败，请重试'
      return
    }
    if (Array.isArray(result.all_collections)) {
      detail.value.all_collections = result.all_collections
    }
    addCollection(result.collection as CollectionDetail)
    collectionCreateOpen.value = false
    collectionCreateName.value = ''
    collectionCreateParentId.value = 0
    emit('structure-created')
  } catch (_) {
    saveError.value = '创建分组失败，请重试'
  } finally {
    creatingCollection.value = false
  }
}

function openCategoryDropdown() {
  collectionDropdownOpen.value = false
  tagDropdownOpen.value = false
  categoryDropdownOpen.value = true
  activeCategorySuggestion.value = -1
}

function onCategoryInput() {
  categoryDropdownOpen.value = true
  activeCategorySuggestion.value = -1
}

function selectCategory(item: CategoryDetail) {
  if (!detail.value || item.id <= 0) return
  detail.value.category = item
  categoryInput.value = ''
  categoryDropdownOpen.value = false
  activeCategorySuggestion.value = -1
  markDirty()
}

function clearCategory() {
  if (!detail.value?.category) return
  detail.value.category = null
  categoryInput.value = ''
  categoryDropdownOpen.value = false
  markDirty()
}

function findCategoryById(items: CategoryDetail[], id: number): CategoryDetail | null {
  for (const item of items) {
    if (item.id === id) return item
    const child = findCategoryById(item.children || [], id)
    if (child) return child
  }
  return null
}

function beginCategoryCreate() {
  const name = categoryInput.value.trim()
  if (!name) return
  categoryCreateName.value = name
  categoryCreateParentId.value = 0
  categoryCreateOpen.value = true
  categoryDropdownOpen.value = false
  activeCategorySuggestion.value = -1
}

function cancelCategoryCreate() {
  if (creatingCategory.value) return
  categoryCreateOpen.value = false
  categoryCreateName.value = ''
  categoryCreateParentId.value = 0
}

async function confirmCategoryCreate() {
  if (!detail.value || creatingCategory.value) return
  const name = categoryCreateName.value.trim()
  if (!name) return
  creatingCategory.value = true
  saveError.value = ''
  try {
    const result = await api(
      'create_category',
      name,
      categoryCreateParentId.value || null,
    )
    if (!result?.ok || !Number.isInteger(result.id)) {
      saveError.value = result?.error || '创建类别失败，请重试'
      return
    }
    if (Array.isArray(result.categories)) {
      detail.value.all_categories = result.categories
    }
    const created = findCategoryById(detail.value.all_categories, Number(result.id))
    if (!created) {
      saveError.value = '类别已创建，但刷新候选项失败'
      return
    }
    selectCategory(created)
    categoryCreateOpen.value = false
    categoryCreateName.value = ''
    categoryCreateParentId.value = 0
    emit('structure-created')
  } catch (_) {
    saveError.value = '创建类别失败，请重试'
  } finally {
    creatingCategory.value = false
  }
}

function onCategoryInputKeydown(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) return
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault()
    categoryDropdownOpen.value = true
    const count = categorySuggestions.value.length + (canCreateCategory.value ? 1 : 0)
    if (!count) return
    const step = e.key === 'ArrowDown' ? 1 : -1
    activeCategorySuggestion.value = activeCategorySuggestion.value < 0
      ? (step > 0 ? 0 : count - 1)
      : (activeCategorySuggestion.value + step + count) % count
  } else if (e.key === 'Enter') {
    const item = categorySuggestions.value[activeCategorySuggestion.value]
    if (item || canCreateCategory.value) {
      e.preventDefault()
      if (item) selectCategory(item)
      else beginCategoryCreate()
    }
  } else if (e.key === 'Escape' && categoryDropdownOpen.value) {
    e.preventDefault()
    e.stopPropagation()
    categoryDropdownOpen.value = false
    activeCategorySuggestion.value = -1
  }
}

function openCollectionDropdown() {
  tagDropdownOpen.value = false
  collectionDropdownOpen.value = true
  activeCollectionSuggestion.value = -1
}

function onCollectionInput() {
  collectionDropdownOpen.value = true
  activeCollectionSuggestion.value = -1
}

async function moveActiveCollectionSuggestion(step: number) {
  const count = collectionSuggestions.value.length + (canCreateCollection.value ? 1 : 0)
  if (!count) return
  activeCollectionSuggestion.value = activeCollectionSuggestion.value < 0
    ? (step > 0 ? 0 : count - 1)
    : (activeCollectionSuggestion.value + step + count) % count
  await nextTick()
  document.getElementById(
    `details-collection-option-${activeCollectionSuggestion.value}`,
  )?.scrollIntoView({ block: 'nearest' })
}

function onCollectionInputKeydown(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) return
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    collectionDropdownOpen.value = true
    void moveActiveCollectionSuggestion(1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    collectionDropdownOpen.value = true
    void moveActiveCollectionSuggestion(-1)
  } else if (e.key === 'Enter') {
    const suggestion = collectionSuggestions.value[activeCollectionSuggestion.value]
    if (suggestion || canCreateCollection.value) {
      e.preventDefault()
      if (suggestion) addCollection(suggestion)
      else beginCollectionCreate()
    }
  } else if (e.key === 'Escape' && collectionDropdownOpen.value) {
    e.preventDefault()
    e.stopPropagation()
    collectionDropdownOpen.value = false
    activeCollectionSuggestion.value = -1
  }
}

function addTag() {
  if (!detail.value) return
  const name = tagInput.value.trim()
  if (!name) return
  const category = tagCategoryInput.value.trim()
  const canonicalTag = detail.value.all_tags.find(
    current => current.name.toLocaleLowerCase() === name.toLocaleLowerCase()
      && current.category.toLocaleLowerCase() === category.toLocaleLowerCase(),
  ) || { name, category }
  const exists = detail.value.tags.some(
    current => tagKey(current) === tagKey(canonicalTag),
  )
  if (!exists) {
    detail.value.tags.push(canonicalTag)
    markDirty()
  }
  tagInput.value = ''
  tagCategoryInput.value = ''
  tagDropdownOpen.value = false
  activeSuggestion.value = -1
}

function selectTag(tag: TagRecord) {
  if (!detail.value || detail.value.tags.some(item => tagKey(item) === tagKey(tag))) return
  detail.value.tags.push(tag)
  tagInput.value = ''
  tagCategoryInput.value = ''
  tagDropdownOpen.value = false
  activeSuggestion.value = -1
  markDirty()
}

function openTagDropdown() {
  collectionDropdownOpen.value = false
  tagDropdownOpen.value = true
  activeSuggestion.value = -1
}

function onTagInput() {
  tagDropdownOpen.value = true
  activeSuggestion.value = -1
}

async function moveActiveSuggestion(step: number) {
  if (!tagSuggestions.value.length) return
  const count = tagSuggestions.value.length
  activeSuggestion.value = activeSuggestion.value < 0
    ? (step > 0 ? 0 : count - 1)
    : (activeSuggestion.value + step + count) % count
  await nextTick()
  document.getElementById(`details-tag-option-${activeSuggestion.value}`)?.scrollIntoView({
    block: 'nearest',
  })
}

function onTagInputKeydown(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) return
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    tagDropdownOpen.value = true
    void moveActiveSuggestion(1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    tagDropdownOpen.value = true
    void moveActiveSuggestion(-1)
  } else if (e.key === 'Enter') {
    const suggestion = tagSuggestions.value[activeSuggestion.value]
    if ((tagDropdownOpen.value && suggestion) || tagInput.value.trim()) {
      e.preventDefault()
      if (tagDropdownOpen.value && suggestion) selectTag(suggestion)
      else addTag()
    }
  } else if (e.key === 'Escape' && tagDropdownOpen.value) {
    e.preventDefault()
    e.stopPropagation()
    tagDropdownOpen.value = false
    activeSuggestion.value = -1
  }
}

function removeTag(index: number) {
  if (!detail.value) return
  detail.value.tags.splice(index, 1)
  markDirty()
}

function isPersistedTag(tag: TagRecord) {
  return detail.value?.all_tags.some(
    current => tagKey(current) === tagKey(tag),
  ) === true
}

async function beginTagRename(tag: TagRecord) {
  if (loading.value || saving.value || renaming.value) return
  tagDropdownOpen.value = false
  renameSource.value = tag
  renameValue.value = tag.name
  renameCategory.value = tag.category
  await nextTick()
  renameInputEl.value?.focus()
  renameInputEl.value?.select()
}

function cancelTagRename() {
  if (renaming.value) return
  renameSource.value = null
  renameValue.value = ''
  renameCategory.value = ''
}

function replaceTag(
  tags: TagRecord[],
  source: TagRecord,
  newName: string,
  newCategory: string,
) {
  const oldKey = tagKey(source)
  const seen = new Set<string>()
  return tags.reduce<TagRecord[]>((result, tag) => {
    const value = tagKey(tag) === oldKey
      ? { ...tag, name: newName, category: newCategory }
      : tag
    const key = tagKey(value)
    if (!seen.has(key)) {
      seen.add(key)
      result.push(value)
    }
    return result
  }, [])
}

// 标签名称和种类编辑是全局操作；目标已存在时由后端合并关联。
async function confirmTagRename() {
  if (!detail.value || renaming.value) return
  const source = renameSource.value
  const oldName = source?.name || ''
  const newName = renameValue.value.trim()
  const newCategory = renameCategory.value.trim()
  if (!oldName || !newName) {
    saveError.value = '标签名称不能为空'
    return
  }
  if (oldName === newName && (source?.category || '') === newCategory) {
    cancelTagRename()
    return
  }
  renaming.value = true
  saveError.value = ''
  try {
    const result = await api(
      'rename_tag',
      oldName,
      newName,
      source?.category || '',
      newCategory,
    )
    if (!result?.ok || typeof result.name !== 'string') {
      saveError.value = result?.error || '标签编辑失败，请重试'
      return
    }
    const finalName = result.name
    const finalCategory = typeof result.category === 'string' ? result.category : newCategory
    detail.value.tags = replaceTag(detail.value.tags, source!, finalName, finalCategory)
    detail.value.all_tags = Array.isArray(result.all_tag_records)
      ? normalizeTagRecords(result.all_tag_records, [])
      : replaceTag(detail.value.all_tags, source!, finalName, finalCategory)
    tagInput.value = ''
    renameSource.value = null
    renameValue.value = ''
    renameCategory.value = ''
    emit('tag-renamed', oldName, finalName)
  } catch (_) {
    saveError.value = '标签编辑失败，请重试'
  } finally {
    renaming.value = false
  }
}

// 保存当前编辑内容；失败时切图流程留在当前图片。
async function saveChanges() {
  if (!detail.value || !dirty.value) return true
  if (
    saving.value || loading.value || renaming.value
    || creatingCollection.value || creatingCategory.value
  ) return false
  const current = detail.value
  saving.value = true
  saveError.value = ''
  try {
    const result = await api('update_meme_details', current.id, {
      title: current.name,
      filename: current.filename,
      collection_ids: current.collections.map(item => item.id),
      category_id: current.category?.id ?? null,
      tags: current.tags.slice(),
      manual_description: current.manual_description,
      manual_ocr_text: current.manual_ocr_text,
      ai_description: current.ai_description,
      ai_ocr_text: current.ai_ocr_text,
    })
    if (!result?.ok) {
      saveError.value = result?.error || '保存失败，请重试'
      return false
    }
    if (Array.isArray(result.all_tag_records)) {
      current.all_tags = normalizeTagRecords(result.all_tag_records, [])
    }
    if (Array.isArray(result.collections)) {
      current.collections = result.collections
    }
    if (Array.isArray(result.all_collections)) {
      current.all_collections = result.all_collections
    }
    current.category = result.category && Number.isInteger(result.category.id)
      ? result.category : null
    if (Array.isArray(result.all_categories)) current.all_categories = result.all_categories
    if (typeof result.title === 'string') current.name = result.title
    if (typeof result.filename === 'string') {
      current.filename = result.filename
      previewFilename.value = result.filename
    }
    dirty.value = false
    emit('saved', current.id)
    return true
  } catch (_) {
    saveError.value = '保存失败，请重试'
    return false
  } finally {
    saving.value = false
  }
}

async function navigate(memeId: number | null) {
  if (
    !memeId || loading.value || saving.value || renaming.value
    || creatingCollection.value || creatingCategory.value
  ) return
  if (!(await saveChanges())) return
  await loadDetails(memeId)
}

// 关闭始终放弃尚未保存的编辑；保存只由明确的保存操作触发。
function close() {
  if (
    saving.value || renaming.value
    || creatingCollection.value || creatingCategory.value
  ) return
  backdrop.reset()
  requestGeneration++
  loading.value = false
  dirty.value = false
  saveError.value = ''
  resetStructureCreateForms()
  visible.value = false
  restoreFocus()
}

function isTextEditing(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

function onKeydown(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) return
  if (e.key === 'Escape') {
    e.preventDefault()
    e.stopPropagation()
    if (!e.repeat) close()
    return
  }
  if (boxEl.value && focusNextDetailInput(boxEl.value, e)) return
  if (!isTextEditing(e.target) && e.key === 'ArrowLeft') {
    e.preventDefault()
    void navigate(detail.value?.previous_id || null)
  } else if (!isTextEditing(e.target) && e.key === 'ArrowRight') {
    e.preventDefault()
    void navigate(detail.value?.next_id || null)
  } else if (boxEl.value) {
    trapTabFocus(boxEl.value, e)
  }
}

defineExpose({ open, close, isOpen: () => visible.value })
</script>

<template>
  <div
    v-if="visible"
    class="details-overlay"
    @pointerdown.capture="backdrop.onPointerDown"
    @pointerup.capture="backdrop.onPointerUp"
    @pointercancel.capture="backdrop.reset"
    @click="backdrop.onClick"
  >
    <div
      v-if="detail"
      ref="boxEl"
      class="details-box"
      role="dialog"
      aria-modal="true"
      aria-labelledby="details-title"
      @keydown.capture="handleDetailSaveShortcut($event, saveChanges)"
      @keydown="onKeydown"
    >
      <div class="details-heading">
        <div id="details-title" class="details-title">表情包详情</div>
        <div class="details-position" aria-live="polite">{{ detail.position }} / {{ detail.total }}</div>
      </div>

      <div ref="contentEl" class="details-content">
        <div class="details-preview-pane">
          <img :src="previewUrl" :alt="detail.name" class="details-preview">
          <div v-if="loading" class="details-preview-loading">正在加载...</div>
          <button
            class="details-nav details-nav-prev"
            type="button"
            :disabled="!detail.previous_id || loading || saving || renaming"
            aria-label="上一张"
            title="上一张（←）"
            @click="navigate(detail.previous_id)"
          >‹</button>
          <button
            class="details-nav details-nav-next"
            type="button"
            :disabled="!detail.next_id || loading || saving || renaming"
            aria-label="下一张"
            title="下一张（→）"
            @click="navigate(detail.next_id)"
          >›</button>
        </div>

        <div ref="infoEl" class="details-info">
          <div class="details-identity">
            <label class="details-field">
              <span>标题</span>
              <input
                v-model="detail.name"
                type="text"
                maxlength="200"
                autocomplete="off"
                :disabled="loading || saving || renaming"
                @input="markDirty"
              >
            </label>
            <label class="details-field">
              <span>文件名</span>
              <input
                v-model="detail.filename"
                type="text"
                maxlength="255"
                autocomplete="off"
                spellcheck="false"
                :disabled="loading || saving || renaming"
                @input="markDirty"
              >
            </label>
            <div class="details-filename-hint">保存时同步重命名实际文件；文件扩展名不能修改。</div>
          </div>
          <dl class="details-meta">
            <div>
              <dt>尺寸</dt>
              <dd>{{ detail.width && detail.height ? `${detail.width} × ${detail.height}` : '未知' }}</dd>
            </div>
            <div>
              <dt>格式</dt>
              <dd>{{ formatName }}</dd>
            </div>
            <div>
              <dt>大小</dt>
              <dd>{{ formatFileSize(detail.file_size) }}</dd>
            </div>
            <div v-if="detail.compressed_only">
              <dt>归档</dt>
              <dd>仅压缩版（无原件）</dd>
            </div>
          </dl>

          <div v-if="loadError" class="details-error" role="alert">{{ loadError }}</div>
          <div v-if="saveError" class="details-error" role="alert">{{ saveError }}</div>

          <fieldset class="details-section" :disabled="loading || saving || renaming || creatingCategory">
            <legend class="details-section-title">
              <span>类别</span>
            </legend>
            <div class="details-category-current" aria-live="polite">
              <span v-if="detail.category" class="details-group" :title="detail.category.path">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" /></svg>
                <span>{{ detail.category.path }}</span>
                <button type="button" aria-label="移除类别" title="移除类别" @click="clearCategory">×</button>
              </span>
              <span v-else class="details-empty">未分类</span>
            </div>
            <div class="details-collection-entry">
              <input
                v-model="categoryInput"
                type="text"
                role="combobox"
                aria-autocomplete="list"
                aria-controls="details-category-dropdown"
                :aria-expanded="categoryDropdownOpen"
                :aria-activedescendant="activeCategorySuggestion >= 0 ? `details-category-option-${activeCategorySuggestion}` : undefined"
                placeholder="搜索已有类别或输入新名称"
                autocomplete="off"
                spellcheck="false"
                @focus="openCategoryDropdown"
                @input="onCategoryInput"
                @blur="categoryDropdownOpen = false"
                @keydown="onCategoryInputKeydown"
              >
              <div
                v-if="categoryDropdownOpen"
                id="details-category-dropdown"
                class="details-collection-dropdown"
                role="listbox"
              >
                <div class="details-tag-dropdown-title">已有类别</div>
                <button
                  v-for="(category, index) in categorySuggestions"
                  :id="`details-category-option-${index}`"
                  :key="category.id"
                  type="button"
                  class="details-collection-option"
                  :class="{ active: activeCategorySuggestion === index }"
                  role="option"
                  :aria-selected="activeCategorySuggestion === index"
                  tabindex="-1"
                  @mouseenter="activeCategorySuggestion = index"
                  @mousedown.prevent="selectCategory(category)"
                >{{ category.path }}</button>
                <button
                  v-if="canCreateCategory"
                  :id="`details-category-option-${categorySuggestions.length}`"
                  type="button"
                  class="details-collection-option details-create-option"
                  :class="{ active: activeCategorySuggestion === categorySuggestions.length }"
                  role="option"
                  tabindex="-1"
                  @mouseenter="activeCategorySuggestion = categorySuggestions.length"
                  @mousedown.prevent="beginCategoryCreate"
                >+  新建类别“{{ categoryInput.trim() }}”</button>
                <div v-if="!categorySuggestions.length && !canCreateCategory" class="details-tag-dropdown-empty">
                  {{ categoryInput.trim() ? '没有匹配的可选类别' : '没有可选类别' }}
                </div>
              </div>
            </div>
            <form
              v-if="categoryCreateOpen"
              class="details-inline-create"
              @submit.prevent="confirmCategoryCreate"
              @keydown.esc.stop.prevent="cancelCategoryCreate"
            >
              <div class="details-inline-create-title">新建类别</div>
              <label>
                <span>名称</span>
                <input v-model="categoryCreateName" type="text" maxlength="100" autocomplete="off">
              </label>
              <label>
                <span>父类别</span>
                <select v-model.number="categoryCreateParentId">
                  <option :value="0">顶层类别</option>
                  <option v-for="item in categoryOptions" :key="item.id" :value="item.id">
                    {{ item.path }}
                  </option>
                </select>
              </label>
              <div class="details-inline-create-hint">创建后会立即选中；图片只会在保存更改后移入该类别。</div>
              <div class="details-inline-create-actions">
                <button type="button" class="btn btn-ghost" @click="cancelCategoryCreate">取消</button>
                <button type="submit" class="btn btn-primary" :disabled="!categoryCreateName.trim()">
                  {{ creatingCategory ? '创建中...' : '创建并选中' }}
                </button>
              </div>
            </form>
          </fieldset>

          <fieldset class="details-section" :disabled="loading || saving || renaming || creatingCollection">
            <legend class="details-section-title">
              <span>分组</span>
              <span class="details-tag-count">{{ detail.collections.length }}</span>
            </legend>
            <div class="details-groups" aria-live="polite">
              <span
                v-for="(collection, index) in detail.collections"
                :key="collection.id"
                class="details-group"
                :title="collection.path"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" /></svg>
                <span>{{ collection.path }}</span>
                <button
                  type="button"
                  :aria-label="`从此表情包移除分组 ${collection.path}`"
                  :title="`移除分组 ${collection.path}`"
                  @click="removeCollection(index)"
                >×</button>
              </span>
              <div v-if="!detail.collections.length" class="details-empty">未分组</div>
            </div>
            <div class="details-collection-entry">
              <input
                v-model="collectionInput"
                type="text"
                role="combobox"
                aria-autocomplete="list"
                aria-controls="details-collection-dropdown"
                :aria-expanded="collectionDropdownOpen"
                :aria-activedescendant="activeCollectionSuggestion >= 0 ? `details-collection-option-${activeCollectionSuggestion}` : undefined"
                placeholder="搜索已有分组或输入新名称"
                autocomplete="off"
                spellcheck="false"
                @focus="openCollectionDropdown"
                @input="onCollectionInput"
                @blur="collectionDropdownOpen = false"
                @keydown="onCollectionInputKeydown"
              >
              <div
                v-if="collectionDropdownOpen"
                id="details-collection-dropdown"
                class="details-collection-dropdown"
                role="listbox"
              >
                <div class="details-tag-dropdown-title">已有分组</div>
                <button
                  v-for="(collection, index) in collectionSuggestions"
                  :id="`details-collection-option-${index}`"
                  :key="collection.id"
                  type="button"
                  class="details-collection-option"
                  :class="{ active: activeCollectionSuggestion === index }"
                  role="option"
                  :aria-selected="activeCollectionSuggestion === index"
                  tabindex="-1"
                  @mouseenter="activeCollectionSuggestion = index"
                  @mousedown.prevent="addCollection(collection)"
                >{{ collection.path }}</button>
                <button
                  v-if="canCreateCollection"
                  :id="`details-collection-option-${collectionSuggestions.length}`"
                  type="button"
                  class="details-collection-option details-create-option"
                  :class="{ active: activeCollectionSuggestion === collectionSuggestions.length }"
                  role="option"
                  tabindex="-1"
                  @mouseenter="activeCollectionSuggestion = collectionSuggestions.length"
                  @mousedown.prevent="beginCollectionCreate"
                >+  新建分组“{{ collectionInput.trim() }}”</button>
                <div v-if="!collectionSuggestions.length && !canCreateCollection" class="details-tag-dropdown-empty">
                  {{ collectionInput.trim() ? '没有匹配的可选分组' : '没有其他可选分组' }}
                </div>
              </div>
            </div>
            <form
              v-if="collectionCreateOpen"
              class="details-inline-create"
              @submit.prevent="confirmCollectionCreate"
              @keydown.esc.stop.prevent="cancelCollectionCreate"
            >
              <div class="details-inline-create-title">新建分组</div>
              <label>
                <span>名称</span>
                <input v-model="collectionCreateName" type="text" maxlength="100" autocomplete="off">
              </label>
              <label>
                <span>父分组</span>
                <select v-model.number="collectionCreateParentId">
                  <option :value="0">顶层分组</option>
                  <option v-for="item in collectionParentOptions" :key="item.id" :value="item.id">
                    {{ item.path }}
                  </option>
                </select>
              </label>
              <div class="details-inline-create-hint">小分组只能建在顶层分组下；表情的归属需点击保存更改才生效。</div>
              <div class="details-inline-create-actions">
                <button type="button" class="btn btn-ghost" @click="cancelCollectionCreate">取消</button>
                <button type="submit" class="btn btn-primary" :disabled="!collectionCreateName.trim()">
                  {{ creatingCollection ? '创建中...' : '创建并选中' }}
                </button>
              </div>
            </form>
          </fieldset>

          <fieldset class="details-section" :disabled="loading || saving || renaming">
            <legend class="details-section-title">
              <span>标签</span>
              <span class="details-tag-count">{{ detail.tags.length }}</span>
            </legend>
            <div class="details-tags" aria-live="polite">
              <span
                v-for="(tag, index) in detail.tags"
                :key="tagKey(tag)"
                class="tag active details-tag"
              >
                <span v-if="tag.category" class="details-tag-category">{{ tag.category }}</span>
                <span class="details-tag-name">{{ tag.name }}</span>
                <button
                  v-if="isPersistedTag(tag)"
                  type="button"
                  class="details-tag-action"
                  :aria-label="`编辑标签 ${tag.name}`"
                  :title="`编辑标签 ${tag.name}`"
                  @click="beginTagRename(tag)"
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L8 18l-4 1 1-4Z" /></svg>
                </button>
                <button
                  type="button"
                  class="details-tag-action"
                  :aria-label="`从此表情包移除标签 ${tag.name}`"
                  :title="`从此表情包移除标签 ${tag.name}`"
                  @click="removeTag(index)"
                >×</button>
              </span>
              <div v-if="!detail.tags.length" class="details-empty">此表情包还没有标签</div>
            </div>
            <form v-if="renameSource" class="details-tag-rename" @submit.prevent="confirmTagRename">
              <div class="details-tag-rename-title">编辑标签“{{ renameSource.name }}”</div>
              <div class="details-tag-rename-row">
                <label class="details-tag-rename-field">
                  <span>名称</span>
                  <input
                    ref="renameInputEl"
                    v-model="renameValue"
                    type="text"
                    maxlength="100"
                    autocomplete="off"
                    spellcheck="false"
                    aria-label="新的标签名称"
                    @keydown.esc.stop.prevent="cancelTagRename"
                  >
                </label>
                <label class="details-tag-rename-field">
                  <span>种类</span>
                  <input
                    v-model="renameCategory"
                    type="text"
                    maxlength="100"
                    autocomplete="off"
                    spellcheck="false"
                    placeholder="留空为未分类"
                    aria-label="新的标签种类"
                    @keydown.esc.stop.prevent="cancelTagRename"
                  >
                </label>
                <div class="details-tag-rename-actions">
                  <button type="button" class="btn btn-ghost" @click="cancelTagRename">取消</button>
                  <button type="submit" class="btn btn-primary" :disabled="!renameValue.trim()">
                    {{ renaming ? '处理中...' : '确定' }}
                  </button>
                </div>
              </div>
              <div class="details-tag-rename-hint">名称和种类会对所有使用该标签的表情包同步更新；若目标标签已存在，将自动合并。</div>
            </form>
            <form class="details-tag-entry" @submit.prevent="addTag">
              <input
                v-model="tagCategoryInput"
                class="details-tag-category-input"
                type="text"
                placeholder="新标签种类（可选）"
                autocomplete="off"
                spellcheck="false"
              >
              <div class="details-tag-input-wrap">
                <input
                  v-model="tagInput"
                  type="text"
                  role="combobox"
                  aria-autocomplete="list"
                  aria-controls="details-tag-dropdown"
                  :aria-expanded="tagDropdownOpen"
                  :aria-activedescendant="activeSuggestion >= 0 ? `details-tag-option-${activeSuggestion}` : undefined"
                  placeholder="搜索已有标签或输入新标签"
                  autocomplete="off"
                  spellcheck="false"
                  @focus="openTagDropdown"
                  @input="onTagInput"
                  @blur="tagDropdownOpen = false"
                  @keydown="onTagInputKeydown"
                >
                <div
                  v-if="tagDropdownOpen"
                  id="details-tag-dropdown"
                  class="details-tag-dropdown"
                  role="listbox"
                >
                  <div class="details-tag-dropdown-title">已有标签</div>
                  <div
                    v-for="(tag, index) in tagSuggestions"
                    :id="`details-tag-option-${index}`"
                    :key="tagKey(tag)"
                    class="details-tag-option"
                    :class="{ active: activeSuggestion === index }"
                    role="option"
                    :aria-selected="activeSuggestion === index"
                    @mouseenter="activeSuggestion = index"
                    @mousedown.prevent="selectTag(tag)"
                  >
                    <span>{{ tag.category ? `${tag.category} · ` : '' }}{{ tag.name }}</span>
                    <button
                      type="button"
                      class="details-tag-option-rename"
                      :aria-label="`编辑标签 ${tag.name}`"
                      :title="`编辑标签 ${tag.name}`"
                      tabindex="-1"
                      @mousedown.stop.prevent="beginTagRename(tag)"
                    >
                      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L8 18l-4 1 1-4Z" /></svg>
                    </button>
                  </div>
                  <div v-if="!tagSuggestions.length" class="details-tag-dropdown-empty">
                    {{ tagInput.trim() ? `没有匹配项，按回车创建“${tagInput.trim()}”` : '没有其他可选标签' }}
                  </div>
                </div>
              </div>
              <button class="btn btn-secondary" type="submit" :disabled="!tagInput.trim()">添加</button>
            </form>
          </fieldset>

          <div class="details-text-source-hint">
            人工与 AI 内容分别保存；可将不适用的一侧留空以明确来源。
          </div>
          <label class="details-section">
            <span class="details-section-title">人工图片描述</span>
            <textarea
              v-model="detail.manual_description"
              class="details-textarea"
              rows="3"
              placeholder="输入人工整理的图片描述"
              :disabled="loading || saving || renaming"
              @input="markDirty"
            />
          </label>

          <label class="details-section">
            <span class="details-section-title">AI 图片描述</span>
            <textarea
              v-model="detail.ai_description"
              class="details-textarea"
              rows="3"
              placeholder="输入或修正 AI 生成的图片描述"
              :disabled="loading || saving || renaming"
              @input="markDirty"
            />
          </label>

          <label class="details-section">
            <span class="details-section-title">人工图片文字</span>
            <textarea
              v-model="detail.manual_ocr_text"
              class="details-textarea"
              rows="3"
              placeholder="输入人工整理的图片文字"
              :disabled="loading || saving || renaming"
              @input="markDirty"
            />
          </label>

          <label class="details-section">
            <span class="details-section-title">AI 图片文字</span>
            <textarea
              v-model="detail.ai_ocr_text"
              class="details-textarea"
              rows="3"
              placeholder="输入或修正 AI 识别的图片文字"
              :disabled="loading || saving || renaming"
              @input="markDirty"
            />
          </label>
        </div>
      </div>

      <div class="details-actions">
        <span class="details-shortcuts">Enter 下一项 · Shift+Enter 换行 · Ctrl+Enter 保存</span>
        <span v-if="dirty" class="details-unsaved">有未保存的修改</span>
        <button ref="closeEl" class="btn btn-secondary" title="关闭详情，返回主页面（Esc）" :disabled="saving || renaming || creatingCollection || creatingCategory" @click="close">
          {{ dirty ? '放弃修改并关闭' : '关闭' }}
        </button>
        <button class="btn btn-primary" title="保存更改（Ctrl+Enter / Ctrl+S）" :disabled="!dirty || loading || saving || renaming || creatingCollection || creatingCategory" @click="saveChanges">
          {{ saving ? '保存中...' : '保存更改' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.details-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.62);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 240;
  animation: fadeIn 0.15s ease;
}

.details-box {
  width: min(960px, calc(100vw - 32px));
  max-height: calc(100vh - 32px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-sizing: border-box;
  padding: 20px 22px 22px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
}

.details-heading {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.details-title {
  color: var(--fg);
  font-size: 15px;
  font-weight: 600;
}

.details-position { color: var(--muted); font-size: 12px; }

.details-content {
  flex: 1;
  display: grid;
  grid-template-columns: minmax(360px, 1.45fr) minmax(300px, 1fr);
  grid-template-rows: minmax(0, 1fr);
  gap: 20px;
  min-height: 0;
}

.details-preview-pane {
  position: relative;
  height: min(72vh, 610px);
  min-height: 0;
  max-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg);
}

.details-preview {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.details-preview-loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: color-mix(in srgb, var(--bg) 75%, transparent);
  color: var(--muted);
  font-size: 12px;
}

.details-nav {
  position: absolute;
  top: 50%;
  width: 38px;
  height: 54px;
  padding: 0;
  transform: translateY(-50%);
  border: 1px solid rgba(255, 255, 255, 0.25);
  border-radius: 9px;
  background: rgba(15, 23, 42, 0.72);
  color: #fff;
  font: 34px/1 system-ui, sans-serif;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, background 0.15s;
}

.details-preview-pane:hover .details-nav,
.details-nav:focus-visible { opacity: 1; }

.details-nav:hover:not(:disabled) { background: rgba(15, 23, 42, 0.9); }

.details-nav:disabled { cursor: default; opacity: 0; }

.details-preview-pane:hover .details-nav:disabled { opacity: 0.25; }

.details-nav-prev { left: 12px; }
.details-nav-next { right: 12px; }

.details-info {
  max-height: min(72vh, 610px);
  min-height: 0;
  min-width: 0;
  overflow-y: auto;
  padding-right: 3px;
}

.details-identity { margin: 2px 0 13px; }

.details-field {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.details-field > span {
  color: var(--muted);
  font-size: 12px;
}

.details-field input {
  width: 100%;
  box-sizing: border-box;
  padding: 7px 9px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  outline: none;
  background: var(--bg);
  color: var(--fg);
  font-family: inherit;
  font-size: 12px;
  line-height: 1.45;
  transition: border-color 0.15s;
}

.details-field input:focus { border-color: var(--primary); }
.details-field input:disabled { opacity: 0.65; }

.details-filename-hint {
  margin: -2px 0 0 56px;
  color: var(--muted);
  font-size: 10px;
  line-height: 1.45;
}

.details-meta { margin: 0 0 16px; }

.details-meta > div {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  gap: 8px;
  margin-bottom: 7px;
  font-size: 12px;
  line-height: 1.45;
}

.details-meta dt { color: var(--muted); }

.details-meta dd {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  color: var(--fg-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.details-error {
  margin-bottom: 12px;
  padding: 8px 10px;
  border: 1px solid rgba(239, 68, 68, 0.35);
  border-radius: var(--radius-sm);
  background: rgba(239, 68, 68, 0.08);
  color: var(--danger);
  font-size: 12px;
}

.details-section {
  display: block;
  min-width: 0;
  margin: 0 0 15px;
  padding: 0;
  border: 0;
}

.details-section-title {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 8px;
  color: var(--fg);
  font-size: 12px;
  font-weight: 600;
}

.details-text-source-hint {
  margin: -2px 0 10px;
  padding: 7px 9px;
  border-radius: var(--radius-sm);
  background: var(--surface-2);
  color: var(--muted);
  font-size: 10px;
  line-height: 1.5;
}

.details-tag-count {
  min-width: 18px;
  padding: 1px 5px;
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--muted);
  font-size: 10px;
  font-weight: 500;
  text-align: center;
}

.details-tags {
  min-height: 44px;
  max-height: 100px;
  overflow-y: auto;
  display: flex;
  flex-wrap: wrap;
  align-content: flex-start;
  gap: 7px;
  box-sizing: border-box;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg);
}

.details-tag {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px 4px 2px 9px;
  font-family: inherit;
  cursor: default;
}

.details-tag-name { margin-right: 2px; }
.details-tag-category { margin-right: 3px; color: var(--muted); font-size: 9px; font-weight: 600; }

.details-tag-action {
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: currentColor;
  font: 13px/1 inherit;
  cursor: pointer;
  opacity: 0.68;
}

.details-tag-action svg {
  width: 11px;
  height: 11px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.details-tag-action:hover {
  background: var(--surface);
  opacity: 1;
}

.details-tag-action:last-child:hover { color: var(--danger); }

.details-empty {
  width: 100%;
  align-self: center;
  color: var(--muted);
  font-size: 12px;
  text-align: center;
}

.details-groups {
  min-height: 44px;
  max-height: 100px;
  overflow-y: auto;
  display: flex;
  flex-wrap: wrap;
  align-content: flex-start;
  gap: 7px;
  box-sizing: border-box;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg);
}

.details-category-current {
  display: flex;
  align-items: center;
  min-height: 28px;
  margin-bottom: 8px;
}

.details-group {
  max-width: 100%;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 4px 3px 8px;
  border: 1px solid var(--border);
  border-radius: 20px;
  background: var(--surface);
  color: var(--fg-secondary);
  font-size: 11px;
}

.details-group > svg {
  width: 12px;
  height: 12px;
  flex: 0 0 auto;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.details-group > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.details-group > button {
  width: 18px;
  height: 18px;
  flex: 0 0 auto;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--muted);
  font: 13px/1 inherit;
  cursor: pointer;
}

.details-group > button:hover {
  background: var(--surface-2);
  color: var(--danger);
}

.details-collection-entry {
  position: relative;
  margin-top: 8px;
}

.details-inline-create {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 8px;
  margin-top: 8px;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-2);
}

.details-inline-create-title {
  grid-column: 1 / -1;
  color: var(--fg);
  font-size: 12px;
  font-weight: 600;
}

.details-inline-create label {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.details-inline-create label > span {
  color: var(--muted);
  font-size: 10px;
}

.details-inline-create input,
.details-inline-create select {
  width: 100%;
  box-sizing: border-box;
  padding: 7px 9px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  outline: none;
  background: var(--bg);
  color: var(--fg);
  font-family: inherit;
  font-size: 12px;
}

.details-inline-create input:focus,
.details-inline-create select:focus { border-color: var(--primary); }

.details-inline-create-hint {
  grid-column: 1 / -1;
  color: var(--muted);
  font-size: 10px;
  line-height: 1.45;
}

.details-inline-create-actions {
  grid-column: 1 / -1;
  display: flex;
  justify-content: flex-end;
  gap: 6px;
}

.details-tag-entry {
  display: grid;
  grid-template-columns: minmax(90px, .55fr) minmax(0, 1fr) auto;
  gap: 8px;
  margin-top: 8px;
}

.details-tag-input-wrap { position: relative; min-width: 0; }

.details-tag-rename {
  margin-top: 8px;
  padding: 9px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-2);
}

.details-tag-rename-title {
  margin-bottom: 7px;
  color: var(--fg);
  font-size: 12px;
  font-weight: 600;
}

.details-tag-rename-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 6px;
}

.details-tag-rename-field { display: flex; min-width: 0; flex-direction: column; gap: 4px; }
.details-tag-rename-field > span { color: var(--muted); font-size: 10px; }
.details-tag-rename-actions { grid-column: 1 / -1; display: flex; justify-content: flex-end; gap: 6px; }

.details-tag-rename-hint {
  margin-top: 6px;
  color: var(--muted);
  font-size: 10px;
  line-height: 1.45;
}

.details-tag-entry input,
.details-tag-rename input,
.details-collection-entry input,
.details-textarea {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  outline: none;
  background: var(--bg);
  color: var(--fg);
  font-family: inherit;
  font-size: 12px;
  line-height: 1.55;
  transition: border-color 0.15s;
}

.details-tag-entry input,
.details-tag-rename input,
.details-collection-entry input { padding: 7px 9px; }

.details-tag-dropdown,
.details-collection-dropdown {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  z-index: 20;
  max-height: 180px;
  overflow-y: auto;
  padding: 4px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface);
  box-shadow: var(--shadow-lg);
  scrollbar-width: thin;
}

.details-tag-dropdown-title {
  margin: -4px -4px 4px;
  padding: 5px 12px;
  background: var(--surface-2);
  color: var(--muted);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.3px;
}

.details-tag-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 7px 8px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--fg);
  font-family: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}

.details-tag-option:hover,
.details-tag-option.active {
  background: var(--surface-2);
  color: var(--primary);
}

.details-tag-option-rename {
  width: 22px;
  height: 22px;
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  opacity: 0;
}

.details-tag-option:hover .details-tag-option-rename,
.details-tag-option.active .details-tag-option-rename { opacity: 1; }

.details-tag-option-rename:hover {
  background: var(--surface);
  color: var(--primary);
}

.details-tag-option-rename svg {
  width: 12px;
  height: 12px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.details-collection-option {
  display: block;
  width: 100%;
  padding: 7px 8px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--fg);
  font-family: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}

.details-collection-option:hover,
.details-collection-option.active {
  background: var(--surface-2);
  color: var(--primary);
}

.details-create-option {
  margin-top: 3px;
  border-top: 1px solid var(--border);
  border-radius: 0 0 4px 4px;
  color: var(--primary);
  font-weight: 600;
}

.details-tag-dropdown-empty {
  padding: 9px 8px;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.45;
  text-align: center;
}

.details-textarea {
  min-height: 72px;
  padding: 8px 10px;
  resize: vertical;
}

.details-tag-entry input:focus,
.details-tag-rename input:focus,
.details-collection-entry input:focus,
.details-textarea:focus { border-color: var(--primary); }

.details-tag-entry input:disabled,
.details-tag-rename input:disabled,
.details-collection-entry input:disabled,
.details-textarea:disabled { opacity: 0.65; }

.details-actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}

.details-unsaved {
  color: var(--muted);
  font-size: 11px;
}

.details-shortcuts { margin-right: auto; color: var(--muted); font-size: 11px; }

@media (hover: none) {
  .details-nav:not(:disabled) { opacity: 1; }
}

@media (max-width: 760px) {
  .details-content { grid-template-columns: 1fr; grid-template-rows: auto auto; overflow-y: auto; }
  .details-preview-pane { height: min(48vh, 380px); min-height: 240px; max-height: none; }
  .details-info { max-height: none; overflow-y: visible; }
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
</style>
