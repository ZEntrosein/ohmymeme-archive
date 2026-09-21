/**
 * OhMyMeme 当前 QQ 会话桥接插件。
 *
 * NapCat 的 OneBot HTTP 接口知道如何发送到 peer，但标准接口没有暴露
 * QQNT 界面当前选中的 peer。拖拽路径用原生窗口句柄精确匹配 Electron
 * BrowserWindow，并只读取该窗口的聊天标题；应用再唯一匹配 OneBot 联系人。
 * 内部 MsgListener 仅保留给不传窗口句柄的兼容调用。
 *
 * 内部事件结构会随 QQNT 版本变化，因此解析器只接受明确的 chatType +
 * peer 标识，并且在无法唯一判断时保持“无目标”，绝不猜测发送对象。
 */

export const PLUGIN_ID = 'napcat-plugin-ohmymeme-current-peer'

import { inflateSync } from 'node:zlib'
import {
  existsSync,
  mkdirSync,
  renameSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs'
import { createHash, randomUUID } from 'node:crypto'
import { dirname, isAbsolute } from 'node:path'

const CHAT_TYPE_C2C = 1
const CHAT_TYPE_GROUP = 2
const MAX_SCAN_NODES = 5000
const MAX_ARCHIVE_IMAGE_BYTES = 64 * 1024 * 1024
const state = {
  active: false,
  current: null,
  source: '',
  updatedAt: 0,
  eventCount: 0,
  lastEvent: '',
  invalidatedAt: 0,
  mediaDiagnostics: [],
  mediaUpdatedAt: 0,
  listener: null,
  listenerHandlers: null,
  ctx: null,
}

function asText(value) {
  if (value === null || value === undefined) return ''
  if (
    typeof value === 'string'
    || typeof value === 'number'
    || typeof value === 'bigint'
  ) return String(value)
  return ''
}

function asObject(value) {
  return value !== null && typeof value === 'object' ? value : null
}

function own(value, keys) {
  const object = asObject(value)
  if (!object) return ''
  for (const key of keys) {
    const text = asText(object[key]).trim()
    if (text) return text
  }
  return ''
}

function boolFlag(value) {
  return value === true || value === 1 || value === '1' || value === 'true'
}

function numberFlag(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number : 0
}

function normalizeChatType(value, object) {
  const number = numberFlag(value)
  if (number === CHAT_TYPE_C2C || number === CHAT_TYPE_GROUP) return number
  const groupId = own(object, ['group_id', 'groupId', 'groupCode', 'groupUin'])
  if (groupId) return CHAT_TYPE_GROUP
  const userId = own(object, ['user_id', 'userId', 'uin', 'userUin', 'toUin', 'fromUin'])
  if (userId) return CHAT_TYPE_C2C
  return 0
}

function candidateFromObject(object, path, inheritedScore) {
  if (!object) return null
  const chatType = normalizeChatType(
    own(object, ['chatType', 'chat_type', 'sessionType', 'type']),
    object,
  )
  if (!chatType) return null

  const peer = own(object, [
    'peerUid',
    'peer_uid',
    'peerUin',
    'peer_uin',
    'peerId',
    'peer_id',
    ...(chatType === CHAT_TYPE_GROUP
      ? ['groupCode', 'groupUin', 'group_id', 'groupId']
      : ['user_id', 'userId', 'uin', 'userUin', 'toUin', 'fromUin']),
  ])
  if (!peer) return null

  let score = inheritedScore
  for (const key of ['current', 'isCurrent', 'selected', 'isSelected', 'active', 'isActive']) {
    if (boolFlag(object[key])) score += 100
  }
  if (boolFlag(object.visible) || boolFlag(object.isVisible)) score += 5

  return {
    chatType,
    peer,
    groupCode: own(object, ['groupCode', 'groupUin', 'group_id', 'groupId']),
    userUin: own(object, ['user_id', 'userId', 'uin', 'userUin', 'toUin', 'fromUin']),
    peerName: own(object, [
      'remark',
      'peerName',
      'peer_name',
      'groupName',
      'group_name',
      'nickname',
      'nickName',
      'name',
      'title',
    ]),
    score,
    path,
  }
}

/** 从 QQNT 的 JavaScript/Native 对象中提取明确的 peer 候选。 */
export function extractPeers(value, path = '$') {
  const candidates = []
  const visited = new Set()
  let scanned = 0

  function walk(node, nodePath, inheritedScore = 0) {
    if (scanned++ >= MAX_SCAN_NODES || node === null || node === undefined) return
    if (typeof node !== 'object') return
    if (visited.has(node)) return
    visited.add(node)

    const object = asObject(node)
    const candidate = candidateFromObject(object, nodePath, inheritedScore)
    if (candidate) candidates.push(candidate)

    if (Array.isArray(node)) {
      node.forEach((child, index) => walk(child, `${nodePath}[${index}]`, inheritedScore))
      return
    }
    for (const key of Object.keys(node)) {
      let child
      try {
        child = node[key]
      } catch {
        continue
      }
      const childScore = inheritedScore + (
        ['current', 'selected', 'active', 'currentTab', 'selectedTab'].includes(key)
          ? 25
          : 0
      )
      walk(child, `${nodePath}.${key}`, childScore)
    }
  }

  walk(value, path)
  const unique = new Map()
  for (const candidate of candidates) {
    const key = `${candidate.chatType}:${candidate.peer}`
    const previous = unique.get(key)
    if (!previous || candidate.score > previous.score) unique.set(key, candidate)
  }
  return [...unique.values()].sort((a, b) => b.score - a.score)
}

export function chooseCurrentPeer(value, allowUnmarked = true) {
  const candidates = extractPeers(value)
  if (!candidates.length) return null
  const topScore = candidates[0].score
  const top = candidates.filter((candidate) => candidate.score === topScore)
  // 显式 current/selected/active 标记优先；没有标记时只有单候选才安全。
  if (topScore >= 100) return top[0]
  return allowUnmarked && candidates.length === 1 ? candidates[0] : null
}

function setCurrent(candidate, source, now = Date.now()) {
  state.current = candidate
  state.source = source
  state.updatedAt = now
  state.invalidatedAt = 0
}

function invalidateCurrent(source, now = Date.now()) {
  state.current = null
  state.source = ''
  state.updatedAt = 0
  state.invalidatedAt = now
  state.lastEvent = source
}

/** 用户标签变化一定先让旧目标失效，避免新目标解析失败时沿用上一会话。 */
function recordTabEvent(payload, source = 'onUserTabStatusChanged') {
  if (!state.active) return
  const now = Date.now()
  state.eventCount += 1
  state.lastEvent = source
  invalidateCurrent(source, now)
  const candidate = chooseCurrentPeer(payload, true)
  if (!candidate) return
  setCurrent(candidate, source, now)
}

/**
 * 当前 QQNT 版本切换 AIO 时会给出只有一个 peer、但没有 active 标记的
 * onMsgBoxChanged。它是此版本能稳定取得当前会话的事件，因此允许唯一候选
 * 直接更新目标。会被后台消息触发的 onMsgInfoListUpdate/Add 不注册回调，
 * 从事件源上阻止它们覆盖当前会话。
 */
function recordMsgBoxEvent(payload, source = 'onMsgBoxChanged') {
  if (!state.active) return
  const now = Date.now()
  state.eventCount += 1
  state.lastEvent = source
  const candidate = chooseCurrentPeer(payload, true)
  if (!candidate) return
  setCurrent(candidate, source, now)
}

// 保留给测试与兼容调用；普通消息更新不得再调用这里更新当前会话。
function recordEvent(payload, source, allowUnmarked = false) {
  if (!state.active) return
  state.eventCount += 1
  state.lastEvent = source
  const candidate = chooseCurrentPeer(payload, allowUnmarked)
  if (candidate) setCurrent(candidate, source)
}

async function toOneBotTarget(candidate) {
  if (!candidate || !state.ctx) return null
  const { chatType } = candidate
  if (chatType === CHAT_TYPE_GROUP) {
    let groupId = candidate.groupCode || candidate.peer
    if (!/^\d+$/.test(String(groupId))) {
      const converter = state.ctx.core?.apis?.UserApi?.getUinByUidV2
      if (typeof converter !== 'function') return null
      groupId = await converter.call(state.ctx.core.apis.UserApi, String(groupId))
    }
    if (!/^\d+$/.test(String(groupId))) return null
    return {
      message_type: 'group',
      peer_id: String(groupId),
      label: candidate.peerName || '当前 QQ 群聊',
    }
  }

  let userId = candidate.userUin || candidate.peer
  if (!/^\d+$/.test(String(userId))) {
    const converter = state.ctx.core?.apis?.UserApi?.getUinByUidV2
    if (typeof converter !== 'function') return null
    userId = await converter.call(state.ctx.core.apis.UserApi, String(userId))
  }
  if (!/^\d+$/.test(String(userId))) return null
  return {
    message_type: 'private',
    peer_id: String(userId),
    label: candidate.peerName || '当前 QQ 私聊',
  }
}

function queryValue(request, key) {
  const query = request?.query
  let value = typeof query?.get === 'function' ? query.get(key) : query?.[key]
  if (Array.isArray(value)) value = value[0]
  if (value === undefined && typeof request?.url === 'string') {
    try {
      value = new URL(request.url, 'http://127.0.0.1').searchParams.get(key)
    } catch {
      value = undefined
    }
  }
  return asText(value).trim()
}

function requireDigits(value, label, maxLength = 32) {
  const text = asText(value).trim()
  if (!text || text.length > maxLength || !/^\d+$/.test(text)) {
    throw new Error(`${label}无效`)
  }
  return text
}

function requireResourceId(value) {
  const text = asText(value).trim()
  if (!text || text.length > 4096 || /[\x00-\x1f]/.test(text)) {
    throw new Error('聊天记录资源 ID 无效')
  }
  return text
}

function decodeCompressedText(source) {
  if (!source || typeof source.length !== 'number' || source.length < 2) return ''
  const buffer = Buffer.from(source)
  try {
    return buffer[0] === 1
      ? inflateSync(buffer.subarray(1)).toString('utf8')
      : buffer.toString('utf8')
  } catch {
    return ''
  }
}

function richMessageReference(element) {
  const payload = decodeCompressedText(element?.richMsg?.template1)
  if (!payload) return null
  const attribute = (name) => {
    const match = payload.match(new RegExp(`\\b${name}=["']([^"']*)["']`, 'i'))
    return match ? match[1] : ''
  }
  return {
    resid: attribute('m_resid') || attribute('resid'),
    file_name: attribute('m_fileName') || attribute('fileName'),
    service_id: asText(element?.richMsg?.serviceId),
  }
}

function readProtoVarint(buffer, start) {
  let value = 0n
  let shift = 0n
  let offset = start
  while (offset < buffer.length && shift <= 63n) {
    const byte = BigInt(buffer[offset])
    offset += 1
    value |= (byte & 0x7fn) << shift
    if ((byte & 0x80n) === 0n) return [value, offset]
    shift += 7n
  }
  throw new Error('protobuf varint 无效')
}

function protoFields(source) {
  const buffer = Buffer.from(source || [])
  if (buffer.length > 4 * 1024 * 1024) throw new Error('图片元数据过大')
  const result = new Map()
  let offset = 0
  while (offset < buffer.length) {
    const [tag, tagEnd] = readProtoVarint(buffer, offset)
    offset = tagEnd
    const field = Number(tag >> 3n)
    const wire = Number(tag & 7n)
    let value
    if (wire === 0) {
      ;[value, offset] = readProtoVarint(buffer, offset)
    } else if (wire === 1) {
      if (offset + 8 > buffer.length) throw new Error('protobuf fixed64 越界')
      value = buffer.subarray(offset, offset + 8)
      offset += 8
    } else if (wire === 2) {
      const [length, lengthEnd] = readProtoVarint(buffer, offset)
      offset = lengthEnd
      const size = Number(length)
      if (!Number.isSafeInteger(size) || size < 0 || offset + size > buffer.length) {
        throw new Error('protobuf bytes 越界')
      }
      value = buffer.subarray(offset, offset + size)
      offset += size
    } else if (wire === 5) {
      if (offset + 4 > buffer.length) throw new Error('protobuf fixed32 越界')
      value = buffer.subarray(offset, offset + 4)
      offset += 4
    } else {
      throw new Error(`不支持的 protobuf wire type：${wire}`)
    }
    const values = result.get(field) || []
    values.push({ wire, value })
    result.set(field, values)
  }
  return result
}

function protoBytes(fields, field) {
  const entry = fields.get(field)?.find((item) => item.wire === 2)
  return entry ? Buffer.from(entry.value) : Buffer.alloc(0)
}

function protoText(fields, field) {
  return protoBytes(fields, field).toString('utf8')
}

function protoNumber(fields, field) {
  const entry = fields.get(field)?.find((item) => item.wire === 0)
  return entry ? Number(entry.value) : 0
}

function decodeMediaMetadata(pbElem) {
  try {
    const msgInfo = protoFields(pbElem)
    const body = protoFields(protoBytes(msgInfo, 1))
    const indexFields = protoFields(protoBytes(body, 1))
    const infoFields = protoFields(protoBytes(indexFields, 1))
    const typeFields = protoFields(protoBytes(infoFields, 5))
    const fileUuid = protoText(indexFields, 2)
    const fileHash = protoText(infoFields, 2)
    if (!fileUuid && !fileHash) return null
    const pictureFields = protoFields(protoBytes(body, 2))
    const pictureExt = protoFields(protoBytes(pictureFields, 2))
    const domain = protoText(pictureFields, 3)
    const urlPath = protoText(pictureFields, 1)
    const parameter = protoText(pictureExt, 1)
    const embeddedUrl = (
      /^[A-Za-z0-9.-]+(?::\d+)?$/.test(domain)
      && urlPath.startsWith('/')
      && !/[\r\n]/.test(urlPath + parameter)
    ) ? `https://${domain}${urlPath}${parameter}` : ''
    return { index: {
      info: {
        fileSize: protoNumber(infoFields, 1),
        fileHash,
        fileSha1: protoText(infoFields, 3),
        fileName: protoText(infoFields, 4),
        type: {
          type: protoNumber(typeFields, 1),
          picFormat: protoNumber(typeFields, 2),
          videoFormat: protoNumber(typeFields, 3),
          voiceFormat: protoNumber(typeFields, 4),
        },
        width: protoNumber(infoFields, 6),
        height: protoNumber(infoFields, 7),
        time: protoNumber(infoFields, 8),
        original: protoNumber(infoFields, 9),
      },
      fileUuid,
      storeId: protoNumber(indexFields, 3),
      uploadTime: protoNumber(indexFields, 4),
      ttl: protoNumber(indexFields, 5),
      subType: protoNumber(indexFields, 6),
    }, embeddedUrl }
  } catch {
    return null
  }
}

function isOfficialQQMediaUrl(value) {
  try {
    const parsed = new URL(asText(value))
    if (parsed.protocol !== 'https:') return false
    const host = parsed.hostname.toLowerCase()
    return host === 'qq.com'
      || host.endsWith('.qq.com')
      || host === 'qq.com.cn'
      || host.endsWith('.qq.com.cn')
      || host === 'qpic.cn'
      || host.endsWith('.qpic.cn')
  } catch {
    return false
  }
}

function looksLikeImage(bytes, contentType = '') {
  if (!Buffer.isBuffer(bytes) || bytes.length < 4) return false
  if (asText(contentType).toLowerCase().startsWith('image/')) return true
  return bytes.subarray(0, 3).equals(Buffer.from([0xff, 0xd8, 0xff]))
    || bytes.subarray(0, 8).equals(Buffer.from('\x89PNG\r\n\x1a\n', 'binary'))
    || bytes.subarray(0, 6).toString('ascii') === 'GIF87a'
    || bytes.subarray(0, 6).toString('ascii') === 'GIF89a'
    || (bytes.subarray(0, 4).toString('ascii') === 'RIFF'
      && bytes.subarray(8, 12).toString('ascii') === 'WEBP')
    || bytes.subarray(0, 2).toString('ascii') === 'BM'
}

async function fetchOfficialQQImage(urls, options = {}) {
  const fetchImpl = options.fetchImpl || globalThis.fetch
  const diagnostic = options.diagnostic || null
  if (typeof fetchImpl !== 'function') return null
  const expectedHash = /^[a-f0-9]{32}$/i.test(asText(options.expectedHash))
    ? asText(options.expectedHash).toLowerCase()
    : ''
  for (const initial of Array.isArray(urls) ? urls : []) {
    let current = asText(initial)
    for (let redirects = 0; redirects <= 3; redirects += 1) {
      if (!isOfficialQQMediaUrl(current)) {
        if (diagnostic) diagnostic.official_result = 'blocked_host'
        break
      }
      const controller = new AbortController()
      const timeout = setTimeout(() => controller.abort(), 20000)
      try {
        const response = await fetchImpl(current, {
          redirect: 'manual',
          signal: controller.signal,
          headers: { Accept: 'image/*,*/*;q=0.8' },
        })
        if (diagnostic) {
          diagnostic.official_attempted = true
          diagnostic.official_statuses ||= []
          if (diagnostic.official_statuses.length < 8) {
            diagnostic.official_statuses.push(Number(response.status || 0))
          }
        }
        if ([301, 302, 303, 307, 308].includes(response.status)) {
          const location = response.headers?.get?.('location') || ''
          current = new URL(location, current).toString()
          continue
        }
        if (!response.ok || !response.body?.getReader) {
          if (diagnostic) diagnostic.official_result = 'http_or_body_error'
          break
        }
        const declaredSize = Number(response.headers?.get?.('content-length') || 0)
        if (declaredSize > MAX_ARCHIVE_IMAGE_BYTES) {
          if (diagnostic) diagnostic.official_result = 'too_large'
          break
        }
        const reader = response.body.getReader()
        const chunks = []
        let size = 0
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          const chunk = Buffer.from(value)
          size += chunk.length
          if (size > MAX_ARCHIVE_IMAGE_BYTES) {
            await reader.cancel()
            throw new Error('图片超过归档大小上限')
          }
          chunks.push(chunk)
        }
        const bytes = Buffer.concat(chunks, size)
        const contentType = response.headers?.get?.('content-type') || ''
        if (!looksLikeImage(bytes, contentType)) {
          if (diagnostic) diagnostic.official_result = 'not_image'
          break
        }
        if (expectedHash) {
          const actualHash = createHash('md5').update(bytes).digest('hex')
          if (actualHash !== expectedHash) {
            if (diagnostic) diagnostic.official_result = 'hash_mismatch'
            break
          }
        }
        if (diagnostic) {
          diagnostic.official_result = 'ok'
          diagnostic.downloaded_bytes = bytes.length
        }
        return bytes
      } catch (error) {
        if (diagnostic) {
          diagnostic.official_result = 'request_error'
          diagnostic.official_error = error?.name || 'Error'
        }
        break
      } finally {
        clearTimeout(timeout)
      }
    }
  }
  return null
}

