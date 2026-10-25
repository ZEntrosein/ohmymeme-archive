<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { ALL_GENRES, tagKey, useMemes } from './composables/useMemes'
import { useDragSort } from './composables/useDragSort'
import { useContextMenu, type MenuItem } from './composables/useContextMenu'
import { useCollectionBuilder, flattenCollections, type CollectionOption } from './composables/useCollectionBuilder'
import ContextMenu from './components/ContextMenu.vue'
import CollectionBuilder from './components/CollectionBuilder.vue'
import CollectionTreeNode from './components/CollectionTreeNode.vue'
import ChatRecordDetails from './components/ChatRecordDetails.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import HierarchyDeleteDialog from './components/HierarchyDeleteDialog.vue'
import ImportMenu from './components/ImportMenu.vue'
import ImportProgressOverlay from './components/ImportProgressOverlay.vue'
import LargeMemeImportDialog from './components/LargeMemeImportDialog.vue'
import MemeDetails from './components/MemeDetails.vue'
import NotionImportDialog from './components/NotionImportDialog.vue'
import InputDialog from './components/InputDialog.vue'
import Pager from './components/Pager.vue'
import SimilarImportDialog from './components/SimilarImportDialog.vue'
import SetupGuide from './components/SetupGuide.vue'
import SyncOverlay from './components/SyncOverlay.vue'
import TagEditor from './components/TagEditor.vue'
import UpdateDialog from './components/UpdateDialog.vue'
import type { ChatRecordSummary, Genre, Meme, TagRecord } from './types'
import { api } from './utils/api'

const { state, setMemes, search, goToPage, setSearch, toggleGenre, toggleTag, setActiveCollection, setActiveCategory, refreshTags, refreshCollections, refreshCategories, refreshRecentUsage, copyMeme, reorderMemes, canReorder, startNativeDrag, selectAllVisible, clearSelection, loadInitData } = useMemes()
const ctx = useContextMenu()
const cb = useCollectionBuilder()
const tagEditor = ref<InstanceType<typeof TagEditor> | null>(null)
const memeDetails = ref<InstanceType<typeof MemeDetails> | null>(null)
const chatRecordDetails = ref<InstanceType<typeof ChatRecordDetails> | null>(null)
const updateDialog = ref<InstanceType<typeof UpdateDialog> | null>(null)
const inputDialog = ref<InstanceType<typeof InputDialog> | null>(null)
const confirmDialog = ref<InstanceType<typeof ConfirmDialog> | null>(null)
const hierarchyDeleteDialog = ref<InstanceType<typeof HierarchyDeleteDialog> | null>(null)
const largeMemeImportDialog = ref<InstanceType<typeof LargeMemeImportDialog> | null>(null)
const similarImportDialog = ref<InstanceType<typeof SimilarImportDialog> | null>(null)
const setupGuide = ref<InstanceType<typeof SetupGuide> | null>(null)

function showSetupGuide() {
  setupGuide.value?.show()
}

// 统一确认对话框（替代原生 confirm，风格与重构主题一致）
async function confirmAsk(title: string, message: string): Promise<boolean> {
  return !!(await confirmDialog.value?.open(title, message))
}

type HierarchyDeleteMode = 'promote' | 'cascade'

async function chooseHierarchyDeleteMode(
  kind: 'collection' | 'category',
  name: string,
): Promise<{ mode: HierarchyDeleteMode; remember: boolean } | null> {
  const configured = await api('get_hierarchy_delete_mode')
  if (configured === 'promote' || configured === 'cascade') {
    return { mode: configured, remember: false }
  }
  return await hierarchyDeleteDialog.value?.open(kind, name) ?? null
}

async function deleteHierarchyNode(
  kind: 'collection' | 'category',
  id: number,
  name: string,
) {
  const choice = await chooseHierarchyDeleteMode(kind, name)
  if (!choice) return
  const result = await api(
    kind === 'category' ? 'delete_category' : 'delete_collection',
    id,
    choice.mode,
  )
  if (!result?.ok) {
    showToast(result?.error || `${kind === 'category' ? '类别' : '分组'}删除失败`)
    return
  }
  if (choice.remember) {
    const saved = await api('set_hierarchy_delete_mode', choice.mode)
    if (!saved?.ok) showToast('已删除，但“始终如此”设置保存失败')
  }
  const deletedIds = new Set<number>(
    (Array.isArray(result.deleted_ids) ? result.deleted_ids : [id]).map(Number),
  )
  if (kind === 'category') {
    if (state.activeCategory != null && deletedIds.has(state.activeCategory)) {
      state.activeCategory = result.parent_id == null
        ? (findCollectionNode(state.categories, -4) ? -4 : null)
        : Number(result.parent_id)
    }
    await refreshCategories()
  } else {
    if (state.activeCollection != null && deletedIds.has(state.activeCollection)) {
      state.activeCollection = result.parent_id == null
        ? (findCollectionNode(state.collections, -4) ? -4 : null)
        : Number(result.parent_id)
    }
    await refreshCollections()
  }
  await search()
  showToast(`${kind === 'category' ? '类别' : '分组'}已删除`)
}

// 检查更新并弹窗：首次 force 发起后台检查；pending 时以非 force 轮询取结果
async function checkUpdateAndPrompt() {
  const upd = await window.pywebview?.api?.check_update(false, true)
  if (upd && upd.pending) {
    setTimeout(checkUpdateResult, 2000)
  } else {
    maybeShowUpdate(upd)
  }
}

// 轮询最近一次后台检查结果（非 force，避免重复触发新检查造成永久 pending）
async function checkUpdateResult() {
  const upd = await window.pywebview?.api?.check_update(false, false)
  if (upd && upd.pending) {
    setTimeout(checkUpdateResult, 2000)
  } else {
    maybeShowUpdate(upd)
  }
}

function maybeShowUpdate(upd) {
  try {
    if (upd && upd.has_update) {
      updateDialog.value?.show(upd.current, upd.latest, upd.download_url, upd.notes)
    }
  } catch (_) {}
}

const sortEnabled = ref(false)
const selectMode = ref(false)
const drag = useDragSort(
  () => state.memes,
  setMemes,
  canReorder,
  () => sortEnabled.value,
  reorderMemes,
  () => { showToast('排序保存失败'); search() },
)

const sidebarCollapsed = ref(false)
const dragOver = ref(false)
let dragCounter = 0
let nativeDragActive = false
let dragGeneration = 0
let dragState: { sx: number; sy: number; px: number; py: number; raf: number | null } | null = null
let updateInterval: ReturnType<typeof setInterval> | null = null

async function refreshLibraryView(kind: 'content' | 'structure' = 'content'): Promise<boolean> {
  const refreshed = await search()
  if (kind === 'structure') {
    await refreshTags()
    await refreshCollections()
    await refreshCategories()
  }
  return refreshed
}

// 启动动画：仅页面首次加载（启动）时播放一次，快捷键呼出不重载页面故不重复播放
const startupAnim = ref(true)
const startupVideoReady = ref(false)
const startupVideoSrc = '/resources/OhMyMeme.mp4'
let startupAnimTimer: ReturnType<typeof setTimeout> | null = null
function dismissStartupAnim() {
  startupAnim.value = false
  if (startupAnimTimer) { clearTimeout(startupAnimTimer); startupAnimTimer = null }
}
onMounted(() => {
  // 兜底：视频加载失败或未触发 ended 时最多 6s 后移除遮罩，避免卡死界面
  startupAnimTimer = setTimeout(dismissStartupAnim, 6000)
  // 后端 evaluate_js 刷新入口（设置保存/同步后由 webui.py 调用）
  window.refreshMemes = () => {
    window.pywebview?.api?.get_settings?.().then((s: any) => {
      if (s) state.hoverZoom = s.hover_zoom !== false
    })
    void refreshLibraryView('content')
  }
  window.refreshTags = refreshTags
  window.refreshCollections = refreshCollections
  window.refreshCategories = refreshCategories
  window.refreshRecent = () => { void refreshRecentUsage() }
  // NapCat/LAN 等后台线程只重新查询当前筛选并回到第一页；仅结构变化才更新侧栏。
  window.refreshLibrary = (kind = 'content') => { void refreshLibraryView(kind) }
  // 设置页「设置向导」按钮入口（SettingsApi.open_guide 调用）
  window.showGuide = showSetupGuide
  // 云端直接使用：后端预取缩略图/入库后通知，刷新网格+标签+分组
  window.onCloudReady = () => {
    imgRetries.clear()
    thumbRev.value++
    void refreshLibraryView('structure')
  }
})
// 网格列数由 CSS repeat(auto-fill, minmax(112px, 1fr)) 随容器宽度自适应

// 侧栏浏览树与右侧当前内容独立，切换侧栏不改变内容导航
const sidebarTab = ref<'collections' | 'categories'>('collections')
const sidebarItems = computed(() => sidebarTab.value === 'categories' ? state.categories : state.collections)
const activeSidebarId = computed(() => (
  sidebarTab.value === 'categories'
    ? (state.activeCategory ?? (state.activeCollection == null ? -1 : null))
    : state.activeCollection
))
const contentTab = computed(() => state.activeCategory != null ? 'categories' : 'collections')
const contentItems = computed(() => contentTab.value === 'categories' ? state.categories : state.collections)
const activeContentId = computed(() => state.activeCategory ?? state.activeCollection)
const hoveredSidebarNode = ref<{
  kind: 'collection' | 'category'
  id: number
} | null>(null)

function subtreeIds(items: any[], targetId: number): Set<number> {
  const collect = (node: any, result: Set<number>) => {
    result.add(Number(node.id))
    for (const child of node.children || []) collect(child, result)
  }
  const find = (nodes: any[]): Set<number> | null => {
    for (const node of nodes) {
      if (Number(node.id) === targetId) {
        const result = new Set<number>()
        collect(node, result)
        return result
      }
      const nested = find(node.children || [])
      if (nested) return nested
    }
    return null
  }
  return find(items) || new Set([targetId])
}

const hoveredSidebarIds = computed(() => {
  const hovered = hoveredSidebarNode.value
  if (!hovered || hovered.id <= 0) return new Set<number>()
  const items = hovered.kind === 'category' ? state.categories : state.collections
  return subtreeIds(items, hovered.id)
})

function onSidebarNodeHover(id: number | null) {
  hoveredSidebarNode.value = id == null
    ? null
    : {
        kind: sidebarTab.value === 'categories' ? 'category' : 'collection',
        id,
      }
}

function isMemeInHoveredSidebarNode(meme: Meme): boolean {
  const hovered = hoveredSidebarNode.value
  if (!hovered) return false
  if (hovered.kind === 'category') {
    if (hovered.id === -1) return true
    if (hovered.id === -4) return meme.category_id == null
    if (hovered.id < -4) return meme.category_id === -hovered.id - 4
    return hoveredSidebarIds.value.has(Number(meme.category_id))
  }
  if (hovered.id === -2) return meme.favorited === true
  if (hovered.id === -3) return meme.recently_used === true
  if (hovered.id === -4) return !meme.collection_ids?.length
  if (hovered.id <= 0) return false
  return (meme.collection_ids || []).some(id => hoveredSidebarIds.value.has(Number(id)))
}

