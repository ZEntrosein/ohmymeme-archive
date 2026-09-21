<script setup lang="ts">
import { computed, nextTick, onUnmounted, reactive, ref } from 'vue'
import { api, rememberFocus, restoreFocus, trapTabFocus } from '../utils/api'

const emit = defineEmits<{
  imported: []
}>()

const visible = ref(false)
const loading = ref(false)
const testing = ref(false)
const running = ref(false)
const hasProgress = ref(false)
const progress = ref(0)
const statusKind = ref('')
const statusText = ref('')
const properties = ref<Array<{ name: string, type: string }>>([])
const boxEl = ref<HTMLElement>()
const tokenEl = ref<HTMLInputElement>()

type TagMapping = { category: string, property: string }

const settings = reactive({
  notion_token: '',
  notion_database_id: '',
  notion_data_source_id: '',
  notion_media_property: '',
  notion_title_property: '',
  notion_tags_property: '',
  notion_tag_mappings: [] as TagMapping[],
  notion_collection_property: '',
  notion_version: '2026-03-11',
})

let pollTimer: ReturnType<typeof setInterval> | null = null

const progressLabel = computed(() => running.value ? `${progress.value}%` : '')

function showToast(msg: string) {
  const el = document.getElementById('toast')
  if (!el) return
  el.textContent = msg
  el.classList.add('show')
  setTimeout(() => el.classList.remove('show'), 1800)
}

function applySettings(saved: any) {
  settings.notion_token = String(saved?.notion_token || '')
  settings.notion_database_id = String(saved?.notion_database_id || '')
  settings.notion_data_source_id = String(saved?.notion_data_source_id || '')
  settings.notion_media_property = String(saved?.notion_media_property || '')
  settings.notion_title_property = String(saved?.notion_title_property || '')
  settings.notion_tags_property = String(saved?.notion_tags_property || '')
  const mappings = Array.isArray(saved?.notion_tag_mappings)
    ? saved.notion_tag_mappings.filter((item: any) => item && typeof item === 'object' && String(item.property || '').trim())
    : []
  settings.notion_tag_mappings = mappings.map((item: any) => ({
    category: String(item.category || item.property || '').trim(),
    property: String(item.property || '').trim(),
  }))
  if (!settings.notion_tag_mappings.length && settings.notion_tags_property.trim()) {
    settings.notion_tag_mappings = [{
      category: settings.notion_tags_property.trim(),
      property: settings.notion_tags_property.trim(),
    }]
  }
  settings.notion_collection_property = String(saved?.notion_collection_property || '')
  settings.notion_version = String(saved?.notion_version || '2026-03-11')
}

async function open() {
  rememberFocus()
  visible.value = true
  loading.value = true
  statusKind.value = ''
  try {
    applySettings(await api('get_settings'))
    const state = await api('get_notion_import_progress')
    if (state && state.status !== 'idle') {
      updateProgress(state)
      if (state.status === 'running') beginPolling()
    }
  } finally {
    loading.value = false
    await nextTick()
    tokenEl.value?.focus()
  }
}

function close() {
  visible.value = false
  restoreFocus()
}

function validate(requireMedia = false) {
  if (!settings.notion_token.trim()) {
    showToast('请填写 Notion Integration Token')
    tokenEl.value?.focus()
    return false
  }
  if (!settings.notion_database_id.trim() && !settings.notion_data_source_id.trim()) {
    showToast('请填写数据库 URL 或 Data source ID')
    return false
  }
  if (requireMedia && !settings.notion_media_property.trim()) {
    showToast('请选择图片属性，或先测试连接自动识别')
    return false
  }
  return true
}

function suggest(types: string[]) {
  return properties.value.find(item => types.includes(item.type))?.name || ''
}

function requestSettings() {
  return {
    ...settings,
    notion_tags_property: '',
    notion_tag_mappings: settings.notion_tag_mappings
      .map(item => ({
        category: item.category.trim() || item.property.trim(),
        property: item.property.trim(),
      }))
      .filter(item => item.property),
  }
}