function qqImageCachePath(element, needCreate) {
  try {
    const msgService = state.ctx?.core?.context?.session?.getMsgService?.()
    const candidate = msgService?.getRichMediaFilePathForGuild?.({
      md5HexStr: asText(element?.picElement?.md5HexStr),
      fileName: asText(element?.picElement?.fileName),
      elementType: Number(element?.elementType || 0),
      elementSubType: Number(element?.picElement?.picSubType || 0),
      thumbSize: 0,
      needCreate,
      downloadType: 1,
      file_uuid: asText(element?.picElement?.fileUuid),
    })
    return candidate && isAbsolute(String(candidate)) ? String(candidate) : ''
  } catch {
    return ''
  }
}

async function cacheOfficialQQImage(
  element,
  urls,
  metadata,
  knownPath = '',
  diagnostic = null,
) {
  let target = knownPath || qqImageCachePath(element, true)
  if (diagnostic) diagnostic.cache_target_available = Boolean(target)
  if (!target) {
    if (diagnostic) diagnostic.cache_write = 'no_target'
    return ''
  }
  try {
    if (existsSync(target) && statSync(target).size > 0) return target
  } catch {
  }
  const expectedHash = asText(element?.picElement?.md5HexStr)
    || asText(metadata?.index?.info?.fileHash)
  const bytes = await fetchOfficialQQImage(urls, { expectedHash, diagnostic })
  if (!bytes) {
    if (diagnostic) diagnostic.cache_write = 'no_download'
    return ''
  }
  const temporary = `${target}.omm-${randomUUID()}.tmp`
  try {
    mkdirSync(dirname(target), { recursive: true })
    writeFileSync(temporary, bytes, { flag: 'wx' })
    if (existsSync(target) && statSync(target).size > 0) {
      unlinkSync(temporary)
    } else {
      if (existsSync(target)) unlinkSync(target)
      renameSync(temporary, target)
    }
    const saved = existsSync(target) && statSync(target).size > 0 ? target : ''
    if (diagnostic) diagnostic.cache_write = saved ? 'ok' : 'missing_after_write'
    return saved
  } catch (error) {
    if (diagnostic) {
      diagnostic.cache_write = 'write_error'
      diagnostic.cache_error = error?.name || 'Error'
    }
    try {
      if (existsSync(temporary)) unlinkSync(temporary)
    } catch {
    }
    return ''
  }
}