const folderCards = computed(() => {
  if (!activeContentId.value || activeContentId.value < 0) return []
  const find = (items: any[]): any[] => {
    for (const c of items) {
      if (c.id === activeContentId.value) return c.children || []
      if (c.children && c.children.length) {
        const r = find(c.children)
        if (r.length) return r
      }
    }
    return []
  }
  return find(contentItems.value)
})

// 面包屑：当前分组或类别从「全部」到自身的祖先链
const breadcrumb = computed(() => {
  if (!activeContentId.value) return []
  const walk = (items: any[], target: number, trail: any[]): any[] | null => {
    for (const c of items) {
      if (c.id === target) return [...trail, c]
      if (c.children && c.children.length) {
        const r = walk(c.children, target, [...trail, c])
        if (r) return r
      }
    }
    return null
  }
  const path = walk(contentItems.value, activeContentId.value, [])
  if (path) return [{ id: null, name: '全部' }, ...path]
  const sys = contentItems.value.find(c => c.id === activeContentId.value)
  return sys ? [{ id: null, name: '全部' }, sys] : []
})

// 图标条祖先高亮：当前分组所属的顶层分组 id（用于折叠时高亮）
const activeTopLevel = computed(() => {
  const id = activeSidebarId.value
  if (!id || (id < 0 && (sidebarTab.value === 'collections' || id >= -4))) return id
  const findTop = (items: any[], target: number, inherited: number | null): number | null => {
    for (const c of items) {
      const thisTop = inherited ?? c.id
      if (c.id === target) return thisTop
      if (c.children && c.children.length) {
        const r = findTop(c.children, target, thisTop)
        if (r != null) return r
      }
    }
    return null
  }
  const r = findTop(sidebarItems.value, activeSidebarId.value, null)
  return r != null ? r : activeSidebarId.value
})

const tagGroups = computed(() => {
  const groups = new Map<string, TagRecord[]>()
  for (const tag of state.allTags) {
    const category = tag.category || '未分类'
    const items = groups.get(category) || []
    items.push(tag)
    groups.set(category, items)
  }
  return [...groups.entries()].map(([category, tags]) => ({ category, tags }))
})

const genreOptions: { value: Genre; label: string }[] = [
  { value: 'image', label: '图片' },
  { value: 'gif', label: 'GIF' },
  { value: 'chat_record', label: '聊天记录' },
]

function openChatRecord(record: ChatRecordSummary) {
  if (sortEnabled.value || selectMode.value) return
  if (ignoreChatRecordClick) { ignoreChatRecordClick = false; return }
  chatRecordDetails.value?.open(record.id)
}

async function deleteChatRecord(recordId: number) {
  if (!await confirmAsk('删除聊天记录', '确定删除这份聊天记录及其本地媒体吗？')) return
  const result = await window.pywebview?.api?.delete_chat_record(recordId)
  if (!result?.ok) {
    showToast(result?.error || '删除失败')
    return
  }
  chatRecordDetails.value?.close()
  showToast('聊天记录已删除')
  await refreshLibraryView('structure')
}

async function onChatRecordSaved(_recordId: number) {
  showToast('聊天记录管理信息已保存')
  await refreshLibraryView('structure')
}

async function createChatCollection() {
  const name = await inputDialog.value?.open(
    '新建分组路径', '', '例如：角色/爱丽丝/问候',
  )
  if (!name) return
  const result = await api('create_collection', name)
  if (!result?.ok) { showToast(result?.error || '分组创建失败'); return }
  showToast('分组已创建，请点击保存完成归属')
  await refreshCollections()
  await chatRecordDetails.value?.reload()
}

async function createChatCategory() {
  const name = await inputDialog.value?.open(
    '新建类别路径', '', '例如：来源/QQ/群聊',
  )
  if (!name) return
  const result = await api('create_category', name, null)
  if (!result?.ok) { showToast(result?.error || '类别创建失败'); return }
  showToast('类别已创建，请点击保存完成归属')
  await refreshCategories()
  await chatRecordDetails.value?.reload()
}

async function promptCreateHierarchy(
  kind: 'collection' | 'category',
  parentId: number | null = null,
) {
  const label = kind === 'category' ? '类别' : '分组'
  const relative = parentId != null
  const title = relative ? `在当前${label}下新建路径` : `新建${label}路径`
  const placeholder = relative
    ? `输入相对子路径，例如：子${label}/更下一级`
    : `例如：一级${label}/二级${label}/三级${label}`
  const path = await inputDialog.value?.open(title, '', placeholder)
  if (!path) return null
  const result = await api(
    kind === 'category' ? 'create_category' : 'create_collection',
    path,
    parentId,
  )
  if (!result?.ok) {
    showToast(result?.error || `${label}创建失败`)
    return null
  }
  return result
}

async function onSidebarAdd() {
  const kind = sidebarTab.value === 'categories' ? 'category' : 'collection'
  const result = await promptCreateHierarchy(kind)
  if (!result) return
  showToast(`${kind === 'category' ? '类别' : '分组'}路径已创建`)
  if (kind === 'category') await refreshCategories()
  else await refreshCollections()
}

function onBreadcrumbClick(id: number | null) {
  if (contentTab.value === 'categories') setActiveCategory(id === -1 ? null : id)
  else setActiveCollection(id)
}

function onSidebarSelect(id: number) {
  if (sidebarTab.value === 'categories') setActiveCategory(id === -1 ? null : id)
  else setActiveCollection(id, id === -5 && state.activeCollection === -5)
}

function onFolderCardClick(childId: number) {
  if (contentTab.value === 'categories') setActiveCategory(childId)
  else setActiveCollection(childId)
}

function onFolderCardContext(e: MouseEvent, childId: number, childName: string) {
  e.preventDefault()
  e.stopPropagation()
  if (contentTab.value === 'categories') {
    onCategoryRightClick(e, childId, childName)
    return
  }
  ctx.show([
    { action: 'delete-folder', label: '删除小分组', danger: true },
  ], { folderId: childId, folderName: childName, isFolder: true }, e.clientX, e.clientY)
}

function switchSidebarTab(tab: 'collections' | 'categories') {
  if (sidebarTab.value === tab) return
  hoveredSidebarNode.value = null
  sidebarTab.value = tab
}

async function handleCopy(meme: Meme, e?: Event) {
  if (longPressCopyBlock) {
    const blocked = longPressCopyBlock.memeId === meme.id && Date.now() <= longPressCopyBlock.until
    longPressCopyBlock = null
    if (blocked) return
  }
  if (meme.cloud) e?.stopPropagation()
  if (ignoreClick) { ignoreClick = false; return }
  // 整理/多选模式：不复制（勾选由 drag-select 处理）
  if (sortEnabled.value || selectMode.value) return
  if (meme.cloud) { await cloudDownload(meme); return }
  const ok = await copyMeme(meme.id)
  if (ok) {
    await refreshRecentUsage()
    showToast(`${meme.name} 已复制`)
  }
  else showToast('复制失败')
}

// 云端直接使用：下载中防连点（按文件名）
const cloudBusy = ref<Record<string, boolean>>({})
// 下载成功后的遮罩自上而下退场动画（动画结束才刷新列表）
const cloudWipe = ref<Record<string, boolean>>({})
// 云端缩略图预取完成后递增，触发云端行 img 重载（补回 404 失败的图片）
const thumbRev = ref(0)

const CLOUD_ERR_TEXT: Record<string, string> = {
  busy: '正在下载中',
  not_found: '云端条目不存在',
  download_failed: '下载失败',
  sha_mismatch: '文件校验失败',
  too_large: '文件超出限制',
  invalid_image: '无效图片',
  disabled: '云端直接使用未开启',
  no_sync: '未配置云端同步',
  file_exists: '目标类别目录已有同名文件，请先扫描本地库',
}

// 云端直接使用：点击云卡 → 下载校验入库 → 遮罩退场动画 → 自动复制 → 刷新三件套
async function cloudDownload(meme: Meme) {
  const fname = meme.filename
  if (cloudBusy.value[fname]) return
  cloudBusy.value[fname] = true
  try {
    const r = await window.pywebview?.api?.cloud_download(fname)
    if (r && r.ok) {
      showToast(r.status === 'copied' ? `${meme.name} 已复制` : `${meme.name} 已下载`)
      await refreshTags()
      await refreshCollections()
      cloudWipe.value[fname] = true
      await new Promise(res => setTimeout(res, 480))
      delete cloudWipe.value[fname]
      search()
    } else {
      showToast(CLOUD_ERR_TEXT[r?.status] || '下载失败')
    }
  } catch (_) {
    showToast('下载失败')
  } finally {
    delete cloudBusy.value[fname]
  }
}

// 缩略图加载失败重试计数（按基础 URL，成功/onCloudReady 后清零）
const imgRetries = new Map<string, number>()

// 缩略图加载失败（预取未完成/突发连接被拒等）：隐藏破图标并带 cache-buster 有界重试
function onImgError(e: Event) {
  const img = e.target as HTMLImageElement
  const base = img.src.replace(/([?&])r=\d+/, '')
  img.classList.add('img-error')
  const n = (imgRetries.get(base) || 0) + 1
  if (n > 2) return
  imgRetries.set(base, n)
  setTimeout(() => { img.src = `${base}${base.includes('?') ? '&' : '?'}r=${Date.now()}` }, 300 * n)
}

function onImgLoad(e: Event) {
  const img = e.target as HTMLImageElement
  img.classList.remove('img-error')
  imgRetries.delete(img.src.replace(/([?&])r=\d+/, ''))
}

function showToast(msg: string) {
  const el = document.getElementById('toast')
  if (!el) return
  el.textContent = msg
  el.classList.add('show')
  setTimeout(() => el.classList.remove('show'), 1600)
}

// 卡片悬停快速收藏（右键菜单外的一键入口）
async function quickFavorite(meme: Meme) {
  if (meme.cloud) return
  if (sortEnabled.value || selectMode.value) return
  const ok = await window.pywebview?.api?.toggle_favorite(meme.id)
  if (ok === null || ok === undefined) return
  meme.favorited = ok
  await refreshCollections()
  if (!ok && state.activeCollection === -2) {
    const fav = state.collections.find((c: any) => c.id === -2)
    if (!fav || fav.count === 0) setActiveCollection(-4)
  }
  showToast(ok ? '已收藏' : '已取消收藏')
}

function onSearchInput(e: Event) {
  const q = (e.target as HTMLInputElement).value
  setSearch(q)
  debounceSearch()
}

function clearSearch() {
  setSearch('')
  search()
}

// 点击标题栏 logo 返回主页：清空搜索/标签/分组筛选
function goHome() {
  setSearch('')
  state.activeTags.clear()
  state.activeCollection = null
  state.activeCategory = null
  state.activeGenres = [...ALL_GENRES]
  clearSelection()
  search()
}

let searchTimer: ReturnType<typeof setTimeout>
function debounceSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => search(), 300)
}