function addTagMapping(property = '') {
  settings.notion_tag_mappings.push({ category: property, property })
}

function removeTagMapping(index: number) {
  settings.notion_tag_mappings.splice(index, 1)
}

function fillTagCategory(mapping: TagMapping) {
  if (!mapping.category.trim()) mapping.category = mapping.property.trim()
}

async function testConnection() {
  if (!validate()) return
  testing.value = true
  statusKind.value = ''
  statusText.value = '正在连接 Notion...'
  try {
    const result = await api('notion_test_connection', requestSettings())
    if (!result?.ok) {
      statusKind.value = 'error'
      statusText.value = result?.error || '连接失败'
      return
    }
    properties.value = Array.isArray(result.properties) ? result.properties : []
    if (!settings.notion_data_source_id.trim()) settings.notion_data_source_id = result.data_source_id || ''
    if (!settings.notion_media_property.trim()) settings.notion_media_property = suggest(['files'])
    if (!settings.notion_title_property.trim()) settings.notion_title_property = suggest(['title'])
    if (!settings.notion_tag_mappings.length) {
      const property = suggest(['multi_select', 'select', 'status', 'relation'])
      if (property) addTagMapping(property)
    }
    if (!settings.notion_collection_property.trim()) settings.notion_collection_property = suggest(['select', 'status'])
    statusKind.value = 'ok'
    statusText.value = `连接成功，已识别 ${properties.value.length} 个属性`
  } finally {
    testing.value = false
  }
}

function beginPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(pollImport, 350)
}

async function startImport() {
  if (!validate(true)) return
  const payload = requestSettings()
  await api('save_settings', payload)
  hasProgress.value = true
  progress.value = 0
  running.value = true
  statusKind.value = ''
  statusText.value = '正在启动...'
  const result = await api('start_notion_import', payload)
  if (!result?.ok) {
    running.value = false
    statusKind.value = 'error'
    statusText.value = result?.error || '启动失败'
    return
  }
  beginPolling()
}

function updateProgress(state: any) {
  hasProgress.value = true
  progress.value = Math.max(0, Math.min(100, Number(state.progress) || 0))
  running.value = state.status === 'running'
  statusText.value = state.error || state.message || ''
  statusKind.value = state.status === 'done' ? 'ok' : state.status === 'error' ? 'error' : ''
}

async function pollImport() {
  const state = await api('get_notion_import_progress')
  if (!state) return
  updateProgress(state)
  if (state.status === 'running') return
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  // 取消或报错前也可能已有条目成功入库；所有终态都重新读取主界面数据。
  emit('imported')
}

async function cancelImport() {
  await api('cancel_notion_import')
  statusText.value = '正在取消...'
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') { e.stopPropagation(); close() }
  else if (boxEl.value) trapTabFocus(boxEl.value, e)
}

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

defineExpose({ open, close })
</script>