async function downloadQQImageByMessage(
  raw,
  element,
  timeout = 20000,
  diagnostic = null,
) {
  const msgId = asText(raw?.msgId).trim()
  const elementId = asText(element?.elementId).trim()
  if (!msgId || msgId === '0' || msgId.startsWith('omm:') || !elementId) {
    if (diagnostic) diagnostic.native_result = 'missing_identity'
    return ''
  }
  const downloadMedia = state.ctx?.core?.apis?.FileApi?.downloadMedia
  if (typeof downloadMedia !== 'function') {
    if (diagnostic) diagnostic.native_result = 'api_unavailable'
    return ''
  }
  const sourcePath = asText(element?.picElement?.sourcePath)
  const targetPath = qqImageCachePath(element, true) || sourcePath
  const existing = existingImagePath(sourcePath) || existingImagePath(targetPath)
  if (existing) {
    if (diagnostic) diagnostic.native_result = 'existing'
    return existing
  }
  if (diagnostic) diagnostic.native_target_available = Boolean(targetPath)

  // qq-chat-exporter 的可靠路径同样把目标文件作为 sourcePath 传入，并开启
  // force；NapCat 会据此重新触发 QQNT 的 rich-media 下载，而不是继续信任
  // RawMessage 中可能已经失效的缓存路径。瞬时超时按短退避重试。
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      if (diagnostic) {
        diagnostic.native_attempted = true
        diagnostic.native_attempts = attempt
      }
      const downloaded = await downloadMedia.call(
        state.ctx.core.apis.FileApi,
        msgId,
        Number(raw?.chatType || 0),
        asText(raw?.peerUid),
        elementId,
        '',
        targetPath,
        timeout,
        true,
      )
      if (diagnostic) diagnostic.native_response_shape = diagnosticValueShape(downloaded)
      const path = existingImagePath(downloaded, targetPath)
        || existingImagePath(sourcePath)
      if (path) {
        if (diagnostic) diagnostic.native_result = 'ok'
        return path
      }
      if (diagnostic) diagnostic.native_result = 'missing_file'
      const servicePath = await downloadQQImageByMessageService(
        raw,
        element,
        targetPath,
        timeout,
        diagnostic,
      )
      if (servicePath) return servicePath
      const freshUrlPath = await downloadQQImageByFreshUrl(
        element,
        targetPath,
        diagnostic,
      )
      if (freshUrlPath) return freshUrlPath
      return ''
    } catch (error) {
      if (diagnostic) {
        diagnostic.native_result = 'error'
        diagnostic.native_error = error?.name || 'Error'
        diagnostic.native_error_detail = safeDiagnosticError(error)
      }
      const retriable = /timeout|connect|network|temporary|econnreset|econnrefused/i
        .test(asText(error?.message || error))
      if (attempt === 1) {
        const servicePath = await downloadQQImageByMessageService(
          raw,
          element,
          targetPath,
          timeout,
          diagnostic,
        )
        if (servicePath) return servicePath
        const freshUrlPath = await downloadQQImageByFreshUrl(
          element,
          targetPath,
          diagnostic,
        )
        if (freshUrlPath) return freshUrlPath
      }
      if (!retriable || attempt >= 3) return ''
      await new Promise((resolve) => setTimeout(resolve, 500 * (2 ** (attempt - 1))))
    }
  }
  return ''
}

/** 用 Native RawMessage 中的 picElement 生成当前有效的 rkey URL。 */
async function downloadQQImageByFreshUrl(element, targetPath, diagnostic = null) {
  const api = state.ctx?.core?.apis?.FileApi
  if (typeof api?.getImageUrl !== 'function') {
    if (diagnostic) diagnostic.fresh_url_result = 'api_unavailable'
    return ''
  }
  try {
    if (diagnostic) diagnostic.fresh_url_attempted = true
    const url = await api.getImageUrl(element?.picElement)
    if (diagnostic) diagnostic.fresh_url_available = Boolean(asText(url))
    if (!asText(url)) {
      if (diagnostic) diagnostic.fresh_url_result = 'empty'
      return ''
    }
    const path = await cacheOfficialQQImage(
      element,
      [asText(url)],
      null,
      targetPath,
      diagnostic,
    )
    if (diagnostic) diagnostic.fresh_url_result = path ? 'ok' : 'download_failed'
    return path
  } catch (error) {
    if (diagnostic) {
      diagnostic.fresh_url_result = 'error'
      diagnostic.fresh_url_error = error?.name || 'Error'
      diagnostic.fresh_url_error_detail = safeDiagnosticError(error)
    }
    return ''
  }
}

/**
 * FileApi.downloadMedia 直接等待 onRichMediaDownloadComplete；部分转发媒体
 * 不会为该调用补发事件，但 QQ 界面仍能通过 MsgService 主动接收/拖出文件。
 * 这里复用界面侧服务作为第二条原生下载通道。
 */
async function downloadQQImageByMessageService(
  raw,
  element,
  targetPath,
  timeout = 20000,
  diagnostic = null,
) {
  const service = state.ctx?.core?.context?.session?.getMsgService?.()
  if (!service) {
    if (diagnostic) diagnostic.message_service_result = 'unavailable'
    return ''
  }
  const request = {
    fileModelId: '0',
    downSourceType: 0,
    downloadSourceType: 0,
    triggerType: 1,
    msgId: asText(raw?.msgId),
    chatType: Number(raw?.chatType || 0),
    peerUid: asText(raw?.peerUid),
    elementId: asText(element?.elementId),
    thumbSize: 0,
    downloadType: 1,
    filePath: targetPath,
  }
  const methods = ['getRichMediaElement', 'downloadRichMedia']
  for (const name of methods) {
    const method = service?.[name]
    if (typeof method !== 'function') continue
    try {
      if (diagnostic) {
        diagnostic.message_service_attempted = true
        diagnostic.message_service_method = name
        diagnostic.message_service_arity = Number(method.length || 0)
      }
      const result = await Promise.race([
        Promise.resolve(method.call(service, request)),
        new Promise((_, reject) => setTimeout(
          () => reject(Object.assign(new Error('消息服务下载超时'), { name: 'TimeoutError' })),
          timeout,
        )),
      ])
      if (diagnostic) diagnostic.message_service_response_shape = diagnosticValueShape(result)
      const path = await waitForImagePath(result, targetPath, Math.min(timeout, 5000))
      if (path) {
        if (diagnostic) diagnostic.message_service_result = 'ok'
        return path
      }
      if (diagnostic) diagnostic.message_service_result = 'missing_file'
    } catch (error) {
      if (diagnostic) {
        diagnostic.message_service_result = 'error'
        diagnostic.message_service_error = error?.name || 'Error'
        diagnostic.message_service_error_detail = safeDiagnosticError(error)
      }
    }
  }
  return ''
}

function imageIdentityKeys(element, metadata = null) {
  const picture = element?.picElement || {}
  const values = [
    picture.fileUuid,
    picture.md5HexStr,
    picture.fileName,
    metadata?.index?.fileUuid,
    metadata?.index?.info?.fileHash,
    metadata?.index?.info?.fileName,
  ]
  return [...new Set(values
    .map((value) => asText(value).trim().toLowerCase())
    .filter(Boolean))]
}

function nativeForwardMediaPath(paths, element, metadata = null) {
  for (const key of imageIdentityKeys(element, metadata)) {
    const candidate = paths?.get(key)
    try {
      if (candidate && isAbsolute(candidate) && existsSync(candidate) && statSync(candidate).size > 0) {
        return candidate
      }
    } catch {
    }
  }
  return ''
}

function elementMediaKind(element) {
  if (element?.videoElement) return 'video'
  if (element?.pttElement) return 'record'
  if (element?.fileElement) return 'file'
  return ''
}