async function openSettings() {
  try {
    const ok = await window.pywebview?.api?.open_settings()
    if (!ok) showToast('无法打开设置窗口')
  } catch (_) {
    showToast('无法打开设置窗口')
  }
}
function toggleSidebar() {
  hoveredSidebarNode.value = null
  sidebarCollapsed.value = !sidebarCollapsed.value
}

function toggleSort() {
  sortEnabled.value = !sortEnabled.value
  if (sortEnabled.value) { selectMode.value = false; drag.enable() }
  else drag.disable()
  if (!sortEnabled.value) clearSelection()
}

function toggleSelect() {
  selectMode.value = !selectMode.value
  if (selectMode.value) { sortEnabled.value = false; drag.disable() }
  else clearSelection()
}

const importMenu = ref<InstanceType<typeof ImportMenu> | null>(null)
const notionImportDialog = ref<InstanceType<typeof NotionImportDialog> | null>(null)

function showImportMenu() {
  importMenu.value?.open()
}

function showNotionImport() {
  notionImportDialog.value?.open()
}

function onImportDone() {
  void refreshLibraryView('structure')
}

function onMemeDetailsSaved() {
  void refreshLibraryView('structure')
}

function onMemeStructureCreated() {
  refreshCollections()
  refreshCategories()
}

async function onTagRenamed(oldName: string, newName: string) {
  await refreshTags()
  await search()
}

const importProgress = ref<InstanceType<typeof ImportProgressOverlay> | null>(null)

function onImporting() {
  importProgress.value?.start()
}

const syncOverlay = ref<InstanceType<typeof SyncOverlay> | null>(null)

function syncUpload() {
  syncOverlay.value?.start('sync_push', '上传中', 'show_upload_progress', 'show_upload_done')
}

function syncDownload() {
  syncOverlay.value?.start('sync_pull', '下载中', 'show_download_progress', 'show_download_done')
}

function onSyncDone() {
  search()
  refreshTags()
  refreshCollections()
  refreshCategories()
}

async function rescanCache() {
  showToast('缓存刷新中...')
  void refreshLibraryView('structure')
  try { await window.pywebview?.api?.cloud_refresh() } catch (_) {}
}

function hideWindow() {
  try { window.pywebview?.api?.hide_window() } catch (_) {}
}

function minimizeWindow() {
  try { window.pywebview?.api?.minimize_window() } catch (_) {}
}

async function onTitlebarMouseDown(e: MouseEvent) {
  if (e.button !== 0) return
  if ((e.target as HTMLElement).closest('.title-btn') || (e.target as HTMLElement).closest('.icon-btn') || (e.target as HTMLElement).closest('.sidebar-toggle') || (e.target as HTMLElement).closest('.logo')) return
  try {
    const gen = dragGeneration
    const nativeDrag = await window.pywebview?.api?.start_window_drag(e.button + 1, e.screenX, e.screenY)
    if (nativeDrag) return
    // await 期间可能已松开鼠标（onWindowMouseUp 已递增 generation）：此时不再启动拖动
    if (gen !== dragGeneration) return
  } catch (_) {}
  dragState = { sx: e.screenX, sy: e.screenY, px: 0, py: 0, raf: null }
  e.preventDefault()
}

function onWindowMouseMove(e: MouseEvent) {
  if (!dragState) return
  // 记录相对拖动起点的总偏移（屏幕坐标，自愈无累积滞后）
  dragState.px = e.screenX - dragState.sx
  dragState.py = e.screenY - dragState.sy
  // rAF 合并每帧最新位置，避免高回报率鼠标消息风暴堵塞桥接
  if (dragState.raf) return
  dragState.raf = requestAnimationFrame(() => {
    if (!dragState) return
    dragState.raf = null
    const dx = dragState.px
    const dy = dragState.py
    if (dx !== 0 || dy !== 0) {
      try { window.pywebview?.api?.move_window(dx, dy) } catch (_) {}
    }
  })
}

function onWindowMouseUp() {
  if (dragState?.raf) cancelAnimationFrame(dragState.raf)
  try { window.pywebview?.api?.stop_window_drag() } catch (_) {}
  dragState = null
  dragGeneration++  // 使未完成的 mousedown await 失效，防止松手后仍启动拖动
}

function onMemeRightClick(e: MouseEvent, meme: Meme) {
  e.preventDefault()
  e.stopPropagation()
  if (suppressContextMenu) {
    const suppressed = suppressContextMenu.memeId === meme.id && Date.now() <= suppressContextMenu.until
    if (suppressed || Date.now() > suppressContextMenu.until) suppressContextMenu = null
    if (suppressed) return
  }
  clearHoverPreview()
  // 云行无本地 id：不弹操作菜单
  if (meme.cloud) return
  const items: MenuItem[] = [
    { action: 'meme-details', label: '查看表情包详情' },
    {
      action: 'copy-original',
      label: meme.compressed_only ? '复制归档版（无原件）' : '复制原件',
    },
    { action: 'rename', label: '重命名' },
    { action: 'favorite', label: meme.favorited ? '取消收藏' : '收藏' },
    { action: 'tag', label: '打标签' },
    { action: 'collection', label: '添加分组' },
  ]
  if (state.activeCollection && state.activeCollection > 0) {
    items.push({ action: 'remove-collection', label: '移出该分组' })
  }
  items.push({ action: 'add-to-subgroup', label: '加入分组' })
  if (state.activeCollection === -3) {
    items.push({ action: 'remove-recent', label: '从最近使用中删除' })
  }
  items.push({ action: 'delete', label: '删除', danger: true })
  ctx.show(items, { memeId: meme.id, filename: meme.filename, memeName: meme.name, favorited: meme.favorited, compressedOnly: meme.compressed_only }, e.clientX, e.clientY)
}

function onFolderRightClick(e: MouseEvent, folderId: number, folderName: string) {
  e.preventDefault()
  e.stopPropagation()
  if (sidebarTab.value === 'categories') {
    onCategoryRightClick(e, folderId, folderName)
    return
  }
  if (folderId === -5) return
  const systemHidden = !!findCollectionNode(state.collections, folderId)?.system_hidden
  const items: MenuItem[] = systemHidden
    ? []
    : [{ action: 'rename-collection', label: '重命名' }]
  if (folderId === -3) {
    items.push({ action: 'clear-recent', label: '清空最近使用', danger: true })
  } else if (folderId > 0) {
    items.push({ action: 'new-collection-child', label: '新建子分组路径' })
    if (!systemHidden) {
      items.push({ action: 'delete-collection', label: '删除', danger: true })
    }
  }
  ctx.show(items, { folderId, folderName, isFolder: true }, e.clientX, e.clientY)
}

function onCategoryRightClick(e: MouseEvent, categoryId: number, categoryName: string) {
  if (categoryId < -4) { e.preventDefault(); return }
  e.preventDefault()
  e.stopPropagation()
  const items: MenuItem[] = []
  if (categoryId > 0) {
    items.push({ action: 'rename-category', label: '重命名' })
    items.push({ action: 'new-category-child', label: '新建子类别路径' })
    items.push({ action: 'delete-category', label: '删除', danger: true })
  }
  if (items.length) {
    ctx.show(items, { folderId: categoryId, folderName: categoryName, isFolder: true, isCategory: true }, e.clientX, e.clientY)
  }
}

// 右键「加入分组」子菜单：表情上下文任意视图可用，列出全部分组树（子分组带「父/子」路径）。
function onShowSubmenu(_items: any[], x: number, y: number) {
  const targetCol = state.activeCollection && state.activeCollection > 0 ? state.activeCollection : null
  const sub: { action: string; label: string }[] = []
  sub.push({ action: '__new-subgroup__', label: targetCol ? '新建子分组路径' : '新建分组路径' })
  const opts: CollectionOption[] = []
  flattenCollections(state.collections, '', opts)
  for (const o of opts) {
    sub.push({ action: 'subgroup-' + o.id, label: o.depth || o.name })
  }
  ctx.showSubmenu(sub, x, y)
}

async function onCtxAction(action: string) {
  ctx.hide()
  const t = ctx.trigger.value
  switch (action) {
    case 'meme-details': {
      const meme = state.memes.find(item => item.id === t.memeId)
      if (meme) memeDetails.value?.open(meme)
      else showToast('无法读取表情包详情')
      break
    }
    case 'copy-original': {
      const result = await window.pywebview?.api?.copy_meme_original(t.memeId)
      if (result?.ok) await refreshRecentUsage()
      showToast(result?.ok
        ? (t.compressedOnly ? '归档压缩版已复制' : '原件已复制')
        : '复制失败')
      break
    }
    case 'rename': {
      const name = await inputDialog.value?.open('重命名', t.memeName || '')
      if (name && name !== t.memeName) { await window.pywebview?.api?.rename_meme(t.memeId, name); search() }
      break
    }
    case 'favorite': {
      const ok = await window.pywebview?.api?.toggle_favorite(t.memeId)
      if (ok !== null) {
        await refreshCollections()
        if (!ok && state.activeCollection === -2) {
          const fav = state.collections.find((c: any) => c.id === -2)
          if (!fav || fav.count === 0) setActiveCollection(-4)
        }
        search()
      }
      break
    }
    case 'tag': {
      const tags = await tagEditor.value?.open(t.memeId)
      if (tags === null) break
      await window.pywebview?.api?.set_meme_tags(t.memeId, tags)
      // 同步已激活标签筛选，避免已删除标签继续筛
      refreshTags()
      search()
      break
    }
    case 'collection': { showCollectionBuilder(t.memeId); break }
    case 'add-to-subgroup': { /* 子菜单在 hover 时加载，点击走 subgroup-N / __new-subgroup__ */ break }
    case '__new-subgroup__': {
      const targetCol = state.activeCollection && state.activeCollection > 0
        ? state.activeCollection
        : null
      const result = await promptCreateHierarchy('collection', targetCol)
      if (!result) break
      const ok = await window.pywebview?.api?.add_to_existing_collection(
        t.memeId, result.id,
      )
      if (ok) { showToast('已添加'); refreshCollections(); search() }
      else showToast('添加分组失败')
      break
    }
    case 'new-collection-root':
    case 'new-collection-child': {
      const parentId = action === 'new-collection-child' ? t.folderId : null
      const result = await promptCreateHierarchy('collection', parentId)
      if (result) { showToast('分组路径已创建'); await refreshCollections() }
      break
    }
    case 'remove-collection': {
      const removedFrom = state.activeCollection
      const ok = await window.pywebview?.api?.remove_from_collection(t.memeId, removedFrom)
      if (ok) {
        // 从子分组移除时加回上层大分组
        const parent = findParentCollection(state.collections, removedFrom)
        if (parent) await window.pywebview?.api?.add_to_existing_collection(t.memeId, parent.id)
        await refreshCollections()
        // 递归查找分组节点（子分组不在顶层），据此判断是否清空后再删
        const c = findCollectionNode(state.collections, removedFrom)
        if ((!c || c.count === 0) && !c?.system_hidden) {
          if (removedFrom > 0) await window.pywebview?.api?.delete_collection(removedFrom)
          setActiveCollection(parent ? parent.id : -4)
        }
        search()
      }
      break
    }
    case 'remove-recent': {
      await window.pywebview?.api?.remove_from_recent(t.memeId)
      search()
      break
    }
    case 'delete': {
      if (!await confirmAsk('删除表情包', '确定删除这个表情包吗？')) break
      const deleted = await window.pywebview?.api?.delete_meme(t.memeId)
      if (!deleted) {
        showToast('删除失败')
        break
      }
      showToast('表情包已删除')
      await refreshLibraryView('structure')
      break
    }
    case 'rename-collection': {
      const name = await inputDialog.value?.open('重命名分组', t.folderName || '')
      if (name && name !== t.folderName) { await window.pywebview?.api?.rename_collection(t.folderId, name); refreshCollections() }
      break
    }
    case 'rename-category': {
      const name = await inputDialog.value?.open('重命名类别', t.folderName || '')
      if (name && name !== t.folderName) {
        const result = await window.pywebview?.api?.rename_category(t.folderId, name)
        if (result?.ok) { showToast('类别已重命名'); await refreshCategories(); search() }
        else showToast(result?.error || '重命名失败')
      }
      break
    }
    case 'new-category-root':
    case 'new-category-child': {
      const parentId = action === 'new-category-child' ? t.folderId : null
      const result = await promptCreateHierarchy('category', parentId)
      if (result) { showToast('类别路径已创建'); await refreshCategories() }
      break
    }
    case 'delete-category': {
      await deleteHierarchyNode('category', t.folderId, t.folderName)
      break
    }
    case 'delete-collection': {
      await deleteHierarchyNode('collection', t.folderId, t.folderName)
      break
    }
    case 'delete-folder': {
      await deleteHierarchyNode('collection', t.folderId, t.folderName)
      break
    }
    case 'clear-recent': {
      if (await confirmAsk('清空最近使用', '确定清空最近使用记录吗？')) {
        await window.pywebview?.api?.clear_recent()
        search()
      }
      break
    }
    default:
      if (action.startsWith('subgroup-')) {
        const cid = Number(action.slice('subgroup-'.length))
        const ok = await window.pywebview?.api?.add_to_existing_collection(t.memeId, cid)
        if (ok) { showToast('已添加到分组'); refreshCollections(); search() }
        else showToast('添加失败')
      } else {
        showToast(`${action} 功能开发中`)
      }
  }
}