<template>
  <div v-if="visible" class="notion-overlay" @click.self="close">
    <div ref="boxEl" class="notion-box" role="dialog" aria-modal="true" aria-labelledby="notion-title" @keydown="onKeydown">
      <div class="notion-header">
        <div>
          <div id="notion-title" class="notion-title">从 Notion 数据库导入</div>
          <div class="notion-subtitle">只读获取 Files &amp; media 图片并保存到本地，不会修改 Notion 数据。</div>
        </div>
        <button class="notion-close" aria-label="关闭" @click="close">×</button>
      </div>

      <div class="notion-content" :class="{ loading }">
        <label class="notion-field notion-wide">
          <span>Integration Token</span>
          <input ref="tokenEl" v-model="settings.notion_token" type="password" placeholder="ntn_..." autocomplete="off">
        </label>
        <label class="notion-field notion-wide">
          <span>数据库 URL 或 ID</span>
          <input v-model="settings.notion_database_id" placeholder="粘贴 Notion 数据库链接" autocomplete="off">
        </label>
        <label class="notion-field notion-wide">
          <span>Data source ID <small>多数据源数据库必填</small></span>
          <input v-model="settings.notion_data_source_id" placeholder="通常可留空自动解析" autocomplete="off">
        </label>

        <div class="notion-grid">
          <label class="notion-field">
            <span>图片属性 <b>必填</b></span>
            <input v-model="settings.notion_media_property" list="notion-media-properties" placeholder="例如：图片">
            <datalist id="notion-media-properties">
              <option v-for="item in properties.filter(p => p.type === 'files')" :key="item.name" :value="item.name" />
            </datalist>
          </label>
          <label class="notion-field">
            <span>标题属性 <small>可选</small></span>
            <input v-model="settings.notion_title_property" list="notion-title-properties" placeholder="留空自动识别 Title">
            <datalist id="notion-title-properties">
              <option v-for="item in properties.filter(p => p.type === 'title')" :key="item.name" :value="item.name" />
            </datalist>
          </label>
          <label class="notion-field">
            <span>分组属性 <small>可选</small></span>
            <input v-model="settings.notion_collection_property" list="notion-group-properties" placeholder="Select、Status 或 Relation">
            <datalist id="notion-group-properties">
              <option v-for="item in properties.filter(p => ['select', 'status', 'multi_select', 'relation'].includes(p.type))" :key="item.name" :value="item.name" />
            </datalist>
          </label>
        </div>

        <div class="notion-tag-mappings">
          <div class="notion-mapping-heading">
            <span>标签列 <small>可选；每列对应一种标签种类</small></span>
            <button type="button" class="notion-add-mapping" @click="addTagMapping()">＋ 添加标签列</button>
          </div>
          <div v-if="!settings.notion_tag_mappings.length" class="notion-mapping-empty">
            尚未导入标签列
          </div>
          <div v-for="(mapping, index) in settings.notion_tag_mappings" :key="index" class="notion-mapping-row">
            <label class="notion-field">
              <span>标签种类</span>
              <input v-model="mapping.category" placeholder="默认使用属性标题">
            </label>
            <label class="notion-field">
              <span>Notion 属性</span>
              <input v-model="mapping.property" list="notion-tags-properties" placeholder="Multi-select、Select 或 Relation" @change="fillTagCategory(mapping)">
            </label>
            <button type="button" class="notion-remove-mapping" aria-label="删除标签列" @click="removeTagMapping(index)">×</button>
          </div>
          <datalist id="notion-tags-properties">
            <option v-for="item in properties.filter(p => ['multi_select', 'select', 'status', 'rich_text', 'relation'].includes(p.type))" :key="item.name" :value="item.name" />
          </datalist>
        </div>

        <div v-if="hasProgress" class="notion-progress">
          <div class="notion-bar-wrap"><div class="notion-bar" :style="{ width: progress + '%' }"></div></div>
          <span>{{ progressLabel }}</span>
        </div>
        <div v-if="statusText" class="notion-status" :class="statusKind">{{ statusText }}</div>
      </div>

      <div class="notion-actions">
        <button class="btn btn-secondary" :disabled="loading || testing || running" @click="testConnection">
          {{ testing ? '连接中...' : '测试连接' }}
        </button>
        <span class="notion-spacer"></span>
        <button v-if="running" class="btn btn-secondary" @click="cancelImport">取消导入</button>
        <button class="btn btn-secondary" @click="close">{{ running ? '后台运行' : '关闭' }}</button>
        <button class="btn btn-primary" :disabled="loading || testing || running" @click="startImport">开始导入</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.notion-overlay {
  position: fixed;
  inset: 0;
  z-index: 520;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(0, 0, 0, 0.62);
  animation: fadeIn 0.15s ease;
}

.notion-box {
  width: min(620px, calc(100vw - 48px));
  max-height: calc(100vh - 48px);
  overflow: auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
}