function elementMediaPayload(element, kind) {
  if (kind === 'video') return element?.videoElement || {}
  if (kind === 'record') return element?.pttElement || {}
  if (kind === 'file') return element?.fileElement || {}
  return {}
}

/** 为非图片媒体从 Native RawMessage 申请当前有效的路径或 URL。 */
async function resolveNativeMediaSource(raw, element, kind, diagnostic = null) {
  const payload = elementMediaPayload(element, kind)
  const existing = existingImagePath(
    payload.filePath || payload.sourcePath || payload.path,
  )
  if (existing) return { path: existing, url: '' }
  const api = state.ctx?.core?.apis?.FileApi
  const peer = {
    chatType: Number(raw?.chatType || 0),
    peerUid: asText(raw?.peerUid),
    guildId: '',
  }
  const attempts = []
  if (kind === 'video') {
    attempts.push(['native_video', () => api?.getVideoUrl?.(
      peer,
      asText(raw?.msgId),
      asText(element?.elementId),
    )])
    attempts.push(['packet_video', () => api?.getVideoUrlPacket?.(
      peer.peerUid,
      asText(payload.fileUuid),
      10000,
    )])
  } else if (kind === 'record') {
    attempts.push(['packet_record', () => api?.getPttUrl?.(
      peer.peerUid,
      asText(payload.fileUuid),
      10000,
    )])
  } else if (kind === 'file') {
    attempts.push(['packet_file', () => api?.getFileUrl?.(
      peer.chatType,
      peer.peerUid,
      asText(payload.fileUuid),
      asText(payload.file10MMd5 || payload.md5HexStr),
      10000,
    )])
  }
  for (const [name, invoke] of attempts) {
    if (typeof invoke !== 'function') continue
    try {
      if (diagnostic) {
        diagnostic.source_attempted = true
        diagnostic.source_method = name
      }
      const value = await Promise.race([
        Promise.resolve(invoke()),
        new Promise((_, reject) => setTimeout(
          () => reject(Object.assign(new Error('媒体地址获取超时'), { name: 'TimeoutError' })),
          12000,
        )),
      ])
      const url = asText(value).trim()
      if (diagnostic) diagnostic.source_response_shape = diagnosticValueShape(value)
      if (url && /^https?:\/\//i.test(url)) {
        if (diagnostic) diagnostic.source_result = 'url'
        return { path: '', url }
      }
    } catch (error) {
      if (diagnostic) {
        diagnostic.source_result = 'error'
        diagnostic.source_error = error?.name || 'Error'
        diagnostic.source_error_detail = safeDiagnosticError(error)
      }
    }
  }
  if (diagnostic && !diagnostic.source_result) diagnostic.source_result = 'missing'
  return { path: '', url: '' }
}

function existingImagePath(value, preferredPath = '', depth = 0) {
  if (depth > 3) return ''
  const candidates = []
  if (typeof value === 'string') {
    candidates.push(value)
  } else if (value && typeof value === 'object') {
    for (const key of ['filePath', 'path', 'localPath', 'savePath', 'toSavePath']) {
      if (typeof value[key] === 'string') candidates.push(value[key])
    }
    for (const key of ['data', 'result', 'fileInfo', 'commonFileInfo']) {
      if (value[key] && typeof value[key] === 'object') {
        const nested = existingImagePath(value[key], '', depth + 1)
        if (nested) return nested
      }
    }
  }
  if (preferredPath) candidates.push(preferredPath)
  for (const candidate of candidates) {
    try {
      if (candidate && isAbsolute(candidate) && existsSync(candidate) && statSync(candidate).size > 0) {
        return candidate
      }
    } catch {
    }
  }
  return ''
}

function safeDiagnosticError(error) {
  return asText(error?.message || error)
    .replace(/https?:\/\/\S+/gi, '<url>')
    .replace(/[A-Za-z]:\\[^\r\n]*/g, '<path>')
    .replace(/(?:[A-Za-z0-9+/_=-]{24,})/g, '<value>')
    .replace(/[\r\n]+/g, ' ')
    .trim()
    .slice(0, 300)
}

function diagnosticValueShape(value, depth = 0) {
  if (value === null) return 'null'
  if (value === undefined) return 'undefined'
  if (Array.isArray(value)) {
    return depth >= 1
      ? `array(${value.length})`
      : `array(${value.length})[${value.slice(0, 3).map((item) => diagnosticValueShape(item, depth + 1)).join(',')}]`
  }
  if (typeof value !== 'object') return typeof value
  const keys = Object.keys(value).slice(0, 16).sort()
  if (depth >= 1) return `object{${keys.join(',')}}`
  return `object{${keys.map((key) => `${key}:${diagnosticValueShape(value[key], depth + 1)}`).join(',')}}`
    .slice(0, 500)
}

function diagnosticObjectFields(value) {
  if (!value || typeof value !== 'object') return []
  return Object.keys(value).sort().slice(0, 64).map((key) => {
    const field = value[key]
    if (field === null) return `${key}:null`
    if (field === undefined) return `${key}:undefined`
    if (field instanceof Map) return `${key}:map(${field.size})`
    if (Array.isArray(field)) return `${key}:array(${field.length})`
    return `${key}:${typeof field}`
  })
}

function safeDiagnosticStack(error) {
  return asText(error?.stack)
    .split(/\r?\n/)
    .slice(0, 8)
    .map((line) => line
      .replace(/\(?file:\/\/\/[^)\s]+\)?/gi, '<frame>')
      .replace(/[A-Za-z]:\\[^)\r\n]+/g, '<frame>')
      .replace(/(?:[A-Za-z0-9+/_=-]{24,})/g, '<value>'))
    .join(' | ')
    .slice(0, 1000)
}