// 批量删除选中的表情包（整理模式操作栏）
async function batchDelete() {
  const ids = [...state.selectedIds]
  if (ids.length === 0) return
  if (!await confirmAsk('批量删除', `确定删除选中的 ${ids.length} 个表情包吗？此操作不可恢复。`)) return
  // 后端异常或 pywebview 不可用：报错即中止，不刷新以免 UI 与 DB 不一致
  let result: any
  try {
    result = await window.pywebview?.api?.delete_memes(ids)
  } catch {
    showToast('批量删除失败')
    return
  }
  if (!result?.ok) {
    showToast('批量删除失败')
    return
  }
  if (result.deleted === 0) {
    // 后端一个都没删掉（所选 id 在库中已不存在），刷新以对齐 UI
    showToast('没有删除任何表情包')
    await refreshLibraryView('structure')
    return
  }
  showToast(`已删除 ${result.deleted ?? ids.length} 个表情包`)
  await refreshLibraryView('structure')
}

// 批量加入分组：CollectionBuilder 选择模式，追加语义（不覆盖已有成员）
function batchAddToCollection() {
  const ids = [...state.selectedIds]
  if (ids.length === 0) return
  cb.openPick(async (r) => {
    let result: any
    try {
      result = r.existingId
        ? await window.pywebview?.api?.batch_add_to_collection(ids, r.existingId)
        : await window.pywebview?.api?.batch_add_to_collection(ids, 0, r.name)
    } catch (_) {
      showToast('加入分组失败')
      return
    }
    if (result?.ok) {
      showToast(r.existingId ? `已加入分组：${r.name}` : `已加入新分组「${r.name}」`)
      refreshCollections()
      search()
    } else {
      showToast(result?.error || '加入分组失败')
    }
  }, 'add', ids)
}

// 批量设置类别：替换单归属类别，由后端同步移动原件。
function batchSetCategory() {
  const ids = [...state.selectedIds]
  if (!ids.length) return
  cb.openPick(async (r) => {
    const result = await api('batch_set_meme_category', ids, r.existingId, r.existingId === null ? r.name : '')
    if (!result?.ok) {
      showToast(result?.error || '设置类别失败')
      return
    }
    showToast(`已为 ${result.count} 个表情包设置类别：${r.name}`)
    await refreshLibraryView('structure')
  }, 'category', ids)
}

// 批量移动分组：从当前分组移出并加入所选分组，原分组移空且无子分组时删除
function batchMoveToCollection() {
  const ids = [...state.selectedIds]
  const fromId = state.activeCollection
  if (ids.length === 0 || !fromId || fromId <= 0) return
  cb.openPick(async (r) => {
    let result: any
    try {
      result = r.existingId
        ? await window.pywebview?.api?.batch_move_to_collection(ids, fromId, r.existingId)
        : await window.pywebview?.api?.batch_move_to_collection(ids, fromId, 0, r.name)
    } catch (_) {
      showToast('移动失败')
      return
    }
    if (!result?.ok) { showToast(result?.error || '移动失败'); return }
    showToast(`已移动 ${result.moved ?? ids.length} 个表情包`)
    await refreshCollections()
    await refreshCategories()
    const node = findCollectionNode(state.collections, fromId)
    if (
      fromId > 0
      && (!node || node.count === 0)
      && !(node?.children || []).length
      && !node?.system_hidden
    ) {
      const parent = findParentCollection(state.collections, fromId)
      await window.pywebview?.api?.delete_collection(fromId)
      setActiveCollection(parent ? parent.id : -4)
      await refreshCollections()
    }
    search()
  }, 'move', ids, fromId)
}

// 批量打标签：合并追加语义（不清空各表情已有标签）
async function batchTag() {
  const ids = [...state.selectedIds]
  if (ids.length === 0) return
  const tags = await tagEditor.value?.openBatch()
  if (tags === null || tags.length === 0) return
  let result: any
  try {
    result = await window.pywebview?.api?.batch_add_tags(ids, tags)
  } catch (_) {
    showToast('批量打标签失败')
    return
  }
  if (result?.ok) {
    showToast(`已为 ${result.count ?? ids.length} 个表情包添加标签`)
    refreshTags()
    search()
  } else {
    showToast('批量打标签失败')
  }
}

// 在 collections 树中查找 target 的父分组
function findParentCollection(items: any[], target: number): any | null {
  for (const c of items) {
    if (c.children) {
      for (const ch of c.children) {
        if (ch.id === target) return c
      }
      const r = findParentCollection(c.children, target)
      if (r) return r
    }
  }
  return null
}

// 递归查找分组节点（含子分组）
function findCollectionNode(items: any[], target: number): any | null {
  for (const c of items) {
    if (c.id === target) return c
    if (c.children && c.children.length) {
      const r = findCollectionNode(c.children, target)
      if (r) return r
    }
  }
  return null
}

async function showCollectionBuilder(memeId?: number) {
  cb.open(async (confirm) => {
    const { name, memeIds, existingId } = confirm
    // 已选已有分组 → 更新其成员；否则新建分组
    const result = existingId
      ? await window.pywebview?.api?.set_collection_members(existingId, memeIds)
      : await window.pywebview?.api?.set_collection_members_new(name, memeIds)
    if (result?.ok || result === true) {
      showToast(existingId ? '已保存到分组：' + name : '分组已创建')
      refreshCollections(); search()
    } else {
      showToast(result?.error || '保存失败')
    }
  }, memeId ? [memeId] : [])
}

const hoverTimers = new Map<number, ReturnType<typeof setTimeout>>()

// 与原始实现一致：auto_play_gif && !hover_to_play 时网格直接播原图；
// hover_to_play 开启时显示缩略图，悬停切原图
function memeSrc(meme: Meme): string {
  if (meme.is_animated && meme.auto_play_gif && !meme.hover_to_play) {
    return `/api/original/${meme.id}/${encodeURIComponent(meme.filename)}`
  }
  if (meme.cloud) return `/api/thumb/${meme.file_hash}?v=${thumbRev.value}`
  return `/api/thumb/${meme.file_hash}`
}

function onCardMouseEnter(meme: Meme, e?: Event) {
  scheduleHoverPreview(meme, (e?.currentTarget as HTMLElement) || null)
  if (!meme.is_animated || !meme.hover_to_play) return
  const timer = setTimeout(() => {
    const img = document.querySelector(`.meme-card[data-meme-id="${meme.id}"] img`) as HTMLImageElement
    if (img) { img.dataset.thumb = img.src; img.src = `/api/original/${meme.id}/${encodeURIComponent(meme.filename)}` }
  }, 150)
  hoverTimers.set(meme.id, timer)
}

function onCardMouseLeave(meme: Meme) {
  clearHoverPreview()
  const timer = hoverTimers.get(meme.id)
  if (timer) { clearTimeout(timer); hoverTimers.delete(meme.id) }
  if (!meme.is_animated || !meme.hover_to_play) return
  const img = document.querySelector(`.meme-card[data-meme-id="${meme.id}"] img`) as HTMLImageElement
  if (img && img.dataset.thumb) img.src = img.dataset.thumb
}

const LONG_PRESS_MS = 500
const LONG_PRESS_MOVE_PX = 8
let nativeDragStart: {
  x: number
  y: number
  memeId: number
  pointerId: number
  card: HTMLElement
  longPressTimer: ReturnType<typeof setTimeout> | null
} | null = null
let chatRecordDragStart: {
  x: number
  y: number
  recordId: number
  pointerId: number
  card: HTMLElement
} | null = null
type LibraryItemType = 'meme' | 'chat_record'
type LibraryTargetType = 'collection' | 'category'
type LibraryDropTarget = {
  type: LibraryTargetType
  id: number
  name: string
}
let libraryDrag: {
  itemType: LibraryItemType
  itemId: number
  pointerId: number
  startX: number
  startY: number
  card: HTMLElement
  target: LibraryDropTarget | null
} | null = null
const libraryDropTargetKey = ref('')

// 悬停 0.5s 放大预览：格子内 object-fit:cover 裁切，横向长图需浮出完整构图
const hoverPreview = ref<Meme | null>(null)
const hoverPreviewPos = ref({ x: 0, y: 0 })
let hoverPreviewTimer: ReturnType<typeof setTimeout> | null = null

function clearHoverPreview() {
  if (hoverPreviewTimer) { clearTimeout(hoverPreviewTimer); hoverPreviewTimer = null }
  hoverPreview.value = null
}

function previewSrc(meme: Meme): string {
  if (meme.cloud) return memeSrc(meme)
  return `/api/original/${meme.id}/${encodeURIComponent(meme.filename)}`
}

