// 根据已保存的配置更新主题与窗口底色。
export function applyTheme(value: unknown) {
  const theme = value === 'light' ? 'light' : 'dark'
  document.documentElement.dataset.theme = theme
  document.documentElement.style.background = theme === 'light' ? '#f8fafc' : '#0d0d0f'
  document.documentElement.style.colorScheme = theme
}

export async function api(method: string, ...args: any[]): Promise<any> {
  try {
    if (typeof pywebview === 'undefined' || !pywebview.api) return null
    return await pywebview.api[method](...args)
  } catch (e) {
    console.error('API error:', method, e)
    return null
  }
}

export function esc(s: string): string {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

// 轻量 Markdown 渲染（与原始实现一致），用于更新日志等富文本
export function renderMarkdown(md: string): string {
  if (!md) return ''
  let s = esc(md)
  s = s.replace(/```([\w+-]*)\n([\s\S]*?)```/g, (m, lang, code) => '<pre class="md-pre"><code>' + code + '</code></pre>')
  s = s.replace(/`([^`\n]+)`/g, '<code class="md-code">$1</code>')
  s = s.replace(/^##### (.*)$/gm, '<h5 class="md-h">$1</h5>')
  s = s.replace(/^#### (.*)$/gm, '<h4 class="md-h">$1</h4>')
  s = s.replace(/^### (.*)$/gm, '<h3 class="md-h">$1</h3>')
  s = s.replace(/^## (.*)$/gm, '<h2 class="md-h">$1</h2>')
  s = s.replace(/^# (.*)$/gm, '<h1 class="md-h">$1</h1>')
  s = s.replace(/^&gt; (.*)$/gm, '<blockquote class="md-quote">$1</blockquote>')
  s = s.replace(/^[-*] (.*)$/gm, '<li class="md-li">$1</li>')
  s = s.replace(/^\d+\. (.*)$/gm, '<li class="md-li">$1</li>')
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  s = s.replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>')
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<span class="md-link">$1</span>')
  s = s.replace(/^-{3,}$/gm, '<hr class="md-hr">')
  s = s.replace(/\n/g, '<br>')
  s = s.replace(/(<\/?(?:h[1-5]|pre|blockquote|li|hr)[^>]*>)\s*<br>/g, '$1')
  s = s.replace(/<br>\s*(<\/?(?:h[1-5]|pre|blockquote|li|hr)[^>]*>)/g, '$1')
  return s
}

// 仅将同一次主指针在遮罩上按下、松开产生的点击视为关闭请求。
export function createBackdropClickGuard(onClose: () => void) {
  let pointerId: number | null = null
  let releasedOnBackdrop = false

  function reset() {
    pointerId = null
    releasedOnBackdrop = false
  }

  function onPointerDown(e: PointerEvent) {
    reset()
    if (e.isPrimary && e.button === 0 && e.target === e.currentTarget) {
      pointerId = e.pointerId
    }
  }

  function onPointerUp(e: PointerEvent) {
    releasedOnBackdrop = pointerId === e.pointerId
      && e.button === 0 && e.target === e.currentTarget
    pointerId = null
  }

  function onClick(e: MouseEvent) {
    const shouldClose = releasedOnBackdrop && e.target === e.currentTarget
    reset()
    if (shouldClose) onClose()
  }

  return { onPointerDown, onPointerUp, onClick, reset }
}

// 捕获保存组合键，避免 Ctrl+Enter 同时触发输入框或表单自己的 Enter 行为。
export function handleDetailSaveShortcut(e: KeyboardEvent, onSave: () => unknown) {
  if (e.isComposing || e.keyCode === 229 || e.altKey || !(e.ctrlKey || e.metaKey)) return
  if (e.key !== 'Enter' && e.key.toLocaleLowerCase() !== 's') return
  e.preventDefault()
  e.stopPropagation()
  if (!e.repeat) void onSave()
}

// 普通 Enter 按页面顺序继续编辑；Shift+Enter、候选确认和输入法保持原行为。
export function focusNextDetailInput(box: HTMLElement, e: KeyboardEvent) {
  if (
    e.key !== 'Enter' || e.defaultPrevented || e.isComposing || e.keyCode === 229
    || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey
  ) return false
  const target = e.target
  const selector = 'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]):not([type="button"]):not([type="submit"]):not([type="reset"]):not([type="file"]), textarea, select'
  if (!(target instanceof HTMLElement) || !target.matches(selector) || target.tagName === 'SELECT') return false
  const fields = Array.from(box.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(selector))
    .filter(field => !field.matches(':disabled') && !('readOnly' in field && field.readOnly)
      && field.tabIndex >= 0 && field.getClientRects().length > 0 && !field.closest('[inert]'))
  const index = fields.indexOf(target as HTMLInputElement | HTMLTextAreaElement)
  if (index < 0) return false
  e.preventDefault()
  fields[index + 1]?.focus()
  return true
}

// ── 弹窗焦点管理（无障碍） ──
let _focusTarget: HTMLElement | null = null

// 记住打开弹窗前的焦点元素，关闭后归还
export function rememberFocus() {
  _focusTarget = document.activeElement instanceof HTMLElement ? document.activeElement : null
}

export function restoreFocus() {
  const el = _focusTarget
  _focusTarget = null
  if (el && el.isConnected) el.focus()
}

// Tab 循环：把焦点限制在弹窗 box 内
export function trapTabFocus(box: HTMLElement, e: KeyboardEvent) {
  if (e.key !== 'Tab') return
  const items = box.querySelectorAll<HTMLElement>('button, input, select, textarea, [tabindex]:not([tabindex="-1"])')
  if (!items.length) return
  const first = items[0]
  const last = items[items.length - 1]
  const active = document.activeElement as HTMLElement | null
  if (e.shiftKey && (active === first || !box.contains(active))) {
    e.preventDefault(); last.focus()
  } else if (!e.shiftKey && (active === last || !box.contains(active))) {
    e.preventDefault(); first.focus()
  }
}