.notion-header {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 20px 22px 16px;
  border-bottom: 1px solid var(--border);
}

.notion-title { font-size: 16px; font-weight: 650; color: var(--fg); }
.notion-subtitle { margin-top: 5px; font-size: 12px; line-height: 1.5; color: var(--muted); }
.notion-close { margin-left: auto; padding: 0 4px; border: 0; background: transparent; color: var(--muted); font-size: 22px; cursor: pointer; }
.notion-close:hover { color: var(--fg); }

.notion-content {
  padding: 18px 22px;
  transition: opacity 0.15s;
}

.notion-content.loading { opacity: 0.55; pointer-events: none; }
.notion-wide { margin-bottom: 12px; }
.notion-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.notion-field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.notion-field span { font-size: 12px; color: var(--fg-secondary); }
.notion-field small { color: var(--muted); font-size: 10.5px; font-weight: 400; }
.notion-field b { color: var(--primary); font-size: 10.5px; font-weight: 500; }
.notion-field input {
  box-sizing: border-box;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  outline: none;
  background: var(--bg);
  color: var(--fg);
  font: inherit;
  font-size: 12.5px;
}
.notion-field input:focus { border-color: var(--primary); }

.notion-tag-mappings { margin-top: 14px; padding-top: 13px; border-top: 1px solid var(--border); }
.notion-mapping-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 9px; color: var(--fg-secondary); font-size: 12px; }
.notion-mapping-heading small { color: var(--muted); font-size: 10.5px; }
.notion-add-mapping { padding: 4px 7px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--bg); color: var(--fg-secondary); font-size: 11px; cursor: pointer; }
.notion-add-mapping:hover { border-color: var(--primary); color: var(--fg); }
.notion-mapping-empty { padding: 10px; border: 1px dashed var(--border); border-radius: var(--radius-sm); color: var(--muted); font-size: 11.5px; text-align: center; }
.notion-mapping-row { display: grid; grid-template-columns: minmax(0, .8fr) minmax(0, 1.2fr) 28px; align-items: end; gap: 9px; margin-top: 9px; }
.notion-remove-mapping { width: 28px; height: 33px; padding: 0; border: 1px solid var(--border); border-radius: var(--radius-sm); background: transparent; color: var(--muted); font-size: 17px; cursor: pointer; }
.notion-remove-mapping:hover { border-color: var(--danger, #f87171); color: var(--danger, #f87171); }

.notion-progress { display: flex; align-items: center; gap: 10px; margin-top: 16px; color: var(--muted); font-size: 11px; }
.notion-bar-wrap { flex: 1; height: 7px; overflow: hidden; border-radius: 4px; background: var(--bg); }
.notion-bar { height: 100%; border-radius: 4px; background: var(--accent); transition: width 0.3s; }
.notion-status { margin-top: 8px; color: var(--fg-secondary); font-size: 12px; line-height: 1.5; word-break: break-word; }
.notion-status.ok { color: var(--success, #34d399); }
.notion-status.error { color: var(--danger, #f87171); }

.notion-actions { display: flex; align-items: center; gap: 8px; padding: 14px 22px 18px; border-top: 1px solid var(--border); }
.notion-spacer { flex: 1; }

@media (max-width: 560px) {
  .notion-overlay { padding: 12px; }
  .notion-box { width: calc(100vw - 24px); max-height: calc(100vh - 24px); }
  .notion-grid { grid-template-columns: 1fr; }
  .notion-mapping-row { grid-template-columns: 1fr 28px; }
  .notion-mapping-row .notion-field:first-child { grid-column: 1 / 2; }
  .notion-mapping-row .notion-field:nth-child(2) { grid-column: 1 / 2; }
  .notion-remove-mapping { grid-column: 2; grid-row: 1 / 3; align-self: center; }
  .notion-actions { flex-wrap: wrap; }
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
</style>