function scheduleHoverPreview(meme: Meme, card: HTMLElement | null) {
  clearHoverPreview()
  if (!state.hoverZoom || sortEnabled.value || selectMode.value || !card) return
  hoverPreviewTimer = setTimeout(() => {
    hoverPreviewTimer = null
    if (!card.isConnected) return
    const r = card.getBoundingClientRect()
    // 按预览盒上限 min(72vw,900)×72vh（与 style.css .hover-preview img 一致）夹紧中心，保证整盒可见
    const halfW = Math.min(window.innerWidth * 0.72, 900) / 2
    const halfH = (window.innerHeight * 0.72) / 2
    const clamp = (v: number, min: number, max: number) => Math.min(Math.max(v, min), Math.max(min, max))
    hoverPreviewPos.value = {
      x: clamp(r.left + r.width / 2, halfW + 8, window.innerWidth - halfW - 8),
      y: clamp(r.top + r.height / 2, halfH + 8, window.innerHeight - halfH - 8),
    }
    hoverPreview.value = meme
  }, 500)
}

watch(() => state.memes, clearHoverPreview)
let ignoreClick = false
let ignoreChatRecordClick = false
let longPressCopyBlock: { memeId: number; pointerId: number; until: number } | null = null
let suppressContextMenu: { memeId: number; until: number } | null = null

// 清理尚未触发的长按计时，同时结束当前卡片手势候选
function clearCardGesture() {
  if (nativeDragStart?.longPressTimer) clearTimeout(nativeDragStart.longPressTimer)
  nativeDragStart = null
}

function onChatRecordPointerDown(e: PointerEvent, record: ChatRecordSummary) {
  if (e.button !== 0 || sortEnabled.value || selectMode.value) return
  chatRecordDragStart = {
    x: e.clientX,
    y: e.clientY,
    recordId: record.id,
    pointerId: e.pointerId,
    card: e.currentTarget as HTMLElement,
  }
  ignoreChatRecordClick = false
}

function onCardPointerDown(e: PointerEvent, meme: Meme, card: HTMLElement) {
  clearHoverPreview()
  ignoreClick = false
  if (longPressCopyBlock && Date.now() > longPressCopyBlock.until) longPressCopyBlock = null
  clearCardGesture()
  // 云行无 id：不参与排序拖拽/原生拖出
  if (meme.cloud) return
  if (sortEnabled.value && canReorder() && !selectMode.value) {
    drag.onPointerDown(e, meme.id, card)
    return
  }
  if (e.button !== 0 || selectMode.value) return
  const gesture = {
    x: e.clientX,
    y: e.clientY,
    memeId: meme.id,
    pointerId: e.pointerId,
    card,
    longPressTimer: null as ReturnType<typeof setTimeout> | null,
  }
  gesture.longPressTimer = setTimeout(() => {
    if (nativeDragStart !== gesture) return
    nativeDragStart = null
    longPressCopyBlock = { memeId: meme.id, pointerId: e.pointerId, until: Date.now() + 1200 }
    suppressContextMenu = { memeId: meme.id, until: Date.now() + 1200 }
    memeDetails.value?.open(meme)
  }, LONG_PRESS_MS)
  nativeDragStart = gesture
}

function clearLibraryDrag() {
  const current = libraryDrag
  if (current) {
    current.card.classList.remove('library-dragging')
    try {
      if (current.card.hasPointerCapture(current.pointerId)) {
        current.card.releasePointerCapture(current.pointerId)
      }
    } catch (_) {}
  }
  libraryDrag = null
  libraryDropTargetKey.value = ''
}

let hierarchyDrag: {
  kind: LibraryTargetType
  id: number
  row: HTMLElement
  pointerId: number
  x: number
  y: number
  active: boolean
  target: { id: number | null; name: string } | null
} | null = null
const hierarchyDragId = ref<number | null>(null)
const hierarchyDropTargetKey = ref('')
let hierarchyClickGuard = false
let hierarchyMovePending = false

function clearHierarchyDrag() {
  const current = hierarchyDrag
  hierarchyDrag = null
  hierarchyDragId.value = null
  hierarchyDropTargetKey.value = ''
  if (current) {
    try {
      if (current.row.hasPointerCapture(current.pointerId)) current.row.releasePointerCapture(current.pointerId)
    } catch (_) {}
  }
}

function onHierarchyPointerDown(e: PointerEvent, id: number) {
  hierarchyClickGuard = false
  if (e.button !== 0 || e.pointerType === 'touch' || sidebarCollapsed.value || hierarchyMovePending) return
  if ((e.target as HTMLElement).closest('button, .tree-toggle')) return
  const node = findCollectionNode(sidebarItems.value, id)
  if (id <= 0 || !node || node.system_hidden || node.direct_category_id != null) return
  clearHierarchyDrag()
  hierarchyDrag = {
    kind: sidebarTab.value === 'categories' ? 'category' : 'collection',
    id, row: e.currentTarget as HTMLElement, pointerId: e.pointerId,
    x: e.clientX, y: e.clientY, active: false, target: null,
  }
}

function updateHierarchyDrag(e: PointerEvent) {
  const current = hierarchyDrag
  if (!current || e.pointerId !== current.pointerId) return
  if (!current.active) {
    if (Math.hypot(e.clientX - current.x, e.clientY - current.y) <= 8) return
    current.active = true
    hierarchyDragId.value = current.id
    hierarchyClickGuard = true
    clearHoverPreview()
    try { current.row.setPointerCapture(current.pointerId) } catch (_) {}
  }
  e.preventDefault()
  current.target = null
  hierarchyDropTargetKey.value = ''
  const element = document.elementFromPoint(e.clientX, e.clientY)
  const root = element?.closest<HTMLElement>('[data-hierarchy-root]')
  const row = element?.closest<HTMLElement>('[data-hierarchy-kind]')
  const kind = root?.dataset.hierarchyRoot || row?.dataset.hierarchyKind
  if (kind !== current.kind) return
  const targetId = root ? null : Number(row?.dataset.hierarchyId)
  const items = current.kind === 'category' ? state.categories : state.collections
  const source = findCollectionNode(items, current.id)
  if (!source) return
  const parentId = source.parent_id ?? findHierarchyParent(items, current.id)
  if (targetId == null || current.kind === 'category' && targetId === -1) {
    if (parentId == null) return
    current.target = { id: null, name: '顶层' }
    hierarchyDropTargetKey.value = current.kind + ':root'
    return
  }
  if (!Number.isInteger(targetId) || targetId <= 0 || targetId === parentId) return
  const target = findCollectionNode(items, targetId)
  if (!target || target.direct_category_id != null || subtreeIds(items, current.id).has(targetId)) return
  current.target = { id: targetId, name: target.name }
  hierarchyDropTargetKey.value = `${current.kind}:${targetId}`
}

function findHierarchyParent(items: any[], id: number, parent: number | null = null): number | null {
  for (const node of items) {
    if (node.id === id) return parent
    if (findCollectionNode(node.children || [], id)) return findHierarchyParent(node.children, id, node.id)
  }
  return null
}

async function finishHierarchyDrop(e: PointerEvent) {
  if (!hierarchyDrag || hierarchyDrag.pointerId !== e.pointerId) return
  updateHierarchyDrag(e)
  const current = hierarchyDrag
  clearHierarchyDrag()
  if (!current.active || !current.target) return
  const items = current.kind === 'category' ? state.categories : state.collections
  const source = findCollectionNode(items, current.id)
  if (!source) return
  const label = current.kind === 'category' ? '类别' : '分组'
  const targetLabel = current.target.id == null ? '顶层' : `「${current.target.name}」下`
  const note = current.kind === 'category'
    ? '整个子类别树和对应原件目录将一起移动，内容归属不变。'
    : '整个子分组树将一起移动，成员关系不变，不移动图片文件。'
  hierarchyMovePending = true
  try {
    if (!await confirmAsk(`移动${label}`, `将${label}「${source.name}」移到${targetLabel}？${note}`)) return
    const result = await api('move_hierarchy_node', current.kind, current.id, current.target.id)
    if (!result?.ok) { showToast(result?.error || '移动失败'); return }
    await refreshLibraryView('structure')
    showToast(`${label}已移动`)
  } finally {
    hierarchyMovePending = false
  }
}

function beginLibraryDrag(
  itemType: LibraryItemType,
  itemId: number,
  pointerId: number,
  startX: number,
  startY: number,
  card: HTMLElement,
) {
  clearLibraryDrag()
  libraryDrag = { itemType, itemId, pointerId, startX, startY, card, target: null }
  card.classList.add('library-dragging')
  try { card.setPointerCapture(pointerId) } catch (_) {}
}

function libraryTargetAt(x: number, y: number): LibraryDropTarget | null {
  const element = document.elementFromPoint(x, y) as HTMLElement | null
  const row = element?.closest<HTMLElement>('[data-library-drop-kind]')
  if (!row) return null
  const type = row.dataset.libraryDropKind as LibraryTargetType
  const id = Number(row.dataset.libraryDropId)
  if (type === 'collection' && id <= 0) return null
  if (type === 'category' && id !== -4 && id <= 0) return null
  if (!Number.isInteger(id)) return null
  return { type, id, name: row.dataset.libraryDropName || '' }
}

function showChatDragResult(result: any) {
  if (result?.cancelled) return
  if (!result?.ok) {
    showToast(result?.error || '聊天记录拖拽发送失败')
    return
  }
  const suffix = result.degraded_media_count
    ? `（${result.degraded_media_count} 个媒体已转为占位）`
    : ''
  const rebuildSource = result.self_native_relay
    ? '（自聊重建后原生转发，发送者为自己）'
    : result.native_filtered_replay
      ? '（筛选后原生负载重放）'
      : result.native_replay
        ? '（原生负载重放）'
        : result.native_rebuild ? '（QQNT 原生重组）' : ''
  const targetSource = result.target_source === 'napcat_plugin'
    ? '（NapCat 会话 ID）'
    : ''
  showToast(`已发送到 ${result.target_label || '当前 QQ 会话'}${targetSource}${rebuildSource}${suffix}`)
}

function startExternalLibraryDrag(itemType: LibraryItemType, itemId: number) {
  clearLibraryDrag()
  nativeDragActive = true
  if (itemType === 'chat_record') {
    api('start_chat_record_drag', itemId).then((result) => {
      nativeDragActive = false
      showChatDragResult(result)
    }).catch(() => {
      nativeDragActive = false
      showToast('聊天记录拖拽发送失败')
    })
    return
  }
  startNativeDrag(itemId).then((result) => {
    nativeDragActive = false
    if (result?.cancelled) return
    if (!result?.ok) {
      showToast(result?.error || '拖拽失败：本地文件不存在')
      return
    }
    void refreshRecentUsage()
    if (result.sent_as_sticker) {
      const targetSource = result.target_source === 'napcat_plugin'
        ? '（NapCat 会话 ID）'
        : ''
      showToast(`已作为表情包发送到 ${result.target_label || '当前 QQ 会话'}${targetSource}`)
    }
  }).catch(() => { nativeDragActive = false })
}

