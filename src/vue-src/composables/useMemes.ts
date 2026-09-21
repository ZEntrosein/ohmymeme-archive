import { reactive, ref, readonly } from 'vue'
import { api } from '../utils/api'
import type { ChatRecordSummary, Genre, Meme, TagRecord } from '../types'

const MEME_PAGE = 200

const state = reactive({
  memes: [] as Meme[],
  chatRecords: [] as ChatRecordSummary[],
  chatTotal: 0,
  collections: [] as any[],
  categories: [] as any[],
  activeCollection: null as number | null,
  activeCategory: null as number | null,
  activeTags: new Set<string>(),
  activeGenre: 'all' as Genre,
  selectedIds: new Set<number>(),
  allTags: [] as TagRecord[],
  searchQuery: '',
  total: 0,
  page: 1,
  pageCount: 1,
  loading: false,
  showStartupAnimation: true,
  startupBgColor: '#000000',
})

let searchGen = 0

export function tagKey(tag: TagRecord): string {
  return `${tag.category || ''}\u001f${tag.name}`.toLocaleLowerCase()
}

function legacyTags(tags: unknown): TagRecord[] {
  if (!Array.isArray(tags)) return []
  return tags
    .filter((tag): tag is string => typeof tag === 'string' && !!tag.trim())
    .map(name => ({ name, category: '' }))
}

