<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { api, rememberFocus, restoreFocus, trapTabFocus } from '../utils/api'

interface ChatMessageView {
  depth: number
  sender: string
  sender_id: string
  time: number | string
  preview: string
  segment_types: string[]
}

interface ChatRecordDetail {
  id: number
  title: string
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
  captured_at: string
  created_at: string
  updated_at: string
  messages: ChatMessageView[]
  send_command: string
}

const emit = defineEmits<{ copied: []; requestDelete: [recordId: number] }>()
const visible = ref(false)
const loading = ref(false)
const error = ref('')
const detail = ref<ChatRecordDetail | null>(null)
const boxEl = ref<HTMLElement>()
const closeEl = ref<HTMLButtonElement>()
let requestGeneration = 0

async function open(recordId: number) {
  const generation = ++requestGeneration
  rememberFocus()
  visible.value = true
  loading.value = true
  error.value = ''
  detail.value = null
  try {
    const result = await api('get_chat_record_details', recordId)
    if (generation !== requestGeneration) return
    if (!result?.ok) {
      error.value = result?.error || '聊天记录读取失败'
      return
    }
    detail.value = result
    await nextTick()
    closeEl.value?.focus()
  } catch (_) {
    if (generation === requestGeneration) error.value = '聊天记录读取失败'
  } finally {
    if (generation === requestGeneration) loading.value = false
  }
}

function close() {
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

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.preventDefault()
    close()
  } else if (boxEl.value) {
    trapTabFocus(boxEl.value, e)
  }
}

defineExpose({ open, close })
</script>

<template>
  <div v-if="visible" class="chat-details-overlay" @click.self="close">
    <div ref="boxEl" class="chat-details-box" role="dialog" aria-modal="true" aria-labelledby="chat-details-title" @keydown="onKeydown">
      <header class="chat-details-header">
        <div>
          <div id="chat-details-title" class="chat-details-kicker">聊天记录详情</div>
          <h2>{{ detail?.title || '正在读取…' }}</h2>
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
            <div><dt>来源</dt><dd>{{ detail.peer_label }}</dd></div>
            <div><dt>会话 ID</dt><dd>{{ detail.peer_id || '未知' }}</dd></div>
            <div><dt>消息 ID</dt><dd>{{ detail.source_message_id }}</dd></div>
            <div><dt>保存时间</dt><dd>{{ formatTime(detail.updated_at) }}</dd></div>
          </dl>
          <div class="chat-command-help">
            <span>在目标 QQ 会话中发送</span>
            <code>{{ detail.send_command }}</code>
            <button class="btn btn-primary" type="button" @click="copyCommand">复制发送命令</button>
            <button class="btn btn-danger" type="button" @click="requestDelete">删除聊天记录</button>
          </div>
        </aside>

        <section class="chat-message-list" aria-label="聊天记录内容">
          <article
            v-for="(message, index) in detail.messages"
            :key="index"
            class="chat-message"
            :style="{ marginLeft: Math.min(message.depth, 4) * 18 + 'px' }"
          >
            <div class="chat-message-head">
              <strong>{{ message.sender }}</strong>
              <time v-if="message.time">{{ formatTime(message.time) }}</time>
            </div>
            <p>{{ message.preview || '[空消息]' }}</p>
            <span v-if="message.depth" class="nested-label">第 {{ message.depth + 1 }} 层记录</span>
          </article>
          <div v-if="detail.messages.length === 0" class="chat-details-state">归档结构完整，但没有可预览的消息节点。</div>
        </section>
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

.chat-message {
  margin-bottom: 10px;
  padding: 10px 12px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 9px;
}

.chat-message-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  color: var(--fg);
  font-size: 12px;
}

.chat-message-head time { color: var(--muted); font-size: 10px; }
.chat-message p { margin: 6px 0 0; color: var(--fg-secondary); font-size: 12px; line-height: 1.55; white-space: pre-wrap; }
.nested-label { display: inline-block; margin-top: 6px; color: var(--primary-strong); font-size: 9px; }
.chat-details-state { margin: auto; padding: 24px; color: var(--fg-secondary); }
.chat-details-state.error { color: var(--danger); }

@media (max-width: 720px) {
  .chat-details-content { grid-template-columns: 1fr; }
  .chat-details-meta { max-height: 230px; border-right: 0; border-bottom: 1px solid var(--border); }
}
</style>