function updateLibraryDrag(e: PointerEvent) {
  if (!libraryDrag) return
  const target = libraryTargetAt(e.clientX, e.clientY)
  libraryDrag.target = target
  libraryDropTargetKey.value = target ? `${target.type}:${target.id}` : ''
  const outside = (
    e.clientX <= 0 || e.clientY <= 0
    || e.clientX >= window.innerWidth || e.clientY >= window.innerHeight
  )
  const movingTowardSidebar = e.clientX < libraryDrag.startX - 4
  const distance = Math.hypot(
    e.clientX - libraryDrag.startX, e.clientY - libraryDrag.startY,
  )
  if (outside || (!target && !movingTowardSidebar && distance > 20)) {
    const { itemType, itemId } = libraryDrag
    startExternalLibraryDrag(itemType, itemId)
  }
}

async function finishLibraryDrop() {
  if (!libraryDrag) return
  const { itemType, itemId, target } = libraryDrag
  clearLibraryDrag()
  if (itemType === 'meme') ignoreClick = true
  else ignoreChatRecordClick = true
  if (!target) return
  const result = await api(
    'assign_library_item', itemType, itemId, target.type, target.id,
  )
  if (!result?.ok) {
    showToast(result?.error || '修改归属失败')
    return
  }
  const itemLabel = itemType === 'meme' ? '表情包' : '聊天记录'
  const action = target.type === 'collection' ? '已加入分组' : '已切换到类别'
  showToast(`${itemLabel}${action}「${target.name}」`)
  await refreshLibraryView('structure')
}

function onDocPointerMove(e: PointerEvent) {
  if (hierarchyDrag) { updateHierarchyDrag(e); return }
  if (drag.dragState.memeId) {
    drag.onPointerMove(e)
    return
  }
  if (libraryDrag) {
    updateLibraryDrag(e)
    return
  }
  if (chatRecordDragStart && !nativeDragActive) {
    const dist = Math.hypot(
      e.clientX - chatRecordDragStart.x,
      e.clientY - chatRecordDragStart.y,
    )
    if (dist > LONG_PRESS_MOVE_PX) {
      const start = chatRecordDragStart
      chatRecordDragStart = null
      ignoreChatRecordClick = true
      beginLibraryDrag(
        'chat_record', start.recordId, start.pointerId, start.x, start.y, start.card,
      )
      updateLibraryDrag(e)
    }
    return
  }
  if (!nativeDragStart || sortEnabled.value || selectMode.value) return
  const dist = Math.hypot(e.clientX - nativeDragStart.x, e.clientY - nativeDragStart.y)
  if (dist > LONG_PRESS_MOVE_PX) {
    const start = nativeDragStart
    clearCardGesture()
    ignoreClick = true
    beginLibraryDrag(
      'meme', start.memeId, start.pointerId, start.x, start.y, start.card,
    )
    updateLibraryDrag(e)
  }
}

async function onDocPointerUp(e: PointerEvent) {
  if (hierarchyDrag) { await finishHierarchyDrop(e); return }
  if (drag.dragState.memeId) {
    const wasActive = await drag.onPointerUp()
    if (wasActive) ignoreClick = true
    return
  }
  if (libraryDrag) {
    await finishLibraryDrop()
    return
  }
  if (longPressCopyBlock?.pointerId === e.pointerId) {
    longPressCopyBlock.until = Date.now() + 800
    suppressContextMenu = { memeId: longPressCopyBlock.memeId, until: Date.now() + 800 }
  }
  chatRecordDragStart = null
  clearCardGesture()
}

function onDocPointerCancel() {
  clearHierarchyDrag()
  if (drag.dragState.memeId) drag.cancel()
  clearLibraryDrag()
  longPressCopyBlock = null
  chatRecordDragStart = null
  clearCardGesture()
}

// 子级弹窗打开时暂停全局导入，但保留编辑框原生的文本拖动行为。
function ignoreImportDrag(e: DragEvent) {
  const selector = [
    '[aria-modal="true"]', '.import-overlay', '.imp-overlay', '.upd-overlay',
    '.ctx-overlay', '#sync-progress-overlay', '#sync-done-overlay',
    '.sync-warning-overlay', '#startup-anim',
  ].join(',')
  const blocked = Array.from(document.querySelectorAll<HTMLElement>(selector))
    .some(overlay => overlay.getClientRects().length > 0
      && getComputedStyle(overlay).visibility !== 'hidden')
  if (!blocked) return false
  dragCounter = 0
  dragOver.value = false
  const target = e.target instanceof Element ? e.target : null
  const editor = target?.closest<HTMLElement>('input, textarea, [contenteditable]')
  const textInput = editor instanceof HTMLInputElement
    && ['text', 'search', 'url', 'tel', 'email', 'password', 'number'].includes(editor.type)
  const editable = editor && !editor.matches(':disabled, [readonly]')
    && (textInput || editor.tagName === 'TEXTAREA' || editor.isContentEditable)
  const files = Array.from(e.dataTransfer?.types || []).includes('Files')
  if (!editable || files) e.preventDefault()
  return true
}

function onDragEnter(e: DragEvent) {
  if (ignoreImportDrag(e)) return
  e.preventDefault()
  if (nativeDragActive) return
  dragCounter++
  dragOver.value = true
}

function onDragOver(e: DragEvent) {
  if (ignoreImportDrag(e)) return
  e.preventDefault()
  if (nativeDragActive) return
}

function onDragLeave(e: DragEvent) {
  if (ignoreImportDrag(e)) return
  e.preventDefault()
  if (nativeDragActive) return
  dragCounter--
  if (dragCounter <= 0) { dragCounter = 0; dragOver.value = false }
}

async function finishMemeImport() {
  showToast('导入成功')
  await refreshLibraryView('structure')
}

async function finishChatRecordImport(result: any) {
  showToast(result?.created === false ? '聊天记录已更新' : '聊天记录导入成功')
  await refreshLibraryView('content')
}

function duplicateIds(result: any): number[] {
  const values = Array.isArray(result?.duplicate_ids)
    ? result.duplicate_ids
    : (result?.existing_id ? [result.existing_id] : [])
  return [...new Set(values.map(Number).filter(Number.isInteger))]
}

async function revealExistingImports(ids: number[]) {
  const uniqueIds = [...new Set((ids || []).map(Number).filter(Number.isInteger))]
  if (!uniqueIds.length) {
    showToast('该图片已存在，但无法定位本地记录')
    return
  }
  const recorded = await api('record_meme_uses', uniqueIds)
  if (!recorded?.ok || !recorded.ids?.length) {
    showToast('该图片已存在，但无法更新最近使用')
    return
  }
  sidebarTab.value = 'collections'
  state.activeTags = new Set()
  state.searchQuery = ''
  state.activeCategory = null
  state.activeCollection = -3
  await Promise.all([refreshTags(), refreshCollections(), refreshCategories()])
  await search()
  showToast(recorded.ids.length > 1
    ? `发现 ${recorded.ids.length} 个已有表情`
    : '该图片已存在')
}

async function handleSimilarImport(result: any) {
  const action = await similarImportDialog.value?.open(result?.candidates || [])
  if (!action) return
  const resolved = await api('resolve_similar_import', result.token, action)
  if (resolved?.ok && resolved.imported) await finishMemeImport()
  else if (resolved?.ok && resolved.existing_id) {
    await revealExistingImports([resolved.existing_id])
  }
  else if (resolved?.ok) showToast('已跳过该图片')
  else showToast(resolved?.error || '处理失败')
}

async function handleLargeMemeImport(result: any) {
  const choice = await largeMemeImportDialog.value?.open(result?.count || 1)
  if (!choice) {
    await api('cancel_large_meme_import', result?.token || '')
    return
  }
  const resolved = await api(
    'resolve_large_meme_import',
    result.token,
    choice.mode,
    choice.remember,
  )
  if (resolved?.async) {
    onImporting()
    return
  }
  if (resolved?.similar_pending) {
    await handleSimilarImport(resolved)
    return
  }
  if (resolved?.duplicate) {
    await revealExistingImports(duplicateIds(resolved))
    return
  }
  if (resolved?.ok) await finishMemeImport()
  else showToast(resolved?.error || '导入失败')
}

async function onDrop(e: DragEvent) {
  if (ignoreImportDrag(e)) return
  e.preventDefault()
  if (nativeDragActive) { dragCounter = 0; dragOver.value = false; return }
  dragCounter = 0
  dragOver.value = false
  const dt = e.dataTransfer
  if (!dt) return
  let uri = ''
  let file: File | null = null
  if (!uri) { try { uri = dt.getData('text/uri-list') || '' } catch (_) {} }
  if (!uri) { try { uri = dt.getData('text/plain') || '' } catch (_) {} }
  uri = uri.trim()
  if (!uri) {
    try {
      if (dt.items?.length) {
        for (let i = 0; i < dt.items.length; i++) {
          const item = dt.items[i]
          if (item.kind === 'string' && item.type === 'text/html') {
            const html = await new Promise<string>(res => item.getAsString(res))
            if (!html) continue
            const text = html.replace(/<[^>]+>/g, '').trim()
            if (text.startsWith('file://')) { uri = text; break }
          }
        }
      }
    } catch (_) {}
  }
  if (!uri) { try { if (dt.files?.length) file = dt.files[0] } catch (_) {} }
  if (!uri && !file) {
    try {
      if (dt.items?.length) {
        for (let i = 0; i < dt.items.length; i++) {
          const it = dt.items[i]
          if (it.kind === 'file') { file = it.getAsFile(); if (file) break }
        }
      }
    } catch (_) {}
  }
  if (uri) {
    try {
      const r = await window.pywebview?.api?.download_original_image(uri)
      if (r?.large_file_pending) {
        await handleLargeMemeImport(r)
        return
      }
      if (r?.ok) {
        await finishMemeImport()
        return
      }
      if (r?.duplicate) {
        await revealExistingImports(duplicateIds(r))
        return
      }
      if (r?.similar_pending) {
        await handleSimilarImport(r)
        return
      }
      showToast(r?.error || '导入失败')
      return
    } catch (_) {
      showToast('导入失败')
      return
    }
  }
  if (file) {
    let b64: string
    try {
      b64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve((reader.result as string).split(',')[1])
        reader.onerror = () => reject(reader.error)
        reader.readAsDataURL(file!)
      })
    } catch (_) {
      showToast('导入失败：无法读取文件')
      return
    }
    try {
      const res = await fetch('/api/upload/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ files: [{ name: file.name, data: b64 }] }) })
      let j: any = null
      try { j = await res.json() } catch (_) { j = null }
      if (j?.large_file_pending) {
        await handleLargeMemeImport(j)
        return
      }
      if (j?.similar_pending) {
        await handleSimilarImport(j)
        return
      }
      if (j?.duplicate) {
        await revealExistingImports(duplicateIds(j))
        return
      }
      if (j?.ok === true && j?.chat_record) {
        await finishChatRecordImport(j)
        return
      }
      // Bottle 对业务失败也可能返回 HTTP 200；必须以响应体的 ok 为准，
      // 否则后端回滚导入时仍会误报“导入成功”。
      if (j?.ok === true) {
        await finishMemeImport()
      }
      else { showToast(j?.error || '导入失败：服务器返回错误') }
    } catch (_) {
      showToast('导入失败：网络或服务异常')
    }
    return
  }
  showToast('未识别到可导入的内容')
}