export function useMemes() {
  function warmLaterPages(
    offset: number,
    selectedTags: TagRecord[],
    query = state.searchQuery,
    collectionId = state.activeCollection,
    categoryId = state.activeCategory,
    genre = state.activeGenre,
  ) {
    // 后端会先取消上一个查询的预热任务；设置未启用或没有后续页时只执行取消。
    void api(
      'start_page_cache_warmup', query, selectedTags, collectionId,
      offset, MEME_PAGE, state.total, categoryId, genre,
    )
  }

  async function waitForPywebview(): Promise<void> {
    while (typeof window.pywebview === 'undefined' || !window.pywebview.api) {
      await new Promise(r => setTimeout(r, 100))
    }
  }

  async function loadInitData() {
    await waitForPywebview()
    const data = await api('get_init_data')
    if (data) {
      state.memes = data.memes || []
      state.allTags = Array.isArray(data.tag_records) ? data.tag_records : legacyTags(data.tags)
      state.collections = data.collections || []
      state.categories = data.categories || []
      state.page = 1
      const library = await api('search_library', '', [], null, 0, MEME_PAGE, null, state.activeGenre)
      if (library && Array.isArray(library.memes) && Array.isArray(library.chat_records)) {
        state.memes = library.memes
        state.chatRecords = library.chat_records
        state.chatTotal = Number(library.chat_total) || 0
        state.total = Number(library.total) || 0
      } else {
        state.total = await api('count_memes', '', [], state.activeCollection, state.activeCategory) || state.memes.length
      }
      state.pageCount = Math.max(1, Math.ceil(state.total / MEME_PAGE))
      warmLaterPages(0, [])
      state.showStartupAnimation = data.show_startup_animation !== false
      state.startupBgColor = data.startup_bg_color || '#000000'
    }
  }

  async function search(resetPage = true) {
    const gen = ++searchGen
    if (resetPage) state.page = 1
    state.loading = true
    // 换页/筛选/切分组即清空选择（「全选当前页」语义）
    state.selectedIds = new Set()
    const offset = (state.page - 1) * MEME_PAGE
    const selectedTags = state.allTags.filter(tag => state.activeTags.has(tagKey(tag)))
    // 查询条件一变化就停止旧预热；即使本次搜索失败，也不能继续读取旧结果。
    void api('cancel_page_cache_warmup')
    try {
      const result = await api(
        'search_library', state.searchQuery, selectedTags, state.activeCollection,
        offset, MEME_PAGE, state.activeCategory, state.activeGenre,
      )
      if (gen !== searchGen) return
      // 桥接失败（null/非数组）时保留当前分页与结果，避免暂时性故障触发空页回退或清空网格
      if (!result || !Array.isArray(result.memes) || !Array.isArray(result.chat_records) || typeof result.total !== 'number') return
      state.total = result.total
      state.pageCount = Math.max(1, Math.ceil(state.total / MEME_PAGE))
      state.memes = result.memes
      state.chatRecords = result.chat_records
      warmLaterPages(offset, selectedTags)
      state.chatTotal = Number(result.chat_total) || 0
      // 删除等操作使当前页变空时回退到可用末页，避免停留在空页显示「没有表情包」
      if (!resetPage && state.memes.length === 0 && state.chatRecords.length === 0 && state.page > 1) {
        const target = Math.max(1, Math.min(state.page, state.pageCount))
        if (target !== state.page) { state.page = target; return search(false) }
      }
    } catch (e) {
      if (gen !== searchGen) return
    } finally {
      if (gen === searchGen) state.loading = false
    }
  }

  async function goToPage(p: number) {
    if (p < 1 || p > state.pageCount || p === state.page) return
    state.page = p
    await search(false)
    if (state.memes.length === 0 && state.chatRecords.length === 0 && state.page > 1) {
      state.page = Math.max(1, Math.min(state.page, state.pageCount))
      await search(false)
    }
  }

  function setSearch(q: string) { state.searchQuery = q }
  function setGenre(genre: Genre) {
    if (state.activeGenre === genre) return
    state.activeGenre = genre
    if (genre === 'chat_record') {
      state.activeTags = new Set()
      state.activeCollection = null
      state.activeCategory = null
    }
    search()
  }
  function toggleTag(tag: TagRecord) {
    if (state.activeGenre === 'chat_record') state.activeGenre = 'all'
    const key = tagKey(tag)
    if (state.activeTags.has(key)) state.activeTags.delete(key)
    else state.activeTags.add(key)
    search()
  }
  function setActiveCollection(id: number | null) {
    if (state.activeGenre === 'chat_record') state.activeGenre = 'all'
    if (state.activeCollection === id) state.activeCollection = null
    else state.activeCollection = id
    state.activeCategory = null
    search()
  }
  function setActiveCategory(id: number | null) {
    if (state.activeGenre === 'chat_record') state.activeGenre = 'all'
    if (state.activeCategory === id) state.activeCategory = null
    else state.activeCategory = id
    state.activeCollection = null
    search()
  }
  async function refreshTags() {
    let tags: TagRecord[] | null = null
    try { tags = await api('get_tag_records') } catch { return }
    // 桥接失败（null/非数组）时保留现有标签与筛选，避免暂时性故障清掉用户筛选
    if (!Array.isArray(tags)) return
    state.allTags = tags
    // 清理已不存在的激活标签（如删除标签下最后一张图后孤儿标签被清理），否则筛选永久卡死
    let pruned = false
    const available = new Set(state.allTags.map(tagKey))
    for (const t of [...state.activeTags]) {
      if (!available.has(t)) { state.activeTags.delete(t); pruned = true }
    }
    if (pruned) await search()
  }
  async function refreshCollections() {
    let items: any[] | null = null
    try { items = await api('get_collections') } catch { return }
    // 桥接失败（null/非数组）时保留现有分组树与筛选，避免暂时性故障复位用户分组
    if (!Array.isArray(items)) return
    state.collections = items
    // 当前分组已被后端隐式删除（如同步 push 时 manifest 清理空分组）时复位到「全部」，避免筛选永久空显
    const ac = state.activeCollection
    if (ac && ac > 0 && !_collectionExists(state.collections, ac)) {
      state.activeCollection = null
      await search()
    }
  }
  async function refreshCategories() {
    let items: any[] | null = null
    try { items = await api('get_categories') } catch { return }
    if (!Array.isArray(items)) return
    state.categories = items
    const ac = state.activeCategory
    if (ac && ac > 0 && !_collectionExists(state.categories, ac)) {
      state.activeCategory = null
      await search()
    }
  }
  async function copyMeme(id: number): Promise<boolean> {
    const result = await api('copy_meme', id)
    return !!result?.ok
  }
  async function reorderMemes(orderedIds: number[]): Promise<boolean> {
    const collectionId = state.activeCollection && state.activeCollection > 0 ? state.activeCollection : null
    const result = collectionId
      ? await api('reorder_collection_members', collectionId, orderedIds)
      : await api('reorder_memes', orderedIds)
    return !!result
  }
  function setMemes(newMemes: Meme[]) { state.memes = newMemes }

  function selectAllVisible() { state.selectedIds = new Set(state.memes.map(m => m.id)) }

  function _collectionExists(items: any[], id: number): boolean {
    for (const c of items) {
      if (c.id === id) return true
      if (c.children && _collectionExists(c.children, id)) return true
    }
    return false
  }
  function clearSelection() { state.selectedIds = new Set() }

  function canReorder(): boolean {
    const q = state.searchQuery.trim()
    if (state.activeGenre === 'chat_record' || state.chatTotal > 0) return false
    if (q || state.activeTags.size > 0) return false
    if (state.activeCategory !== null) return false
    // 全部/未分组/正 ID 分组可排序；收藏夹/最近使用（-2/-3）不可排
    return state.activeCollection === null || state.activeCollection === -4 || state.activeCollection > 0
  }

  async function startNativeDrag(memeId: number): Promise<boolean> {
    try { return !!await api('start_native_drag', memeId) } catch { return false }
  }

  return {
    state,
    setMemes,
    search,
    goToPage,
    setSearch,
    setGenre,
    toggleTag,
    setActiveCollection,
    setActiveCategory,
    refreshTags,
    refreshCollections,
    refreshCategories,
    copyMeme,
    reorderMemes,
    canReorder,
    startNativeDrag,
    selectAllVisible,
    clearSelection,
    loadInitData,
    waitForPywebview,
    MEME_PAGE,
  }
}