async function waitForImagePath(value, preferredPath, timeout = 20000) {
  const started = Date.now()
  while (true) {
    const candidate = existingImagePath(value, preferredPath)
    if (candidate) return candidate
    if (Date.now() - started >= timeout) return ''
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
}

async function downloadQQImageInVisit(
  raw,
  element,
  target,
  timeout = 20000,
  diagnostic = null,
) {
  const service = state.ctx?.core?.context?.session?.getRichMediaService?.()
  if (typeof service?.downloadRichMediaInVisit !== 'function') {
    if (diagnostic) diagnostic.visit_result = 'api_unavailable'
    return ''
  }
  const request = {
    downloadType: 1,
    thumbSize: 0,
    msgId: asText(raw?.msgId),
    msgRandom: asText(raw?.msgRandom || 0),
    msgSeq: asText(raw?.msgSeq || 0),
    msgTime: asText(raw?.msgTime || 0),
    chatType: Number(raw?.chatType || 0),
    senderUid: asText(raw?.senderUid),
    peerUid: asText(raw?.peerUid),
    guildId: '',
    ele: element,
    useHttps: true,
  }
  const variants = [
    ['flat', request],
    ['nested_get_req', {
      getReq: request,
      downSourceType: 0,
      triggerType: 1,
      fileModelId: '0',
    }],
  ]
  if (diagnostic) {
    diagnostic.visit_attempted = true
    diagnostic.visit_request_fields = diagnosticObjectFields(request)
    diagnostic.visit_element_fields = diagnosticObjectFields(element)
    diagnostic.visit_picture_fields = diagnosticObjectFields(element?.picElement)
    diagnostic.visit_attempts = []
  }
  let lastError = null
  for (const [variant, payload] of variants) {
    try {
      if (diagnostic) diagnostic.visit_stage = `invoke_${variant}`
      const result = await Promise.race([
        Promise.resolve(service.downloadRichMediaInVisit(payload)),
        new Promise((_, reject) => setTimeout(
          () => reject(Object.assign(new Error('visit 下载超时'), { name: 'TimeoutError' })),
          Math.min(timeout, 10000),
        )),
      ])
      if (diagnostic) diagnostic.visit_stage = `wait_${variant}`
      const path = await waitForImagePath(result, target, Math.min(timeout, 5000))
      if (diagnostic) {
        diagnostic.visit_response_shape = diagnosticValueShape(result)
        diagnostic.visit_attempts.push({
          variant,
          result: path ? 'ok' : 'missing_file',
        })
      }
      if (path) {
        if (diagnostic) {
          diagnostic.visit_result = 'ok'
          diagnostic.visit_variant = variant
          diagnostic.visit_stage = 'complete'
        }
        return path
      }
    } catch (error) {
      lastError = error
      if (diagnostic) {
        diagnostic.visit_attempts.push({
          variant,
          result: 'error',
          error: error?.name || 'Error',
          detail: safeDiagnosticError(error),
        })
      }
    }
  }
  if (diagnostic) {
    diagnostic.visit_result = lastError ? 'error' : 'missing_file'
    diagnostic.visit_stage = 'complete'
    if (lastError) {
      diagnostic.visit_error = lastError?.name || 'Error'
      diagnostic.visit_error_detail = safeDiagnosticError(lastError)
      diagnostic.visit_error_stack = safeDiagnosticStack(lastError)
    }
  }
  return ''
}

/**
 * 长消息资源里的图片通常没有真实 msgId/elementId，但 MsgInfo 仍携带
 * fileUuid。QQ 界面查看、拖出这类图片时会交给 RichMediaService；这里也
 * 使用同一内核服务把原图落到 QQNT 媒体缓存，再由 OhMyMeme 永久归档。
 */
async function downloadQQImageByUuid(
  raw,
  element,
  metadata,
  target,
  timeout = 20000,
  diagnostic = null,
) {
  const fileUuid = asText(metadata?.index?.fileUuid)
    || asText(element?.picElement?.fileUuid)
  if (!fileUuid) {
    if (diagnostic) diagnostic.uuid_result = 'missing_uuid'
    return ''
  }
  if (!target) {
    if (diagnostic) diagnostic.uuid_result = 'missing_target'
    return ''
  }
  const service = state.ctx?.core?.context?.session?.getRichMediaService?.()
  if (typeof service?.downloadFileForFileUuid !== 'function') {
    if (diagnostic) diagnostic.uuid_result = 'api_unavailable'
    return ''
  }
  const info = metadata?.index?.info || {}
  const hash = asText(info.fileHash) || asText(element?.picElement?.md5HexStr)
  const fileName = asText(info.fileName)
    || asText(element?.picElement?.fileName)
    || hash
  const fileSize = Number(info.fileSize || element?.picElement?.fileSize || 0)
  const peer = {
    chatType: Number(raw?.chatType || 0),
    peerUid: asText(raw?.peerUid),
    guildId: '',
  }
  const fileParams = [{
    fileId: fileUuid,
    fileName,
    fileSize: String(Number.isSafeInteger(fileSize) && fileSize >= 0 ? fileSize : 0),
    fileModelId: '0',
  }]
  try {
    if (diagnostic) {
      diagnostic.uuid_attempted = true
      diagnostic.uuid_api_arity = Number(service.downloadFileForFileUuid.length || 0)
    }
    const result = await Promise.race([
      Promise.resolve(service.downloadFileForFileUuid(peer, fileUuid, fileParams)),
      new Promise((_, reject) => setTimeout(
        () => reject(Object.assign(new Error('fileUuid 下载超时'), { name: 'TimeoutError' })),
        timeout,
      )),
    ])
    if (diagnostic) diagnostic.uuid_response_shape = diagnosticValueShape(result)
    const path = await waitForImagePath(result, target, Math.min(timeout, 5000))
    if (diagnostic) diagnostic.uuid_result = path ? 'ok' : 'missing_file'
    return path
  } catch (error) {
    if (diagnostic) {
      diagnostic.uuid_result = 'error'
      diagnostic.uuid_error = error?.name || 'Error'
      diagnostic.uuid_error_detail = safeDiagnosticError(error)
    }
    return ''
  }
}

async function packetMessageToRaw(
  message,
  syntheticId,
  mediaDiagnostics = [],
  nativeMedia = null,
) {
  const elems = Array.isArray(message?.body?.richText?.elems)
    ? message.body.richText.elems
    : []
  const converter = state.ctx?.core?.apis?.PacketApi?.pkt?.msgConverter
  if (typeof converter?.packetMsgToRaw !== 'function') {
    throw new Error('当前 NapCat 未提供聊天记录消息转换器')
  }
  const rawChains = converter.packetMsgToRaw(elems)
  const pairs = (Array.isArray(rawChains) ? rawChains : [])
    .filter((item) => Array.isArray(item) && item[0])
  const elements = pairs.map((item) => item[0])
  // PacketMsgConverter 会把 NTV2 图片哈希放进 fileName，却没有回填
  // FileApi.getImageUrl 所读取的 md5HexStr，导致 OneBot 转换结果 url 为空。
  for (const element of elements) {
    if (element?.picElement && !element.picElement.md5HexStr) {
      element.picElement.md5HexStr = asText(element.picElement.fileName)
    }
  }
  const isGroup = message?.responseHead?.grp !== undefined
  const peerUin = isGroup
    ? asText(message?.responseHead?.grp?.groupUin || 0)
    : asText(message?.responseHead?.fromUin || 0)
  const peerUid = isGroup
    ? peerUin
    : asText(message?.responseHead?.fromUid)
  const senderName = isGroup
    ? asText(message?.responseHead?.grp?.memberName)
    : asText(message?.responseHead?.forward?.friendName)
  const nativeMessageId = asText(message?.contentHead?.newId).trim()
  const messageId = nativeMessageId && nativeMessageId !== '0'
    ? nativeMessageId
    : syntheticId
  const operation = state.ctx?.core?.apis?.PacketApi?.pkt?.operation
  const imageUrls = []
  const imagePaths = []
  const mediaSources = { video: [], record: [], file: [] }
  for (const [element, packetElement] of pairs) {
    if (!element?.picElement) {
      const kind = elementMediaKind(element)
      if (kind) {
        const entries = nativeMedia?.entries?.[kind] || []
        const cursor = Number(nativeMedia?.cursors?.[kind] || 0)
        mediaSources[kind].push(entries[cursor] || { path: '', url: '' })
        if (nativeMedia?.cursors) nativeMedia.cursors[kind] = cursor + 1
      }
      continue
    }
    const metadata = packetElement?.commonElem?.pbElem
      ? decodeMediaMetadata(packetElement.commonElem.pbElem)
      : null
    if (metadata) {
      // NapCat 4.18 的 PacketMsgPicElement 丢掉了 index.fileUuid，导致
      // QQNT 只能按哈希猜缓存路径；界面拖拽则会保留这个 UUID。
      element.picElement.fileUuid ||= asText(metadata.index?.fileUuid)
      element.picElement.md5HexStr ||= asText(metadata.index?.info?.fileHash)
      element.picElement.fileName ||= asText(metadata.index?.info?.fileHash)
      element.picElement.fileSize ||= Number(metadata.index?.info?.fileSize || 0)
      element.picElement.picWidth ||= Number(metadata.index?.info?.width || 0)
      element.picElement.picHeight ||= Number(metadata.index?.info?.height || 0)
      element.picElement.picSubType ??= Number(metadata.index?.subType || 0)
      element.picElement.fileSubId ||= ''
      element.picElement.sourcePath ||= ''
      element.picElement.summary ||= '[图片]'
      element.picElement.thumbFileSize ||= 0
      element.picElement.thumbPath ||= new Map()
      element.extBufForUI ||= ''
    }
    const diagnostic = {
      image_index: imagePaths.length + 1,
      native_message_id: messageId !== syntheticId,
      element_id: Boolean(asText(element.elementId)),
      packet_metadata: Boolean(metadata),
      file_uuid: Boolean(asText(element.picElement.fileUuid)),
      cache_hit: false,
      native_attempted: false,
      visit_attempted: false,
      uuid_attempted: false,
      official_attempted: false,
    }
    let cachedPath = ''
    const candidate = qqImageCachePath(element, false)
    try {
      if (candidate && existsSync(candidate) && statSync(candidate).size > 0) {
        cachedPath = candidate
        diagnostic.cache_hit = true
      }
    } catch {
    }
    if (!cachedPath) {
      cachedPath = nativeForwardMediaPath(nativeMedia?.paths, element, metadata)
      if (cachedPath) {
        diagnostic.native_attempted = true
        diagnostic.native_result = 'ok'
        diagnostic.native_tree_match = true
      }
    }
    if (!cachedPath) {
      cachedPath = await downloadQQImageByMessage({
        msgId: messageId,
        chatType: isGroup ? CHAT_TYPE_GROUP : CHAT_TYPE_C2C,
        peerUid,
      }, element, 20000, diagnostic)
    }
    if (!cachedPath && metadata) {
      cachedPath = await downloadQQImageInVisit({
        msgId: nativeMessageId,
        msgRandom: asText(
          message?.contentHead?.ranDom
          || message?.contentHead?.random
          || message?.contentHead?.msgRandom
          || 0,
        ),
        msgSeq: asText(message?.contentHead?.sequence || 0),
        msgTime: asText(message?.contentHead?.timeStamp || 0),
        chatType: isGroup ? CHAT_TYPE_GROUP : CHAT_TYPE_C2C,
        senderUid: asText(message?.responseHead?.fromUid),
        peerUid,
      }, element, candidate || qqImageCachePath(element, true), 20000, diagnostic)
    }
    if (!cachedPath && metadata) {
      cachedPath = await downloadQQImageByUuid(
        {
          chatType: isGroup ? CHAT_TYPE_GROUP : CHAT_TYPE_C2C,
          peerUid,
        },
        element,
        metadata,
        candidate || qqImageCachePath(element, true),
        20000,
        diagnostic,
      )
    }
    if (!metadata) {
      if (cachedPath) element.picElement.sourcePath = cachedPath
      imagePaths.push(cachedPath)
      imageUrls.push([])
      diagnostic.result = cachedPath ? 'local' : 'temporary_reference'
      mediaDiagnostics.push(diagnostic)
      continue
    }
    const candidates = metadata.embeddedUrl ? [metadata.embeddedUrl] : []
    try {
      const url = isGroup
        ? await operation.GetGroupImageUrl(Number(peerUin), metadata.index, 20000)
        : await operation.GetImageUrl(
          asText(state.ctx?.core?.apis?.PacketApi?.pkt?.napcore?.basicInfo?.uid),
          metadata.index,
        )
      if (url) candidates.push(asText(url))
    } catch {
    }
    if (!cachedPath && candidates.length) {
      cachedPath = await cacheOfficialQQImage(
        element,
        candidates,
        metadata,
        candidate,
        diagnostic,
      )
    }
    if (cachedPath) element.picElement.sourcePath = cachedPath
    imagePaths.push(cachedPath)
    imageUrls.push(candidates)
    diagnostic.url_candidates = candidates.length
    diagnostic.result = cachedPath ? 'local' : 'temporary_reference'
    mediaDiagnostics.push(diagnostic)
  }
  return { raw: {
    chatType: isGroup ? CHAT_TYPE_GROUP : CHAT_TYPE_C2C,
    elements,
    guildId: '',
    isOnlineMsg: false,
    id: messageId,
    msgId: messageId,
    msgRandom: '0',
    msgSeq: asText(message?.contentHead?.sequence || 0),
    msgTime: asText(message?.contentHead?.timeStamp || 0),
    msgType: 2,
    parentMsgIdList: [],
    parentMsgPeer: { chatType: isGroup ? CHAT_TYPE_GROUP : CHAT_TYPE_C2C, peerUid },
    peerName: '',
    peerUid,
    peerUin,
    recallTime: '0',
    records: [],
    sendNickName: senderName,
    sendRemarkName: senderName,
    senderUid: asText(message?.responseHead?.fromUid),
    senderUin: asText(message?.responseHead?.fromUin || 0),
    sourceType: 0,
    subMsgType: 1,
  }, imageUrls, imagePaths, mediaSources }
}

/**
 * 绕过 NapCat 4.18 只转换主 MultiMsg action 的限制：直接读取同一长消息
 * 资源中的附属 action，并把 richMsg.m_fileName 重新接成内联 forward 树。
 */
export async function recoverForwardResource(resourceOrParams) {
  if (!state.active || !state.ctx) throw new Error('插件尚未启动')
  const params = resourceOrParams && typeof resourceOrParams === 'object'
    ? resourceOrParams
    : { res_id: resourceOrParams }
  const resId = requireResourceId(params.res_id)
  const operation = state.ctx?.core?.apis?.PacketApi?.pkt?.operation
  const parser = state.ctx?.oneBot?.apis?.MsgApi?.parseMessage
  if (typeof operation?.FetchForwardMsgRaw !== 'function') {
    throw new Error('当前 NapCat 未提供原始聊天记录资源读取接口')
  }
  if (typeof parser !== 'function') {
    throw new Error('当前 NapCat 未提供 OneBot 消息转换器')
  }
  let nativeMedia = {
    attempted: false,
    paths: new Map(),
    diagnostics: [],
    entries: { video: [], record: [], file: [] },
    cursors: { video: 0, record: 0, file: 0 },
    messages: 0,
    images: 0,
    media: 0,
    resolved: 0,
    localized: 0,
  }
  try {
    nativeMedia = await downloadNativeForwardMedia(params)
  } catch {
    nativeMedia = {
      attempted: true,
      paths: new Map(),
      diagnostics: [],
      entries: { video: [], record: [], file: [] },
      cursors: { video: 0, record: 0, file: 0 },
      messages: 0,
      images: 0,
      media: 0,
      resolved: 0,
      localized: 0,
    }
  }
  const rawActions = await operation.FetchForwardMsgRaw(resId)
  const actions = new Map()
  for (const action of Array.isArray(rawActions) ? rawActions : []) {
    const command = asText(action?.actionCommand)
    const messages = Array.isArray(action?.actionData?.msgBody)
      ? action.actionData.msgBody
      : []
    if (command && !actions.has(command)) actions.set(command, messages)
  }
  if (!actions.has('MultiMsg')) throw new Error('聊天记录资源缺少主 MultiMsg action')
  if (actions.size > 100 || [...actions.values()].reduce((n, rows) => n + rows.length, 0) > 20000) {
    throw new Error('聊天记录资源超过安全上限')
  }

  const cache = new Map()
  const active = new Set()
  const mediaDiagnostics = []
  const parseAction = async (command, depth = 1) => {
    if (depth > 12) throw new Error('聊天记录嵌套层级超过安全上限')
    if (cache.has(command)) return cache.get(command)
    if (active.has(command)) throw new Error('聊天记录 action 存在循环引用')
    const packetMessages = actions.get(command)
    if (!packetMessages) return []
    active.add(command)
    const result = []
    try {
      for (let index = 0; index < packetMessages.length; index += 1) {
        const packetMessage = packetMessages[index]
        const converted = await packetMessageToRaw(
          packetMessage,
          `omm:${command}:${index}`,
          mediaDiagnostics,
          nativeMedia,
        )
        const raw = converted.raw
        const parsed = await parser.call(
          state.ctx.oneBot.apis.MsgApi,
          raw,
          'array',
          false,
        )
        const node = parsed && typeof parsed === 'object'
          ? parsed
          : {
            user_id: Number(raw.senderUin || 0),
            time: Number(raw.msgTime || 0),
            message_id: raw.id,
            real_seq: raw.msgSeq,
            message_type: raw.chatType === CHAT_TYPE_GROUP ? 'group' : 'private',
            sender: {
              user_id: Number(raw.senderUin || 0),
              nickname: raw.sendNickName,
              card: raw.sendRemarkName,
            },
            raw_message: '',
            message: [],
            message_format: 'array',
          }
        if (!Array.isArray(node.message)) node.message = []
        let imageIndex = 0
        const mediaIndexes = { video: 0, record: 0, file: 0 }
        for (const segment of node.message) {
          if (!segment?.data) continue
          if (segment.type === 'image') {
            const directUrls = converted.imageUrls[imageIndex]
            const cachedPath = converted.imagePaths[imageIndex]
            imageIndex += 1
            if (Array.isArray(directUrls) && directUrls.length) {
              segment.data.omm_fallback_urls = directUrls
            }
            if (cachedPath) segment.data.path = cachedPath
            continue
          }
          if (!(segment.type in mediaIndexes)) continue
          const source = converted.mediaSources[segment.type][mediaIndexes[segment.type]]
          mediaIndexes[segment.type] += 1
          if (source?.path) segment.data.path = source.path
          if (source?.url) {
            const previous = asText(segment.data.url)
            segment.data.url = source.url
            if (previous && previous !== source.url) {
              segment.data.omm_fallback_urls = [previous]
            }
          }
        }
        const elems = Array.isArray(packetMessage?.body?.richText?.elems)
          ? packetMessage.body.richText.elems
          : []
        for (const reference of elems.map(richMessageReference).filter(Boolean)) {
          const childCommand = reference.file_name
          if (childCommand && actions.has(childCommand)) {
            const content = await parseAction(childCommand, depth + 1)
            node.message.push({
              type: 'forward',
              data: {
                id: `omm-action:${childCommand}`,
                prompt: '聊天记录',
                content,
              },
            })
          } else if (reference.resid) {
            node.message.push({
              type: 'forward',
              data: { id: reference.resid, prompt: '聊天记录' },
            })
          }
        }
        node.raw_message = node.message.map((segment) => {
          if (segment?.type === 'text') return asText(segment?.data?.text)
          if (segment?.type === 'forward') return '[聊天记录]'
          return segment?.type ? `[${segment.type}]` : ''
        }).join('')
        result.push(node)
      }
    } finally {
      active.delete(command)
    }
    cache.set(command, result)
    return result
  }

  const messages = await parseAction('MultiMsg')
  state.mediaDiagnostics = mediaDiagnostics.map((item) => ({ ...item }))
  state.mediaUpdatedAt = Date.now()
  return {
    ok: true,
    messages,
    action_count: actions.size,
    media_diagnostics: mediaDiagnostics,
    native_tree_attempted: nativeMedia.attempted,
    native_tree_message_count: nativeMedia.messages,
    native_tree_image_count: nativeMedia.images,
    native_tree_localized_count: nativeMedia.localized,
    native_tree_media_count: nativeMedia.media,
    native_tree_resolved_count: nativeMedia.resolved,
    native_tree_diagnostics: nativeMedia.diagnostics,
  }
}

async function oneBotPeer(messageType, peerId) {
  const type = asText(messageType).trim()
  const id = requireDigits(peerId, type === 'group' ? '群号' : 'QQ 号')
  if (type === 'group') {
    return { chatType: CHAT_TYPE_GROUP, peerUid: id, guildId: '' }
  }
  if (type !== 'private') throw new Error('会话类型无效')
  const converter = state.ctx?.core?.apis?.UserApi?.getUidByUinV2
  if (typeof converter !== 'function') throw new Error('当前 NapCat 无法转换私聊 QQ 号')
  const uid = await converter.call(state.ctx.core.apis.UserApi, id)
  if (!uid) throw new Error(`无法找到私聊对象 ${id}`)
  return { chatType: CHAT_TYPE_C2C, peerUid: String(uid), guildId: '' }
}

function messageList(result) {
  return Array.isArray(result?.msgList) ? result.msgList : []
}

function multiMessageList(result) {
  if (Array.isArray(result?.msgList)) return result.msgList
  if (Array.isArray(result?.messages)) return result.messages
  if (Array.isArray(result?.data?.messages)) return result.data.messages
  return []
}

async function findMessageBySeq(peer, realSeq) {
  const api = state.ctx?.core?.apis?.MsgApi
  if (!api || typeof api.getMsgsBySeqAndCount !== 'function') {
    throw new Error('当前 NapCat 未提供按真实序号读取消息的接口')
  }
  const results = []
  results.push(await api.getMsgsBySeqAndCount(peer, realSeq, 1, true, true))
  if (!messageList(results[0]).some((message) => String(message?.msgSeq) === realSeq)) {
    if (typeof api.queryFirstMsgBySeq === 'function') {
      results.push(await api.queryFirstMsgBySeq(peer, realSeq))
    }
    if (
      !results.some((result) => messageList(result)
        .some((message) => String(message?.msgSeq) === realSeq))
      && typeof api.queryMsgsWithFilterExWithSeq === 'function'
    ) {
      results.push(await api.queryMsgsWithFilterExWithSeq(peer, realSeq))
    }
  }
  for (const result of results) {
    const found = messageList(result)
      .find((message) => String(message?.msgSeq) === realSeq)
    if (found) return found
  }
  return null
}

/**
 * QCE 的资源下载并不从转发协议包猜 URL，而是重新取得带真实 msgId 与
 * elementId 的 RawMessage，再交给 FileApi.downloadMedia。这里复用同一路径，
 * 并按图片 UUID/MD5 建立临时映射，供后面的多 action 协议树回填本地文件。
 */
async function downloadNativeForwardMedia(params) {
  const sourceType = asText(params?.source_type).trim()
  const sourcePeerId = asText(params?.source_peer_id).trim()
  const sourceRealSeq = asText(params?.source_real_seq).trim()
  if (!sourceType || !sourcePeerId || !sourceRealSeq) {
    return {
      attempted: false,
      paths: new Map(),
      diagnostics: [],
      messages: 0,
      images: 0,
      localized: 0,
    }
  }

  const sourcePeer = await oneBotPeer(sourceType, sourcePeerId)
  const outer = await findMessageBySeq(
    sourcePeer,
    requireDigits(sourceRealSeq, '真实消息序号', 64),
  )
  if (!outer?.msgId || !isForwardCard(outer)) {
    return {
      attempted: true,
      paths: new Map(),
      diagnostics: [],
      messages: 0,
      images: 0,
      localized: 0,
    }
  }

  const sourceTime = asText(params?.source_time).trim()
  if (sourceTime && asText(outer.msgTime).trim() && asText(outer.msgTime).trim() !== sourceTime) {
    return {
      attempted: true,
      paths: new Map(),
      diagnostics: [],
      messages: 0,
      images: 0,
      localized: 0,
    }
  }
  const sourceSender = asText(params?.source_sender_id).trim()
  if (
    sourceSender
    && asText(outer.senderUin).trim()
    && asText(outer.senderUin).trim() !== sourceSender
  ) {
    return {
      attempted: true,
      paths: new Map(),
      diagnostics: [],
      messages: 0,
      images: 0,
      localized: 0,
    }
  }

  const api = state.ctx?.core?.apis?.MsgApi
  if (typeof api?.getMultiMsg !== 'function') {
    return {
      attempted: true,
      paths: new Map(),
      diagnostics: [],
      messages: 0,
      images: 0,
      localized: 0,
    }
  }

  const paths = new Map()
  const diagnostics = []
  const entries = { video: [], record: [], file: [] }
  const seenMessages = new Set()
  const fetchedForwards = new Set()
  let messages = 0
  let images = 0
  let media = 0
  let resolved = 0
  let localized = 0

  const walk = async (rows, inheritedPeer, depth = 1) => {
    if (depth > 12 || messages >= 20000) return
    for (const raw of Array.isArray(rows) ? rows : []) {
      if (!raw || typeof raw !== 'object') continue
      const rawId = asText(raw.msgId).trim()
      const identity = rawId && rawId !== '0'
        ? rawId
        : `${depth}:${messages}:${asText(raw.msgSeq)}`
      if (seenMessages.has(identity)) continue
      seenMessages.add(identity)
      messages += 1

      if (!raw.chatType) raw.chatType = inheritedPeer.chatType
      if (!raw.peerUid) raw.peerUid = inheritedPeer.peerUid
      const rawPeer = {
        chatType: Number(raw.chatType || inheritedPeer.chatType),
        peerUid: asText(raw.peerUid || inheritedPeer.peerUid),
        guildId: '',
      }
      const elements = Array.isArray(raw.elements) ? raw.elements : []
      for (const element of elements) {
        if (!element?.picElement) {
          const kind = elementMediaKind(element)
          if (!kind) continue
          media += 1
          const diagnostic = {
            media_index: media,
            media_kind: kind,
            native_message_id: Boolean(rawId && rawId !== '0'),
            element_id: Boolean(asText(element?.elementId).trim()),
          }
          const source = await resolveNativeMediaSource(raw, element, kind, diagnostic)
          entries[kind].push(source)
          diagnostics.push(diagnostic)
          if (source.path || source.url) resolved += 1
          continue
        }
        images += 1
        media += 1
        const diagnostic = {
          image_index: images,
          native_message_id: Boolean(rawId && rawId !== '0'),
          element_id: Boolean(asText(element?.elementId).trim()),
          chat_type: Number(raw?.chatType || 0),
          peer_uid: Boolean(asText(raw?.peerUid).trim()),
          source_path: Boolean(asText(element?.picElement?.sourcePath).trim()),
        }
        const path = await downloadQQImageByMessage(raw, element, 20000, diagnostic)
        diagnostics.push(diagnostic)
        if (!path) continue
        localized += 1
        for (const key of imageIdentityKeys(element)) paths.set(key, path)
      }

      const inline = Array.isArray(raw.records) ? raw.records : []
      if (inline.length) {
        await walk(inline, rawPeer, depth + 1)
        continue
      }
      if (!isForwardCard(raw) || !rawId || fetchedForwards.has(rawId)) continue
      fetchedForwards.add(rawId)
      try {
        const nested = await api.getMultiMsg(rawPeer, rawId, rawId)
        await walk(multiMessageList(nested), rawPeer, depth + 1)
      } catch {
      }
    }
  }

  const outerId = asText(outer.msgId)
  const root = await api.getMultiMsg(sourcePeer, outerId, outerId)
  await walk(multiMessageList(root), sourcePeer)
  return {
    attempted: true,
    paths,
    diagnostics,
    entries,
    cursors: { video: 0, record: 0, file: 0 },
    messages,
    images,
    media,
    resolved,
    localized,
  }
}

function isForwardCard(message) {
  const elements = Array.isArray(message?.elements) ? message.elements : []
  return elements.some((element) => {
    if (element?.multiForwardMsgElement?.resId) return true
    const bytes = element?.arkElement?.bytesData
    if (typeof bytes !== 'string' || !bytes) return false
    try {
      return JSON.parse(bytes)?.app === 'com.tencent.multimsg'
    } catch {
      return false
    }
  })
}

/**
 * 用持久化的会话位置和真实 msgSeq 恢复 QQ 原始 msgId，然后直接原生转发。
 * 这条路径不依赖 NapCat 进程内的 OneBot 短 ID 映射。
 */
export async function forwardStoredMessage(params) {
  if (!state.active || !state.ctx) throw new Error('插件尚未启动')
  const sourceType = asText(params?.source_type).trim()
  const destinationType = asText(params?.destination_type).trim()
  const realSeq = requireDigits(params?.source_real_seq, '真实消息序号', 64)
  const sourcePeer = await oneBotPeer(sourceType, params?.source_peer_id)
  const destinationPeer = await oneBotPeer(
    destinationType,
    params?.destination_peer_id,
  )
  const message = await findMessageBySeq(sourcePeer, realSeq)
  if (!message) {
    throw new Error('QQ 历史记录中已找不到这条外层聊天记录')
  }

  const sourceTime = asText(params?.source_time).trim()
  if (sourceTime && asText(message.msgTime).trim() && String(message.msgTime) !== sourceTime) {
    throw new Error('按序号找到的消息时间与归档不一致，已拒绝转发')
  }
  const sourceSender = asText(params?.source_sender_id).trim()
  if (
    sourceSender
    && asText(message.senderUin).trim()
    && String(message.senderUin) !== sourceSender
  ) {
    throw new Error('按序号找到的消息发送者与归档不一致，已拒绝转发')
  }
  if (!message.msgId) throw new Error('找到的消息缺少 QQ 原始消息 ID')
  if (!isForwardCard(message)) throw new Error('按序号找到的消息不是合并转发卡片')

  const result = await state.ctx.core.apis.MsgApi.forwardMsg(
    sourcePeer,
    destinationPeer,
    [String(message.msgId)],
  )
  if (!result || Number(result.result) !== 0) {
    throw new Error(`原生转发失败：${result?.errMsg || '未知错误'}`)
  }
  return {
    ok: true,
    recovered: true,
    source_real_seq: realSeq,
  }
}

/**
 * 按持久来源位置重新读取一条原始消息，并使用 NapCat 自己的 OneBot
 * 转换器（含 parseMultMsg）恢复消息段。用于补全 get_forward_msg 中被
 * 压成 message: []、但仍保留来源会话与 real_seq 的嵌套节点。
 */
export async function recoverStoredMessage(params) {
  if (!state.active || !state.ctx) throw new Error('插件尚未启动')
  const sourceType = asText(params?.source_type).trim()
  const realSeq = requireDigits(params?.source_real_seq, '真实消息序号', 64)
  const sourcePeer = await oneBotPeer(sourceType, params?.source_peer_id)
  const message = await findMessageBySeq(sourcePeer, realSeq)
  if (!message) throw new Error('QQ 历史记录中已找不到该嵌套节点的原消息')

  const sourceTime = asText(params?.source_time).trim()
  if (sourceTime && asText(message.msgTime).trim() && String(message.msgTime) !== sourceTime) {
    throw new Error('按序号找到的嵌套消息时间不一致，已拒绝恢复')
  }
  const sourceSender = asText(params?.source_sender_id).trim()
  if (
    sourceSender
    && asText(message.senderUin).trim()
    && String(message.senderUin) !== sourceSender
  ) {
    throw new Error('按序号找到的嵌套消息发送者不一致，已拒绝恢复')
  }
  const parser = state.ctx.oneBot?.apis?.MsgApi?.parseMessage
  if (typeof parser !== 'function') {
    throw new Error('当前 NapCat 未提供 OneBot 原始消息转换器')
  }
  if (message.id === undefined || message.id === null) message.id = message.msgId
  const parsed = await parser.call(
    state.ctx.oneBot.apis.MsgApi,
    message,
    'array',
    true,
  )
  if (!parsed || !Array.isArray(parsed.message) || !parsed.message.length) {
    throw new Error('NapCat 重新解析后仍未返回嵌套节点正文')
  }
  return { ok: true, message: parsed }
}

function nativeHandleText(buffer) {
  if (!buffer || typeof buffer.length !== 'number' || buffer.length < 1) return ''
  let value = 0n
  const length = Math.min(buffer.length, 8)
  for (let index = length - 1; index >= 0; index -= 1) {
    value = (value << 8n) | BigInt(buffer[index])
  }
  return value.toString()
}

async function electronBrowserWindows() {
  const electron = await import('electron')
  const BrowserWindow = electron.BrowserWindow || electron.default?.BrowserWindow
  if (!BrowserWindow || typeof BrowserWindow.getAllWindows !== 'function') {
    throw new Error('当前插件进程无法访问 Electron BrowserWindow')
  }
  return BrowserWindow.getAllWindows()
}

/** 按拖拽落点的 HWND 精确读取同一个 QQ 窗口的聊天标题。 */
async function rendererTitleResponse(windowHandle) {
  const requested = asText(windowHandle).trim()
  if (!/^\d+$/.test(requested)) {
    return { ok: false, error: 'QQ 窗口句柄无效' }
  }
  let windows
  try {
    windows = await electronBrowserWindows()
  } catch (error) {
    return { ok: false, error: String(error) }
  }
  const window = windows.find((item) => {
    try {
      return nativeHandleText(item.getNativeWindowHandle()) === requested
    } catch {
      return false
    }
  })
  if (!window?.webContents || window.webContents.isDestroyed?.()) {
    return { ok: false, error: '拖拽落点对应的 QQ 渲染窗口不存在' }
  }
  try {
    const titles = await window.webContents.executeJavaScript(`(() => {
      const values = []
      const add = (value) => {
        const text = String(value || '').replace(/\\s+/g, ' ').trim()
        if (text && text.length <= 200 && !values.includes(text)) values.push(text)
      }
      const header = document.querySelector('.chat-header.panel-header, .chat-header')
      if (!header) return []
      for (const node of header.querySelectorAll('.panel-header__title')) {
        add(node.innerText || node.textContent)
      }
      if (!values.length) {
        const firstLine = String(header.innerText || header.textContent || '').split(/\\r?\\n/)[0]
        add(firstLine)
      }
      return values.slice(0, 4)
    })()`, true)
    if (!Array.isArray(titles) || !titles.length) {
      return { ok: false, error: 'QQ 渲染窗口没有可识别的聊天标题' }
    }
    return {
      ok: true,
      titleCandidates: titles.map((name, index) => ({ name, score: 300 - index })),
    }
  } catch (error) {
    return { ok: false, error: `读取 QQ 渲染窗口失败：${String(error)}` }
  }
}

async function currentResponse(windowHandle = '') {
  if (!state.active) {
    return { ok: false, available: false, error: '插件尚未启动' }
  }
  if (windowHandle) {
    const renderer = await rendererTitleResponse(windowHandle)
    if (renderer.ok) {
      return {
        ok: true,
        available: true,
        confidence: 'renderer_title',
        title_candidates: renderer.titleCandidates,
      }
    }
    return {
      ok: true,
      available: false,
      error: renderer.error,
      event_count: state.eventCount,
      last_event: state.lastEvent,
    }
  }
  const target = await toOneBotTarget(state.current)
  if (!target) {
    return {
      ok: true,
      available: false,
      error: '当前 QQ 会话尚未经过可靠的切换事件确认；请先切换到另一个会话，再打开目标会话',
      updated_at: state.updatedAt,
      source: state.source,
      event_count: state.eventCount,
      last_event: state.lastEvent,
    }
  }
  return {
    ok: true,
    available: true,
    confidence: 'active_tab',
    target,
    updated_at: state.updatedAt,
    source: state.source,
    event_count: state.eventCount,
    last_event: state.lastEvent,
    invalidated_at: state.invalidatedAt,
  }
}

function debugResponse() {
  return {
    ok: true,
    active: state.active,
    current: state.current,
    source: state.source,
    updated_at: state.updatedAt,
    event_count: state.eventCount,
    last_event: state.lastEvent,
  }
}

function mediaStatusResponse() {
  return {
    ok: true,
    updated_at: state.mediaUpdatedAt,
    media: state.mediaDiagnostics.map((item) => ({ ...item })),
  }
}

function matchingMethodNames(value) {
  const result = new Set()
  let current = value
  for (let depth = 0; current && depth < 6; depth += 1) {
    for (const name of Object.getOwnPropertyNames(current)) {
      if (/media|file|drag|download|export|save|element/i.test(name)) {
        try {
          if (typeof value?.[name] === 'function') result.add(name)
        } catch {
        }
      }
    }
    current = Object.getPrototypeOf(current)
  }
  return [...result].sort()
}

function mediaCapabilitiesResponse() {
  const session = state.ctx?.core?.context?.session
  return {
    ok: true,
    msg_service: matchingMethodNames(session?.getMsgService?.()),
    rich_media_service: matchingMethodNames(session?.getRichMediaService?.()),
    file_api: matchingMethodNames(state.ctx?.core?.apis?.FileApi),
  }
}

export async function plugin_init(ctx) {
  state.active = true
  state.ctx = ctx
  state.current = null
  state.source = ''
  state.updatedAt = 0
  state.eventCount = 0
  state.lastEvent = ''
  state.invalidatedAt = 0
  state.mediaDiagnostics = []
  state.mediaUpdatedAt = 0

  ctx.router.get('/current-peer', async (req, res) => {
    try {
      res.json(await currentResponse(queryValue(req, 'window_handle')))
    } catch (error) {
      ctx.logger.error('读取当前 QQ 会话失败', error)
      res.status(500).json({ ok: false, available: false, error: '读取当前 QQ 会话失败' })
    }
  })
  ctx.router.get('/debug-state', (_req, res) => res.json(debugResponse()))
  ctx.router.get('/media-status', (_req, res) => res.json(mediaStatusResponse()))
  ctx.router.get('/media-capabilities', (_req, res) => {
    res.json(mediaCapabilitiesResponse())
  })
  ctx.router.get('/forward-message', async (req, res) => {
    try {
      res.json(await forwardStoredMessage({
        source_type: queryValue(req, 'source_type'),
        source_peer_id: queryValue(req, 'source_peer_id'),
        source_real_seq: queryValue(req, 'source_real_seq'),
        source_time: queryValue(req, 'source_time'),
        source_sender_id: queryValue(req, 'source_sender_id'),
        destination_type: queryValue(req, 'destination_type'),
        destination_peer_id: queryValue(req, 'destination_peer_id'),
      }))
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      ctx.logger.error('按持久索引原生转发失败', error)
      res.json({ ok: false, error: message })
    }
  })
  ctx.router.get('/recover-message', async (req, res) => {
    try {
      res.json(await recoverStoredMessage({
        source_type: queryValue(req, 'source_type'),
        source_peer_id: queryValue(req, 'source_peer_id'),
        source_real_seq: queryValue(req, 'source_real_seq'),
        source_time: queryValue(req, 'source_time'),
        source_sender_id: queryValue(req, 'source_sender_id'),
      }))
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      ctx.logger.error('按来源位置恢复嵌套消息失败', error)
      res.json({ ok: false, error: message })
    }
  })
  ctx.router.get('/recover-forward-resource', async (req, res) => {
    try {
      res.json(await recoverForwardResource({
        res_id: queryValue(req, 'res_id'),
        source_type: queryValue(req, 'source_type'),
        source_peer_id: queryValue(req, 'source_peer_id'),
        source_real_seq: queryValue(req, 'source_real_seq'),
        source_time: queryValue(req, 'source_time'),
        source_sender_id: queryValue(req, 'source_sender_id'),
      }))
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      ctx.logger.error('恢复多层聊天记录资源失败', error)
      res.json({ ok: false, error: message })
    }
  })

  const eventWrapper = ctx.core?.eventWrapper
  if (!eventWrapper || typeof eventWrapper.createListenerFunction !== 'function') {
    throw new Error('当前 NapCat 未提供 MsgListener 内部监听器')
  }
  const listener = eventWrapper.createListenerFunction('NodeIKernelMsgListener', PLUGIN_ID)
  const listenerHandlers = {
    onUserTabStatusChanged: (payload) => recordTabEvent(payload),
    onMsgBoxChanged: (payload) => recordMsgBoxEvent(payload),
  }
  Object.assign(listener, listenerHandlers)
  // onMsgInfoListUpdate/onMsgInfoListAdd/onInputStatusPush 可能来自后台会话，
  // 绝不能据此覆盖当前 AIO；此前的误发正是由这类事件造成。
  state.listener = listener
  state.listenerHandlers = listenerHandlers
  ctx.logger.info('OhMyMeme 当前会话桥接已启动')
}

export async function plugin_cleanup() {
  // createListenerFunction 会在热重载时复用底层 listener。只移除本模块
  // 自己安装的函数，避免旧闭包在新模块加载后继续处理事件。
  if (state.listener && state.listenerHandlers) {
    for (const [name, handler] of Object.entries(state.listenerHandlers)) {
      if (state.listener[name] === handler) delete state.listener[name]
    }
  }
  state.active = false
  state.current = null
  state.source = ''
  state.updatedAt = 0
  state.mediaDiagnostics = []
  state.mediaUpdatedAt = 0
  state.listener = null
  state.listenerHandlers = null
  state.ctx = null
}

export const __test = {
  candidateFromObject,
  currentResponse,
  debugResponse,
  invalidateCurrent,
  recordEvent,
  recordMsgBoxEvent,
  recordTabEvent,
  setCurrent,
  state,
  toOneBotTarget,
  findMessageBySeq,
  isForwardCard,
  oneBotPeer,
  queryValue,
  recoverStoredMessage,
  recoverForwardResource,
  nativeHandleText,
  rendererTitleResponse,
  isOfficialQQMediaUrl,
  fetchOfficialQQImage,
  downloadQQImageByMessage,
  downloadQQImageInVisit,
  downloadQQImageByUuid,
  existingImagePath,
  safeDiagnosticError,
  diagnosticValueShape,
  diagnosticObjectFields,
  safeDiagnosticStack,
  mediaStatusResponse,
  mediaCapabilitiesResponse,
}