// ESC 先关闭当前子层；详情返回后须再次按下才会隐藏主窗口。
function onDocKeydown(e: KeyboardEvent) {
  if (e.key !== 'Escape' || e.defaultPrevented || e.isComposing || e.keyCode === 229) return
  if (e.repeat) { e.preventDefault(); return }
  if (hierarchyDrag) { e.preventDefault(); clearHierarchyDrag(); return }
  if (ctx.visible.value) { ctx.hide(); return }
  const details = chatRecordDetails.value?.isOpen()
    ? chatRecordDetails.value
    : memeDetails.value?.isOpen() ? memeDetails.value : null
  if (details) {
    e.preventDefault()
    e.stopPropagation()
    details.close()
    return
  }
  if (hoverPreview.value) { clearHoverPreview(); return }
  if (selectMode.value) { toggleSelect(); return }
  if (sortEnabled.value) { toggleSort(); return }
  hideWindow()
}

// 侧栏滑动手势：折叠态在侧栏条上右滑展开，展开态在侧栏内左滑折叠（对齐安卓端边缘滑动）
let swipeStart: { x: number; y: number; collapsed: boolean } | null = null
let swipeClickGuard = false

function onSidebarSwipeDown(e: PointerEvent) {
  hierarchyClickGuard = false
  swipeClickGuard = false
  swipeStart = null
  if (e.button !== 0) return
  if (hierarchyDrag) return
  if (!(e.target as HTMLElement).closest?.('#sidebar')) return
  swipeStart = { x: e.clientX, y: e.clientY, collapsed: sidebarCollapsed.value }
}

function onSidebarSwipeMove(e: PointerEvent) {
  if (!swipeStart) return
  const dx = e.clientX - swipeStart.x
  const dy = e.clientY - swipeStart.y
  if (Math.abs(dx) < 40 || Math.abs(dx) <= Math.abs(dy)) return
  if (swipeStart.collapsed && dx > 0 && sidebarCollapsed.value) sidebarCollapsed.value = false
  else if (!swipeStart.collapsed && dx < 0 && !sidebarCollapsed.value) sidebarCollapsed.value = true
  else return
  swipeClickGuard = true
  swipeStart = null
}

function onSidebarSwipeEnd() { swipeStart = null }

function onSwipeClickCapture(e: MouseEvent) {
  if (hierarchyClickGuard && e.detail !== 0) {
    hierarchyClickGuard = false
    e.stopPropagation()
    e.preventDefault()
    return
  }
  if (!swipeClickGuard || e.detail === 0) return
  swipeClickGuard = false
  e.stopPropagation()
  e.preventDefault()
}

onMounted(() => {
  document.addEventListener('dragenter', onDragEnter)
  document.addEventListener('dragover', onDragOver)
  document.addEventListener('dragleave', onDragLeave)
  document.addEventListener('drop', onDrop)
  document.addEventListener('mousemove', onWindowMouseMove)
  document.addEventListener('mouseup', onWindowMouseUp)
  document.addEventListener('pointermove', onDocPointerMove)
  document.addEventListener('pointerup', onDocPointerUp)
  document.addEventListener('pointercancel', onDocPointerCancel)
  document.addEventListener('keydown', onDocKeydown)
  document.addEventListener('pointerdown', onSidebarSwipeDown)
  document.addEventListener('pointermove', onSidebarSwipeMove)
  document.addEventListener('pointerup', onSidebarSwipeEnd)
  document.addEventListener('pointercancel', onSidebarSwipeEnd)
  document.addEventListener('click', onSwipeClickCapture, true)
  window.addEventListener('blur', onDocPointerCancel)
})

onUnmounted(() => {
  clearHierarchyDrag()
  document.removeEventListener('dragenter', onDragEnter)
  document.removeEventListener('dragover', onDragOver)
  document.removeEventListener('dragleave', onDragLeave)
  document.removeEventListener('drop', onDrop)
  document.removeEventListener('mousemove', onWindowMouseMove)
  document.removeEventListener('mouseup', onWindowMouseUp)
  document.removeEventListener('pointermove', onDocPointerMove)
  document.removeEventListener('pointerup', onDocPointerUp)
  document.removeEventListener('pointercancel', onDocPointerCancel)
  document.removeEventListener('keydown', onDocKeydown)
  document.removeEventListener('pointerdown', onSidebarSwipeDown)
  document.removeEventListener('pointermove', onSidebarSwipeMove)
  document.removeEventListener('pointerup', onSidebarSwipeEnd)
  document.removeEventListener('pointercancel', onSidebarSwipeEnd)
  document.removeEventListener('click', onSwipeClickCapture, true)
  window.removeEventListener('blur', onDocPointerCancel)
  if (updateInterval) { clearInterval(updateInterval); updateInterval = null }
  clearCardGesture()
  hoverTimers.forEach(t => clearTimeout(t))
  hoverTimers.clear()
  if (hoverPreviewTimer) { clearTimeout(hoverPreviewTimer); hoverPreviewTimer = null }
})

;(async () => {
  await loadInitData()
  // 配置无 guide=ok（新装或旧版升级）时启动即弹设置向导；启动动画遮罩 z-index 更高，动画结束后自然露出
  if (!state.guideOk) setupGuide.value?.show()
  // 动画开启时：播放期间即加载后续内容（动画天然覆盖桥接稳定时间），去除 300ms 延时；
  // 动画关闭时：不播放动画，降级为 300ms 延时
  const runBackground = async () => {
    await window.pywebview?.api?.rescan_cache()
    await window.pywebview?.api?.run_auto_sync()
    await search()
    await refreshTags()
    await refreshCollections()
    await refreshCategories()
  }
  const prefersReducedMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  if (state.showStartupAnimation && !prefersReducedMotion) {
    // 启动遮罩背景贴合视频边缘色（含 html/body 首次渲染）
    document.documentElement.style.background = state.startupBgColor
    document.body.style.background = state.startupBgColor
    startupVideoReady.value = true
    runBackground()
  } else {
    dismissStartupAnim()
    setTimeout(runBackground, 300)
  }
  // 每日更新检测（完整弹窗 + 下载安装，与原始实现一致）
  await checkUpdateAndPrompt()
  updateInterval = setInterval(checkUpdateAndPrompt, 24 * 60 * 60 * 1000)
})()
</script>

<template>
  <div id="app">
    <header id="titlebar" @mousedown="onTitlebarMouseDown">
      <div class="titlebar__left">
        <div class="logo" title="返回主页" @click="goHome">OhMy<span>Meme</span></div>
      </div>
      <span class="spacer"></span>
      <div class="titlebar__actions">
        <button class="icon-btn" :class="{ 'sort-on': sortEnabled }" title="拖拽排序" aria-label="拖拽排序" @click="toggleSort">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10l5-5 5 5M7 14l5 5 5-5"/></svg>
        </button>
        <button class="icon-btn" :class="{ 'sort-on': selectMode }" title="多选" aria-label="多选" @click="toggleSelect">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
        </button>
        <button class="icon-btn" title="上传到远端" aria-label="上传到远端" @click="syncUpload()">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 19V5m-7 7l7-7 7 7"/></svg>
        </button>
        <button class="icon-btn" title="从远端下载" aria-label="从远端下载" @click="syncDownload()">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14m-7-7l7 7 7-7"/></svg>
        </button>
        <button class="title-btn" @click="showImportMenu()">导入</button>
        <button class="title-btn" @click="rescanCache()">刷新</button>
        <button class="title-btn" @click="openSettings()">设置</button>
        <button class="title-btn window-btn" title="最小化" aria-label="最小化窗口" @click="minimizeWindow()">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h10" /></svg>
        </button>
        <button class="title-btn window-btn close-btn" title="隐藏到托盘" aria-label="隐藏到托盘" @click="hideWindow()">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg>
        </button>
      </div>
    </header>

    <div id="content">
      <aside id="sidebar" :class="{ collapsed: sidebarCollapsed }">
        <div id="sidebar-header"
          :data-hierarchy-root="sidebarTab === 'categories' ? 'category' : 'collection'"
          :class="{ 'hierarchy-root-target': hierarchyDropTargetKey.endsWith(':root') }"
          :title="hierarchyDragId != null ? '松手后确认移至顶层' : undefined"
        >
          <div v-if="!sidebarCollapsed" class="sidebar-tabs" role="tablist" aria-label="浏览方式">
            <button
              class="sidebar-tab"
              :class="{ active: sidebarTab === 'collections' }"
              role="tab"
              :aria-selected="sidebarTab === 'collections'"
              @click="switchSidebarTab('collections')"
            >分组</button>
            <button
              class="sidebar-tab"
              :class="{ active: sidebarTab === 'categories' }"
              role="tab"
              :aria-selected="sidebarTab === 'categories'"
              @click="switchSidebarTab('categories')"
            >类别</button>
          </div>
          <button
            v-if="!sidebarCollapsed"
            class="sidebar-add"
            :title="sidebarTab === 'categories' ? '新建类别路径' : '新建分组路径'"
            :aria-label="sidebarTab === 'categories' ? '新建类别路径' : '新建分组路径'"
            @click="onSidebarAdd"
          >＋</button>
        </div>
        <div id="tree">
          <CollectionTreeNode
            v-for="c in sidebarItems"
            :key="c.id"
            :node="c"
            :active-id="sidebarCollapsed ? activeTopLevel : activeSidebarId"
            :depth="0"
            :collapsed="sidebarCollapsed"
            :drop-kind="sidebarTab === 'categories' ? 'category' : 'collection'"
            :drop-target-key="hierarchyDropTargetKey || libraryDropTargetKey"
            :hierarchy-drag-id="hierarchyDragId"
            @select="onSidebarSelect"
            @node-hover="onSidebarNodeHover"
            @folder-context="onFolderRightClick"
            @node-pointerdown="onHierarchyPointerDown"
          />
        </div>
      </aside>

      <div id="main">
        <div id="search-wrap">
          <button class="sidebar-toggle" :class="{ collapsed: sidebarCollapsed }" @click="toggleSidebar" title="折叠/展开侧边栏" aria-label="折叠/展开侧边栏" :aria-expanded="!sidebarCollapsed">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>
            </svg>
          </button>
          <input id="search" type="text" placeholder="搜索图片、GIF 或聊天记录..." :value="state.searchQuery" @input="onSearchInput" autofocus spellcheck="false">
          <button v-if="state.searchQuery" class="search-clear" title="清除搜索" aria-label="清除搜索" @click="clearSearch">×</button>
        </div>

        <div id="tagbar">
          <div class="tag-group genre-group">
            <span class="tag-category">体裁</span>
            <span
              v-for="item in genreOptions"
              :key="item.value"
              class="tag genre-tag"
              :class="{ active: state.activeGenres.includes(item.value) }"
              role="checkbox"
              tabindex="0"
              :aria-checked="state.activeGenres.includes(item.value)"
              @click="toggleGenre(item.value)"
              @keydown.enter.prevent="toggleGenre(item.value)"
              @keydown.space.prevent="toggleGenre(item.value)"
            ><svg class="genre-check" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="1.5" y="1.5" width="13" height="13" rx="3"/><path v-if="state.activeGenres.includes(item.value)" d="m4 8 2.5 2.5L12 5"/></svg>{{ item.label }}</span>
          </div>
          <div v-for="group in tagGroups" :key="group.category" class="tag-group">
            <span class="tag-category">{{ group.category }}</span>
            <span
              v-for="tag in group.tags"
              :key="tagKey(tag)"
              class="tag"
              :class="{ active: state.activeTags.has(tagKey(tag)) }"
              role="button"
              tabindex="0"
              :aria-pressed="state.activeTags.has(tagKey(tag))"
              @click="toggleTag(tag)"
              @keydown.enter.prevent="toggleTag(tag)"
              @keydown.space.prevent="toggleTag(tag)"
            >{{ tag.name }}</span>
          </div>
        </div>

        <div v-if="breadcrumb.length > 1" id="breadcrumb">
          <template v-for="(crumb, idx) in breadcrumb" :key="idx">
            <span v-if="idx > 0" class="crumb-sep">›</span>
            <button
              class="crumb"
              :class="{ current: idx === breadcrumb.length - 1 }"
              @click="onBreadcrumbClick(crumb.id)"
            >{{ crumb.name }}</button>
          </template>
        </div>

        <Pager
          v-if="state.pageCount > 1"
          :page="state.page"
          :page-count="state.pageCount"
          @go="goToPage"
        />

        <div v-if="selectMode" id="batch-bar">
          <span class="count">已选 {{ state.selectedIds.size }} 项</span>
          <button class="btn btn-sm" :disabled="state.memes.length === 0" @click="selectAllVisible">全选当前页</button>
          <button class="btn btn-sm btn-secondary" :disabled="state.selectedIds.size === 0" @click="clearSelection">取消选择</button>
          <button class="btn btn-sm" :disabled="state.selectedIds.size === 0" @click="batchAddToCollection">加入分组</button>
          <button class="btn btn-sm" :disabled="state.selectedIds.size === 0" @click="batchSetCategory">设置类别</button>
          <button class="btn btn-sm" :disabled="state.selectedIds.size === 0" @click="batchTag">打标签</button>
          <button v-if="state.activeCollection && state.activeCollection > 0" class="btn btn-sm" :disabled="state.selectedIds.size === 0" @click="batchMoveToCollection">移动到分组</button>
          <button class="btn btn-sm btn-danger" :disabled="state.selectedIds.size === 0" @click="batchDelete">批量删除</button>
        </div>

        <div id="grid-wrap" @scroll.passive="clearHoverPreview">
          <div v-if="folderCards.length" class="meme-grid folder-grid">
            <div
              v-for="child in folderCards"
              :key="'folder-' + child.id"
              class="meme-card folder-card"
              :data-folder-id="child.id"
              role="button"
              tabindex="0"
              :aria-label="'打开' + (contentTab === 'categories' ? '类别 ' : '分组 ') + child.name"
              @click="onFolderCardClick(child.id)"
              @contextmenu="onFolderCardContext($event, child.id, child.name)"
              @keydown.enter.prevent="onFolderCardClick(child.id)"
              @keydown.space.prevent="onFolderCardClick(child.id)"
            >
              <div class="folder-preview">
                <svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="var(--accent)" stroke-width="1.5"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
                <span class="folder-name">{{ child.name }}</span>
              </div>
            </div>
          </div>

          <div v-if="state.chatRecords.length" class="meme-grid chat-record-grid">
            <div
              v-for="record in state.chatRecords"
              :key="'chat-' + record.id"
              class="meme-card chat-record-card"
              role="button"
              tabindex="0"
              :aria-label="'查看聊天记录 ' + (record.name || record.title)"
              title="点击查看详情；拖到侧栏修改归属，拖出窗口发送到 QQ"
              @click="openChatRecord(record)"
              @pointerdown="onChatRecordPointerDown($event, record)"
              @keydown.enter.prevent="openChatRecord(record)"
              @keydown.space.prevent="openChatRecord(record)"
            >
              <div class="chat-record-preview">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
                  <path d="M4 4h16v12H7l-3 3V4z"/><path d="M8 8h8M8 12h5"/>
                </svg>
                <span class="chat-record-count">{{ record.node_count }} 条消息</span>
              </div>
              <span class="genre-badge">聊天记录</span>
              <span class="meme-name">{{ record.name || record.title }}</span>
            </div>
          </div>

          <drag-select
            v-model="state.selectedIds"
            :disabled="!sortEnabled && !selectMode"
            :multiple="true"
            :click-option-to-select="selectMode"
            :draggable-on-option="false"
            :click-blank-to-clear="false"
          >
            <TransitionGroup
              id="meme-grid"
              tag="div"
              name="meme-list"
              class="meme-grid"
              :class="{ 'sort-enabled': sortEnabled && canReorder(), 'select-enabled': selectMode }"
            >
              <drag-select-option
                v-for="meme in state.memes"
                :key="meme.cloud ? meme.filename : meme.id"
                :value="meme.cloud ? meme.filename : meme.id"
                :disabled="!!meme.cloud"
              >
                <div
                  class="meme-card"
                  :class="{
                    dragging: drag.dragState.active && drag.dragState.memeId === meme.id,
                    selected: state.selectedIds.has(meme.id),
                    'cloud-card': !!meme.cloud,
                    'cloud-downloading': !!cloudBusy[meme.filename],
                    'cloud-wiping': !!cloudWipe[meme.filename],
                    'sidebar-related': isMemeInHoveredSidebarNode(meme),
                  }"
                  :data-meme-id="meme.id"
                  role="button"
                  tabindex="0"
                  :aria-label="meme.name"
                  :title="meme.cloud ? '点击下载并复制云端表情' : '拖到侧栏修改归属，拖出窗口发送或复制文件'"
                  @click="handleCopy(meme, $event)"
                  @contextmenu="onMemeRightClick($event, meme)"
                  @pointerdown="onCardPointerDown($event, meme, $event.currentTarget as HTMLElement)"
                  @mouseenter="onCardMouseEnter(meme, $event)"
                  @mouseleave="onCardMouseLeave(meme)"
                  @keydown.enter.prevent="handleCopy(meme)"
                  @keydown.space.prevent="handleCopy(meme)"
                >
                  <img
                    :src="memeSrc(meme)"
                    :alt="meme.name"
                    :draggable="false"
                    loading="lazy"
                    @error="onImgError"
                    @load="onImgLoad"
                  >
                  <span v-if="state.selectedIds.has(meme.id)" class="select-badge">✓</span>
                  <button
                    v-if="!selectMode && !sortEnabled && !meme.cloud"
                    class="fav-btn"
                    :class="{ active: meme.favorited }"
                    :aria-label="meme.favorited ? '取消收藏' : '收藏'"
                    :title="meme.favorited ? '取消收藏' : '收藏'"
                    @click.stop="quickFavorite(meme)"
                    @pointerdown.stop
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
                  </button>
                  <span v-if="meme.from_stego" class="gif-badge stego-badge">隐写导入</span>
                  <span v-else-if="meme.is_animated" class="gif-badge">{{ meme.is_gif ? 'GIF' : 'WebP' }}</span>
                  <span v-if="meme.cloud" class="cloud-badge" aria-label="云端">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"/></svg>
                  </span>
                  <span class="meme-name">{{ meme.name }}</span>
                </div>
              </drag-select-option>
            </TransitionGroup>
          </drag-select>

          <div v-if="state.memes.length === 0 && state.chatRecords.length === 0 && !state.loading" id="empty">
            <svg class="empty-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 7a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>
              <path d="M8.5 13.5h7M8.5 16.5h4.5"/>
            </svg>
            <div class="text">{{ state.activeGenres.length ? '没有符合条件的内容' : '请选中至少一种体裁' }}</div>
            <button v-if="state.activeGenres.some(genre => genre !== 'chat_record')" class="btn btn-primary" @click="showImportMenu()">导入表情包</button>
          </div>
        </div>
      </div>
    </div>
  </div>

  <CollectionBuilder />
  <ImportMenu ref="importMenu" @imported="onImportDone" @importing="onImporting" @notion="showNotionImport" @duplicates="revealExistingImports" @large-pending="handleLargeMemeImport" />
  <ImportProgressOverlay ref="importProgress" @imported="onImportDone" @duplicates="revealExistingImports" />
  <NotionImportDialog ref="notionImportDialog" @imported="onImportDone" />
  <SyncOverlay ref="syncOverlay" @synced="onSyncDone" />
  <MemeDetails
    ref="memeDetails"
    @saved="onMemeDetailsSaved"
    @tag-renamed="onTagRenamed"
    @structure-created="onMemeStructureCreated"
  />
  <ChatRecordDetails
    ref="chatRecordDetails"
    @copied="showToast('发送命令已复制')"
    @saved="onChatRecordSaved"
    @request-create-collection="createChatCollection"
    @request-create-category="createChatCategory"
    @request-delete="deleteChatRecord"
  />
  <TagEditor ref="tagEditor" />
  <UpdateDialog ref="updateDialog" />
  <InputDialog ref="inputDialog" />
  <ConfirmDialog ref="confirmDialog" />
  <HierarchyDeleteDialog ref="hierarchyDeleteDialog" />
  <LargeMemeImportDialog ref="largeMemeImportDialog" />
  <SimilarImportDialog ref="similarImportDialog" />
  <SetupGuide ref="setupGuide" @open-import-menu="showImportMenu" />
  <ContextMenu
    :visible="ctx.visible.value" :x="ctx.x.value" :y="ctx.y.value"
    :items="ctx.items.value" :trigger="ctx.trigger.value"
    :submenu-visible="ctx.submenuVisible.value" :submenu-items="ctx.submenuItems.value"
    :submenu-x="ctx.submenuX.value" :submenu-y="ctx.submenuY.value"
    @action="onCtxAction" @close="ctx.hide"
    @show-submenu="onShowSubmenu" @hide-submenu="ctx.hideSubmenu"
  />

  <div id="drop-overlay" :class="{ 'drag-over': dragOver }">
    <div class="drop-content">
      <div class="drop-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/></svg>
      </div>
      <div class="drop-text">拖放图片或聊天记录到此处导入</div>
    </div>
  </div>

  <div id="toast" role="status" aria-live="polite"></div>
  <div id="loading" :class="{ show: state.loading }"><div class="spinner"></div></div>

  <Transition name="hover-preview-fade">
    <div v-if="hoverPreview" class="hover-preview" :style="{ left: hoverPreviewPos.x + 'px', top: hoverPreviewPos.y + 'px' }">
      <img :src="previewSrc(hoverPreview)" :alt="hoverPreview.name">
    </div>
  </Transition>

  <Transition name="startup-fade">
    <div v-if="startupAnim" id="startup-anim" :style="{ background: state.startupBgColor }" @click="dismissStartupAnim">
      <video v-if="startupVideoReady" :src="startupVideoSrc" autoplay muted playsinline @ended="dismissStartupAnim"></video>
    </div>
  </Transition>
</template>
