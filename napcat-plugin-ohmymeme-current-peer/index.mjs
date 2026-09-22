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

import { gzipSync, gunzipSync, inflateSync } from 'node:zlib'
import {
  existsSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs'
import { createHash, randomUUID } from 'node:crypto'
import { tmpdir } from 'node:os'
import { basename, dirname, isAbsolute, sep } from 'node:path'

const CHAT_TYPE_C2C = 1
const CHAT_TYPE_GROUP = 2
const MAX_SCAN_NODES = 5000
const MAX_ARCHIVE_IMAGE_BYTES = 64 * 1024 * 1024
const MAX_NATIVE_FORWARD_BYTES = 64 * 1024 * 1024
const MAX_NATIVE_FORWARD_EXPORT_BYTES = 5 * 1024 * 1024
const MAX_NATIVE_FORWARD_DEPTH = 12
const NATIVE_FORWARD_TEMP_PREFIX = 'ohmymeme-native-forward-'
const FORWARD_CARD_APP = 'com.tencent.multimsg'
const ELEMENT_TYPE_FILE = 3
const ELEMENT_TYPE_PTT = 4
const ELEMENT_TYPE_VIDEO = 5
const ELEMENT_TYPE_ARK = 10
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
  sendMsgTarget: null,
  sendMsgOriginal: null,
  sendMsgWrapper: null,
  packetConverterPatches: [],
  forwardCardRewriteCount: 0,
  sourceGroupNames: new Map(),
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

function presentKeys(value) {
  const object = asObject(value)
  if (!object) return []
  return Object.entries(object)
    .filter(([, item]) => item !== null && item !== undefined)
    .map(([key]) => key)
    .sort()
}

/** 将 NapCat 伪造转发卡片修正为 QQNT 原生 extra 字符串格式。 */
export function normalizeForwardCardBytes(bytesData) {
  if (typeof bytesData !== 'string' || !bytesData) {
    return { value: bytesData, changed: false }
  }
  let card
  try {
    card = JSON.parse(bytesData)
  } catch {
    return { value: bytesData, changed: false }
  }
  if (
    !card
    || card.app !== FORWARD_CARD_APP
    || !card.extra
    || typeof card.extra !== 'object'
    || Array.isArray(card.extra)
  ) {
    return { value: bytesData, changed: false }
  }
  return {
    value: JSON.stringify({
      ...card,
      extra: `${JSON.stringify(card.extra)}\n`,
    }),
    changed: true,
  }
}

/** 只复制并修正需要兼容处理的 ARK 元素。 */
export function normalizeForwardArkElement(element) {
  if (!element?.arkElement) return { value: element, changed: false }
  const normalized = normalizeForwardCardBytes(element.arkElement.bytesData)
  if (!normalized.changed) return { value: element, changed: false }
  return {
    value: {
      ...element,
      arkElement: {
        ...element.arkElement,
        bytesData: normalized.value,
      },
    },
    changed: true,
  }
}

function normalizeForwardArkElements(elements) {
  if (!Array.isArray(elements)) return { value: elements, changed: false, count: 0 }
  let changed = false
  let count = 0
  const value = elements.map((element) => {
    const normalized = normalizeForwardArkElement(element)
    if (normalized.changed) {
      changed = true
      count += 1
    }
    return normalized.value
  })
  return { value: changed ? value : elements, changed, count }
}

function normalizeForwardCardsInRawMessage(raw) {
  if (!raw || typeof raw !== 'object') return { value: raw, changed: false, count: 0 }
  let value = raw
  let changed = false
  let count = 0
  for (const key of ['msg', 'elements']) {
    const normalized = normalizeForwardArkElements(raw[key])
    if (!normalized.changed) continue
    if (!changed) value = { ...raw }
    value[key] = normalized.value
    changed = true
    count += normalized.count
  }
  return { value, changed, count }
}

function installForwardCardCompatibility(ctx) {
  const msgApi = ctx.core?.apis?.MsgApi
  if (msgApi && typeof msgApi.sendMsg === 'function') {
    const original = msgApi.sendMsg
    const wrapper = function (peer, elements, ...args) {
      const normalized = normalizeForwardArkElements(elements)
      state.forwardCardRewriteCount += normalized.count
      return original.call(this, peer, normalized.value, ...args)
    }
    state.sendMsgTarget = msgApi
    state.sendMsgOriginal = original
    state.sendMsgWrapper = wrapper
    msgApi.sendMsg = wrapper
  }

  const converter = ctx.core?.apis?.PacketApi?.pkt?.msgConverter
  if (!converter) return
  for (const name of ['rawMsgWithSendMsgToPacketMsg', 'rawMsgToPacketMsg']) {
    if (typeof converter[name] !== 'function') continue
    const original = converter[name]
    const wrapper = function (raw, ...args) {
      const normalized = normalizeForwardCardsInRawMessage(raw)
      state.forwardCardRewriteCount += normalized.count
      return original.call(this, normalized.value, ...args)
    }
    state.packetConverterPatches.push({ converter, name, original, wrapper })
    converter[name] = wrapper
  }
}

function removeForwardCardCompatibility() {
  if (
    state.sendMsgTarget
    && state.sendMsgWrapper
    && state.sendMsgTarget.sendMsg === state.sendMsgWrapper
  ) {
    state.sendMsgTarget.sendMsg = state.sendMsgOriginal
  }
  for (const patch of state.packetConverterPatches.toReversed()) {
    if (patch.converter[patch.name] === patch.wrapper) {
      patch.converter[patch.name] = patch.original
    }
  }
  state.sendMsgTarget = null
  state.sendMsgOriginal = null
  state.sendMsgWrapper = null
  state.packetConverterPatches = []
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
  let nativePayloadBase64 = ''
  if (typeof operation?.sendPacket === 'function') {
    try {
      const nativePayload = await downloadNativeForwardProto(operation, resId)
      if (nativePayload.length <= MAX_NATIVE_FORWARD_EXPORT_BYTES) {
        nativePayloadBase64 = nativePayload.toString('base64')
      }
    } catch {
    }
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
        if (raw.senderUid) node.omm_sender_uid = asText(raw.senderUid)
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
    native_payload_b64: nativePayloadBase64,
    native_payload_preserved: Boolean(nativePayloadBase64),
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

function nativeForwardTempPath(value) {
  const requested = asText(value).trim()
  if (!requested || !isAbsolute(requested)) {
    throw new Error('原生重组临时文件路径无效')
  }
  const root = realpathSync(tmpdir())
  const candidate = realpathSync(requested)
  const rootPrefix = `${root}${root.endsWith(sep) ? '' : sep}`.toLowerCase()
  if (
    !candidate.toLowerCase().startsWith(rootPrefix)
    || !basename(candidate).startsWith(NATIVE_FORWARD_TEMP_PREFIX)
  ) {
    throw new Error('原生重组临时文件不在允许目录内')
  }
  const size = statSync(candidate).size
  if (!Number.isSafeInteger(size) || size <= 0 || size > MAX_NATIVE_FORWARD_BYTES) {
    throw new Error('原生重组数据大小无效')
  }
  return candidate
}

function validateNativeForwardNodes(nodes, depth = 0, counter = { value: 0 }) {
  if (!Array.isArray(nodes) || nodes.length === 0) {
    throw new Error('原生重组没有可发送的聊天记录节点')
  }
  if (depth > MAX_NATIVE_FORWARD_DEPTH) {
    throw new Error('原生重组嵌套层级超过安全上限')
  }
  for (const node of nodes) {
    counter.value += 1
    if (counter.value > MAX_SCAN_NODES) {
      throw new Error('原生重组节点数量超过安全上限')
    }
    if (node?.type !== 'node' || !asObject(node.data)) {
      throw new Error('原生重组节点格式无效')
    }
    const content = node.data.content
    if (!Array.isArray(content) || content.length === 0) {
      throw new Error('原生重组节点正文为空')
    }
    const children = content.filter((item) => item?.type === 'node')
    if (children.length && children.length !== content.length) {
      throw new Error('嵌套聊天记录不能与普通消息段混合')
    }
    if (children.length) validateNativeForwardNodes(children, depth + 1, counter)
  }
  return counter.value
}

/** 读取并立即删除 OhMyMeme 写入系统临时目录的节点树。 */
export function loadNativeForwardPayload(payloadPath) {
  const candidate = nativeForwardTempPath(payloadPath)
  let raw
  try {
    raw = readFileSync(candidate, 'utf8')
  } finally {
    try {
      unlinkSync(candidate)
    } catch {
    }
  }
  let payload
  try {
    payload = JSON.parse(raw)
  } catch {
    throw new Error('原生重组临时文件不是有效 JSON')
  }
  if (!asObject(payload) || Number(payload.schema_version) !== 1) {
    throw new Error('原生重组数据版本不受支持')
  }
  const destinationType = asText(payload.destination_type).trim()
  if (!['group', 'private'].includes(destinationType)) {
    throw new Error('原生重组目标会话类型无效')
  }
  const destinationPeerId = requireDigits(
    payload.destination_peer_id,
    destinationType === 'group' ? '群号' : 'QQ 号',
  )
  validateNativeForwardNodes(payload.messages)
  const nativePayloadBase64 = asText(payload.native_payload_b64).trim()
  let nativePayload = null
  if (nativePayloadBase64) {
    if (!/^[A-Za-z0-9+/]+={0,2}$/.test(nativePayloadBase64)) {
      throw new Error('原生聊天记录负载编码无效')
    }
    nativePayload = Buffer.from(nativePayloadBase64, 'base64')
    if (
      !nativePayload.length
      || nativePayload.length > MAX_NATIVE_FORWARD_BYTES
      || nativePayload.toString('base64') !== nativePayloadBase64
    ) {
      throw new Error('原生聊天记录负载大小或编码无效')
    }
  }
  const nativeCard = asObject(payload.native_card)
  if (nativeCard && nativeCard.app !== FORWARD_CARD_APP) {
    throw new Error('原生聊天记录卡片格式无效')
  }
  const nativeSourceType = asText(payload.native_source_type).trim()
  const nativeSourcePeerId = asText(payload.native_source_peer_id).trim()
  if (nativePayload && !['group', 'private'].includes(nativeSourceType)) {
    throw new Error('原生聊天记录缺少资源来源会话类型')
  }
  if (
    nativePayload
    && nativeSourceType === 'group'
    && !/^\d+$/.test(nativeSourcePeerId)
  ) {
    throw new Error('原生聊天记录缺少资源来源群号')
  }
  return {
    destination_type: destinationType,
    destination_peer_id: destinationPeerId,
    messages: payload.messages,
    nativePayload,
    nativeCard,
    nativeSourceType,
    nativeSourcePeerId,
  }
}

function forwardNodeName(node) {
  return asText(node?.data?.nickname || node?.data?.name || 'QQ用户')
    .trim()
    .slice(0, 100) || 'QQ用户'
}

function isSpecialNativeForwardElement(element) {
  return [
    ELEMENT_TYPE_FILE,
    ELEMENT_TYPE_PTT,
    ELEMENT_TYPE_VIDEO,
    ELEMENT_TYPE_ARK,
  ].includes(Number(element?.elementType))
}

async function stageNativeForwardLeaf(conversionPeer, selfPeer, content) {
  const msgApi = state.ctx?.oneBot?.apis?.MsgApi
  if (
    typeof msgApi?.createSendElements !== 'function'
    || typeof msgApi?.sendMsgWithOb11UniqueId !== 'function'
  ) {
    throw new Error('当前 NapCat 未提供原生消息暂存接口')
  }
  const created = await msgApi.createSendElements(content, conversionPeer)
  const elements = Array.isArray(created?.sendElements) ? created.sendElements : []
  const ordinary = elements.filter((element) => !isSpecialNativeForwardElement(element))
  const batches = [
    ...(ordinary.length ? [ordinary] : []),
    ...elements.filter(isSpecialNativeForwardElement).map((element) => [element]),
  ]
  if (!batches.length) throw new Error('原生重组节点没有可发送的消息元素')
  const result = []
  for (const batch of batches) {
    const message = await msgApi.sendMsgWithOb11UniqueId(
      selfPeer,
      batch,
      Array.isArray(created?.deleteAfterSentFiles) ? created.deleteAfterSentFiles : [],
    )
    const msgId = asText(message?.msgId).trim()
    if (!msgId) throw new Error('QQNT 未返回暂存消息 ID')
    result.push(msgId)
  }
  return result
}

function isForwardCardMessage(message) {
  for (const element of Array.isArray(message?.elements) ? message.elements : []) {
    const bytes = asText(element?.arkElement?.bytesData)
    if (!bytes) continue
    try {
      if (JSON.parse(bytes)?.app === FORWARD_CARD_APP) return true
    } catch {
    }
  }
  return false
}

function tryNativeForwardCard(message) {
  let multiForwardResId = ''
  let arkCandidate = null
  for (const element of Array.isArray(message?.elements) ? message.elements : []) {
    multiForwardResId ||= asText(
      element?.multiForwardMsgElement?.resId
      || element?.multiForwardMsgElement?.resid,
    ).trim()
    const bytes = asText(element?.arkElement?.bytesData)
    if (!bytes) continue
    try {
      const card = JSON.parse(bytes)
      if (card?.app === FORWARD_CARD_APP) {
        arkCandidate = { element, card }
        const resId = asText(
          card?.meta?.detail?.resid || card?.meta?.detail?.resId,
        ).trim()
        if (resId) return { element, card, resId }
      }
    } catch {
    }
  }
  if (arkCandidate && multiForwardResId) {
    return { ...arkCandidate, resId: multiForwardResId }
  }
  return null
}

function nativeForwardStructure(message) {
  const elements = Array.isArray(message?.elements) ? message.elements : []
  let arkDetailKeys = []
  let hasForwardArk = false
  for (const element of elements) {
    const bytes = asText(element?.arkElement?.bytesData)
    if (!bytes) continue
    try {
      const card = JSON.parse(bytes)
      if (card?.app !== FORWARD_CARD_APP) continue
      hasForwardArk = true
      arkDetailKeys = presentKeys(card?.meta?.detail)
    } catch {
    }
  }
  return {
    has_forward_ark: hasForwardArk,
    has_multi_forward: elements.some((element) => (
      Boolean(element?.multiForwardMsgElement)
    )),
    ark_detail_keys: arkDetailKeys,
    element_keys: elements.map(presentKeys),
  }
}

function waitForNativeForward(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

async function nativeForwardCard(message, peer) {
  const direct = tryNativeForwardCard(message)
  if (direct) return direct

  const api = state.ctx?.core?.apis?.MsgApi
  const msgId = asText(message?.msgId || message?.id).trim()
  const msgSeq = asText(message?.msgSeq).trim()
  let queried = 0
  let queryErrors = 0
  const delays = [0, 80, 220, 500]
  for (const delay of delays) {
    if (delay) await waitForNativeForward(delay)
    const rows = []
    if (msgId && typeof api?.getMsgsByMsgId === 'function') {
      try {
        rows.push(...messageList(await api.getMsgsByMsgId(peer, [msgId])))
      } catch {
        queryErrors += 1
      }
    }
    if (msgSeq && typeof api?.getMsgsBySeqAndCount === 'function') {
      try {
        rows.push(...messageList(await api.getMsgsBySeqAndCount(
          peer,
          msgSeq,
          1,
          true,
          true,
        )))
      } catch {
        queryErrors += 1
      }
    }
    queried += rows.length
    const exact = rows.filter((row) => (
      (!msgId || asText(row?.msgId || row?.id) === msgId)
      && (!msgSeq || asText(row?.msgSeq) === msgSeq)
    ))
    for (const row of [...exact, ...rows]) {
      const stored = tryNativeForwardCard(row)
      if (stored) return stored
    }
  }

  const shape = nativeForwardStructure(message)
  throw new Error(
    'QQNT 原生模板没有返回聊天记录资源；'
    + `即时结构=${JSON.stringify(shape)}；落库候选=${queried}；查询异常=${queryErrors}`,
  )
}

function normalizeForwardTimestamp(value) {
  let timestamp = Number(value)
  if (!Number.isFinite(timestamp) || timestamp <= 0) return 0
  if (timestamp > 1e12) timestamp = Math.floor(timestamp / 1000)
  return Math.floor(timestamp)
}

function nativeForwardEntry(node, msgId) {
  const data = asObject(node?.data) || {}
  return {
    msgId: asText(msgId).trim(),
    senderName: forwardNodeName(node),
    senderUin: asText(data.user_id || data.uin).trim(),
    senderUid: asText(data.omm_sender_uid).trim(),
    time: normalizeForwardTimestamp(data.time),
    sourceType: asText(data.omm_source_type).trim(),
    sourcePeerId: asText(data.omm_source_peer_id).trim(),
  }
}

async function resolveNativeForwardIdentity(entry) {
  const selfInfo = state.ctx?.core?.selfInfo || {}
  const selfUin = asText(selfInfo.uin).trim()
  const senderUin = /^\d+$/.test(entry.senderUin) ? entry.senderUin : selfUin
  let senderUid = entry.senderUid
  if (!senderUid && senderUin && senderUin === selfUin) {
    senderUid = asText(selfInfo.uid).trim()
  }
  if (!senderUid && senderUin) {
    const converter = state.ctx?.core?.apis?.UserApi?.getUidByUinV2
    if (typeof converter === 'function') {
      try {
        senderUid = asText(await converter.call(
          state.ctx.core.apis.UserApi,
          senderUin,
        )).trim()
      } catch {
      }
    }
  }
  if (!senderUin || !senderUid) {
    throw new Error(
      '无法把某个归档发送者转换为 QQNT 身份；'
      + '请重新导入该聊天记录后再试',
    )
  }
  let sourcePeerName = ''
  if (entry.sourceType === 'group' && /^\d+$/.test(entry.sourcePeerId)) {
    if (state.sourceGroupNames.has(entry.sourcePeerId)) {
      sourcePeerName = state.sourceGroupNames.get(entry.sourcePeerId)
    } else {
      const getGroups = state.ctx?.core?.apis?.GroupApi?.getGroups
      if (typeof getGroups === 'function') {
        try {
          const groups = await getGroups.call(state.ctx.core.apis.GroupApi, false)
          for (const group of Array.isArray(groups) ? groups : []) {
            const groupId = asText(
              group?.groupCode || group?.groupUin || group?.peerUid,
            ).trim()
            if (!groupId) continue
            state.sourceGroupNames.set(groupId, asText(group?.groupName).trim())
          }
          sourcePeerName = state.sourceGroupNames.get(entry.sourcePeerId) || ''
        } catch {
        }
      }
      state.sourceGroupNames.set(entry.sourcePeerId, sourcePeerName)
    }
  }
  return { ...entry, senderUin, senderUid, sourcePeerName }
}

function patchNativeForwardBody(body, entry) {
  const response = asObject(body?.responseHead)
  const content = asObject(body?.contentHead)
  if (!response || !content) {
    throw new Error('QQNT 原生模板缺少消息头')
  }
  response.fromUin = Number(entry.senderUin)
  response.fromUid = entry.senderUid
  if (entry.sourceType === 'group' && /^\d+$/.test(entry.sourcePeerId)) {
    delete response.toUid
    response.forward = {}
    response.grp = {
      groupUin: Number(entry.sourcePeerId),
      memberName: entry.senderName,
      unknown5: 2,
      ...(entry.sourcePeerName ? { groupName: entry.sourcePeerName } : {}),
    }
    content.type = 82
    delete content.subType
    delete content.c2cCmd
    delete content.divSeq
    if (asObject(content.forward)) content.forward.field3 = 1
    const elems = body?.body?.richText?.elems
    if (Array.isArray(elems)) {
      if (!elems.some((element) => asObject(element?.generalFlags))) {
        elems.unshift({ generalFlags: {} })
      }
      if (!elems.some((element) => asObject(element?.elemFlags2))) {
        elems.splice(1, 0, { elemFlags2: {} })
      }
    }
  } else {
    delete response.grp
    response.forward ||= {}
    response.forward.friendName = entry.senderName
    if (entry.sourceType === 'private') {
      response.toUid = asText(state.ctx?.core?.selfInfo?.uid)
      content.type = 9
      content.subType = 4
      delete content.c2cCmd
      content.divSeq = 4
      if (asObject(content.forward)) content.forward.field3 = 2
    }
  }
  if (entry.time) content.timeStamp = entry.time
  if (asObject(content.forward)) {
    content.forward.unknownBase64 = (
      `https://q.qlogo.cn/headimg_dl?dst_uin=${entry.senderUin}`
      + '&spec=0&img_type=jpg'
    )
    delete content.forward.avatar
  }
}

function encodeProtoVarint(value) {
  let current = BigInt(value)
  if (current < 0n) throw new Error('Protobuf varint 不能为负数')
  const bytes = []
  do {
    let byte = Number(current & 0x7fn)
    current >>= 7n
    if (current) byte |= 0x80
    bytes.push(byte)
  } while (current)
  return Buffer.from(bytes)
}

function readWireVarint(buffer, offset) {
  let value = 0n
  let shift = 0n
  let cursor = offset
  for (let count = 0; count < 10 && cursor < buffer.length; count += 1) {
    const byte = buffer[cursor]
    cursor += 1
    value |= BigInt(byte & 0x7f) << shift
    if (!(byte & 0x80)) return { value, next: cursor }
    shift += 7n
  }
  throw new Error('Protobuf varint 无效')
}

function protoWireFields(buffer) {
  const fields = []
  let cursor = 0
  while (cursor < buffer.length) {
    const start = cursor
    const key = readWireVarint(buffer, cursor)
    cursor = key.next
    const number = Number(key.value >> 3n)
    const wire = Number(key.value & 7n)
    let value
    if (!number) throw new Error('Protobuf 字段号无效')
    if (wire === 0) {
      const scalar = readWireVarint(buffer, cursor)
      value = scalar.value
      cursor = scalar.next
    } else if (wire === 1) {
      if (cursor + 8 > buffer.length) throw new Error('Protobuf fixed64 越界')
      value = buffer.subarray(cursor, cursor + 8)
      cursor += 8
    } else if (wire === 2) {
      const size = readWireVarint(buffer, cursor)
      cursor = size.next
      const length = Number(size.value)
      if (!Number.isSafeInteger(length) || length < 0 || cursor + length > buffer.length) {
        throw new Error('Protobuf bytes 越界')
      }
      value = buffer.subarray(cursor, cursor + length)
      cursor += length
    } else if (wire === 5) {
      if (cursor + 4 > buffer.length) throw new Error('Protobuf fixed32 越界')
      value = buffer.subarray(cursor, cursor + 4)
      cursor += 4
    } else {
      throw new Error(`Protobuf wire type ${wire} 不受支持`)
    }
    fields.push({ number, wire, value, raw: buffer.subarray(start, cursor) })
  }
  return fields
}

function protoBytesField(number, value) {
  const bytes = Buffer.from(value)
  return Buffer.concat([
    encodeProtoVarint((BigInt(number) << 3n) | 2n),
    encodeProtoVarint(bytes.length),
    bytes,
  ])
}

function protoVarintField(number, value) {
  return Buffer.concat([
    encodeProtoVarint(BigInt(number) << 3n),
    encodeProtoVarint(value),
  ])
}

function firstProtoBytes(buffer, number) {
  return protoWireFields(buffer).find((field) => (
    field.number === number && field.wire === 2
  ))?.value
}

function firstProtoString(buffer, number) {
  const value = firstProtoBytes(buffer, number)
  return value ? value.toString('utf8') : ''
}

function setProtoField(buffer, number, replacement) {
  const result = []
  let replaced = false
  for (const field of protoWireFields(buffer)) {
    if (field.number !== number) {
      result.push(field.raw)
      continue
    }
    if (!replaced && replacement) result.push(replacement)
    replaced = true
  }
  if (!replaced && replacement) result.push(replacement)
  return Buffer.concat(result)
}

function setProtoBytes(buffer, number, value) {
  return setProtoField(buffer, number, protoBytesField(number, value))
}

function setProtoString(buffer, number, value) {
  return setProtoBytes(buffer, number, Buffer.from(value, 'utf8'))
}

function setProtoVarint(buffer, number, value) {
  return setProtoField(buffer, number, protoVarintField(number, value))
}

function rewriteRepeatedProtoBytes(buffer, number, mapper) {
  const result = []
  let index = 0
  for (const field of protoWireFields(buffer)) {
    if (field.number !== number || field.wire !== 2) {
      result.push(field.raw)
      continue
    }
    const rewritten = mapper(field.value, index)
    index += 1
    if (rewritten !== null) result.push(protoBytesField(number, rewritten))
  }
  return { value: Buffer.concat(result), count: index }
}

function sourceCompatibleRichText(richText) {
  const fields = protoWireFields(richText)
  let hasGeneralFlags = false
  let hasElemFlags2 = false
  for (const field of fields) {
    if (field.number !== 2 || field.wire !== 2) continue
    const elementFields = protoWireFields(field.value)
    hasGeneralFlags ||= elementFields.some((item) => item.number === 37)
    hasElemFlags2 ||= elementFields.some((item) => item.number === 9)
  }
  const prefix = []
  if (!hasGeneralFlags) {
    prefix.push(protoBytesField(2, protoBytesField(37, Buffer.alloc(0))))
  }
  if (!hasElemFlags2) {
    prefix.push(protoBytesField(2, protoBytesField(9, Buffer.alloc(0))))
  }
  return prefix.length ? Buffer.concat([...prefix, ...fields.map((field) => field.raw)]) : richText
}

function patchGroupSourceProtoBody(body, response, content, entry) {
  response = setProtoField(response, 6, null)
  response = setProtoBytes(response, 7, Buffer.alloc(0))
  let group = Buffer.concat([
    protoVarintField(1, entry.sourcePeerId),
    protoStringMessage(4, entry.senderName),
    protoVarintField(5, 2),
  ])
  if (entry.sourcePeerName) {
    group = Buffer.concat([group, protoStringMessage(7, entry.sourcePeerName)])
  }
  response = setProtoBytes(response, 8, group)

  content = setProtoVarint(content, 1, 82)
  content = setProtoField(content, 2, null)
  content = setProtoField(content, 3, null)
  content = setProtoField(content, 9, null)
  content = setProtoField(content, 12, null)
  const forward = firstProtoBytes(content, 15) || Buffer.alloc(0)
  content = setProtoBytes(content, 15, setProtoVarint(forward, 3, 1))

  const messageBody = firstProtoBytes(body, 3)
  const richText = messageBody && firstProtoBytes(messageBody, 1)
  if (messageBody && richText) {
    body = setProtoBytes(
      body,
      3,
      setProtoBytes(messageBody, 1, sourceCompatibleRichText(richText)),
    )
  }
  return { body, response, content }
}

function patchPrivateSourceProtoBody(response, content, entry) {
  response = setProtoString(
    response,
    6,
    asText(state.ctx?.core?.selfInfo?.uid).trim(),
  )
  response = setProtoField(response, 8, null)
  const responseForward = firstProtoBytes(response, 7) || Buffer.alloc(0)
  response = setProtoBytes(
    response,
    7,
    setProtoString(responseForward, 6, entry.senderName),
  )
  content = setProtoVarint(content, 1, 9)
  content = setProtoVarint(content, 2, 4)
  content = setProtoField(content, 3, null)
  content = setProtoVarint(content, 9, 4)
  const forward = firstProtoBytes(content, 15) || Buffer.alloc(0)
  content = setProtoBytes(content, 15, setProtoVarint(forward, 3, 2))
  return { response, content }
}

function patchNativeForwardProtoBody(body, entry) {
  let response = firstProtoBytes(body, 1)
  let content = firstProtoBytes(body, 2)
  if (!response || !content) throw new Error('QQNT 原生模板缺少消息头')

  response = setProtoVarint(response, 1, entry.senderUin)
  response = setProtoString(response, 2, entry.senderUid)
  if (entry.sourceType === 'group' && /^\d+$/.test(entry.sourcePeerId)) {
    const patched = patchGroupSourceProtoBody(body, response, content, entry)
    body = patched.body
    response = patched.response
    content = patched.content
  } else if (entry.sourceType === 'private') {
    const patched = patchPrivateSourceProtoBody(response, content, entry)
    response = patched.response
    content = patched.content
  } else {
    const forward = firstProtoBytes(response, 7) || Buffer.alloc(0)
    response = setProtoBytes(response, 7, setProtoString(forward, 6, entry.senderName))
  }

  if (entry.time) content = setProtoVarint(content, 6, entry.time)
  const forward = firstProtoBytes(content, 15)
  if (forward) {
    const avatar = (
      `https://q.qlogo.cn/headimg_dl?dst_uin=${entry.senderUin}`
      + '&spec=0&img_type=jpg'
    )
    let patchedForward = setProtoString(forward, 5, avatar)
    patchedForward = setProtoField(patchedForward, 6, null)
    content = setProtoBytes(content, 15, patchedForward)
  }
  body = setProtoBytes(body, 1, response)
  return setProtoBytes(body, 2, content)
}

function patchNativeForwardProtoPayload(payload, entries) {
  let foundMain = false
  let patchedCount = 0
  const actions = rewriteRepeatedProtoBytes(payload, 2, (action) => {
    if (firstProtoString(action, 1) !== 'MultiMsg') return action
    if (foundMain) throw new Error('QQNT 原生资源包含多个主 action')
    foundMain = true
    const data = firstProtoBytes(action, 2)
    if (!data) throw new Error('QQNT 原生资源缺少主 action 正文')
    const bodies = rewriteRepeatedProtoBytes(data, 1, (body, index) => {
      const entry = entries[index]
      if (!entry) throw new Error('QQNT 原生模板节点数超出归档')
      patchedCount += 1
      return patchNativeForwardProtoBody(body, entry)
    })
    return setProtoBytes(action, 2, bodies.value)
  })
  if (!foundMain) throw new Error('聊天记录资源缺少主 MultiMsg action')
  if (patchedCount !== entries.length) {
    throw new Error(`QQNT 原生模板节点数不匹配（${patchedCount}/${entries.length}）`)
  }
  return actions.value
}

function longMessageSettings(download) {
  return Buffer.concat([
    protoVarintField(1, download ? 2 : 4),
    protoVarintField(2, download ? 0 : 1),
    protoVarintField(3, download ? 0 : 7),
    protoVarintField(4, 0),
  ])
}

async function downloadNativeForwardProto(operation, resId) {
  if (typeof operation?.sendPacket !== 'function') {
    throw new Error('当前 NapCat 未提供原始长消息下载接口')
  }
  const selfUid = asText(state.ctx?.core?.selfInfo?.uid).trim()
  const uid = protoBytesField(1, protoStringMessage(2, selfUid))
  const info = Buffer.concat([
    uid,
    protoBytesField(2, Buffer.from(resId, 'utf8')),
    protoVarintField(3, 1),
  ])
  const request = Buffer.concat([
    protoBytesField(1, info),
    protoBytesField(15, longMessageSettings(true)),
  ])
  const response = await operation.sendPacket({
    cmd: 'trpc.group.long_msg_interface.MsgService.SsoRecvLongMsg',
    data: request,
  }, true)
  const result = firstProtoBytes(response, 1)
  const compressed = result && firstProtoBytes(result, 4)
  if (!compressed) throw new Error('QQNT 原始长消息下载结果缺少负载')
  return gunzipSync(compressed)
}

function protoStringMessage(number, value) {
  return protoBytesField(number, Buffer.from(value, 'utf8'))
}

async function uploadNativeForwardProto(operation, payload, destinationPeer) {
  if (typeof operation?.sendPacket !== 'function') {
    throw new Error('当前 NapCat 未提供原始长消息上传接口')
  }
  const isGroup = destinationPeer.chatType === CHAT_TYPE_GROUP
  const groupUin = isGroup ? asText(destinationPeer.peerUid) : '0'
  const owner = isGroup
    ? groupUin
    : asText(state.ctx?.core?.selfInfo?.uid).trim()
  const info = Buffer.concat([
    protoVarintField(1, isGroup ? 3 : 1),
    protoBytesField(2, protoStringMessage(2, owner)),
    protoVarintField(3, groupUin),
    protoBytesField(4, gzipSync(payload)),
  ])
  const request = Buffer.concat([
    protoBytesField(2, info),
    protoBytesField(15, longMessageSettings(false)),
  ])
  const response = await operation.sendPacket({
    cmd: 'trpc.group.long_msg_interface.MsgService.SsoSendLongMsg',
    data: request,
  }, true)
  const result = firstProtoBytes(response, 2)
  const newResId = result && firstProtoString(result, 3)
  if (!newResId) throw new Error('QQNT 未返回原始重建资源 ID')
  return newResId
}

function validateNativeForwardProtoPayload(payload) {
  let mainCount = 0
  let nodeCount = 0
  for (const actionField of protoWireFields(payload)) {
    if (actionField.number !== 2 || actionField.wire !== 2) continue
    const action = actionField.value
    if (firstProtoString(action, 1) !== 'MultiMsg') continue
    mainCount += 1
    const data = firstProtoBytes(action, 2)
    if (!data) throw new Error('原生聊天记录负载缺少主 action 正文')
    nodeCount += protoWireFields(data).filter((field) => (
      field.number === 1 && field.wire === 2
    )).length
  }
  if (mainCount !== 1 || !nodeCount) {
    throw new Error('原生聊天记录负载缺少唯一且非空的 MultiMsg action')
  }
  return nodeCount
}

async function sendNativeForwardResourceCard(
  element,
  templateCard,
  newResId,
  destinationPeer,
  nodeCount,
  archivedCard = null,
) {
  let card = templateCard
  if (archivedCard) {
    card = JSON.parse(JSON.stringify(archivedCard))
  }
  if (!asObject(card?.meta?.detail)) {
    throw new Error('QQNT 原生聊天记录卡片缺少 detail')
  }
  const uuid = randomUUID()
  card.meta.detail.resid = newResId
  card.meta.detail.uniseq = uuid
  let extra = {}
  try {
    extra = typeof card.extra === 'string'
      ? JSON.parse(card.extra)
      : { ...(asObject(card.extra) || {}) }
  } catch {
    extra = {}
  }
  extra.filename = uuid
  extra.tsum = archivedCard && Number(extra.tsum) > 0
    ? Number(extra.tsum)
    : nodeCount
  card.extra = `${JSON.stringify(extra)}\n`
  const finalElement = {
    ...element,
    elementId: '',
    arkElement: {
      ...element.arkElement,
      bytesData: JSON.stringify(card),
    },
  }
  if (asObject(element.multiForwardMsgElement)) {
    finalElement.multiForwardMsgElement = {
      ...element.multiForwardMsgElement,
      resId: newResId,
      resid: newResId,
    }
  }
  const send = state.ctx?.core?.apis?.MsgApi?.sendMsg
  if (typeof send !== 'function') {
    throw new Error('当前 NapCat 未提供原生卡片发送接口')
  }
  return send.call(state.ctx.core.apis.MsgApi, destinationPeer, [finalElement])
}

async function rebuildNativeForwardCard(templateMessage, entries, destinationPeer) {
  const operation = state.ctx?.core?.apis?.PacketApi?.pkt?.operation
  if (!operation) throw new Error('当前 NapCat 未提供原生资源重建接口')
  const { element, card, resId } = await nativeForwardCard(
    templateMessage,
    { chatType: CHAT_TYPE_C2C, peerUid: asText(state.ctx.core.selfInfo.uid), guildId: '' },
  )
  const resolvedEntries = []
  for (const entry of entries) {
    resolvedEntries.push(await resolveNativeForwardIdentity(entry))
  }
  let newResId = ''
  if (typeof operation.sendPacket === 'function') {
    const nativePayload = await downloadNativeForwardProto(operation, resId)
    const patchedPayload = patchNativeForwardProtoPayload(
      nativePayload,
      resolvedEntries,
    )
    newResId = await uploadNativeForwardProto(
      operation,
      patchedPayload,
      destinationPeer,
    )
  } else {
    if (
      typeof operation.FetchForwardMsgRaw !== 'function'
      || typeof operation.UploadForwardMsgV2 !== 'function'
    ) {
      throw new Error('当前 NapCat 未提供原生资源重建接口')
    }
    const actions = await operation.FetchForwardMsgRaw(resId)
    const main = (Array.isArray(actions) ? actions : []).find(
      (action) => action?.actionCommand === 'MultiMsg',
    )
    const bodies = Array.isArray(main?.actionData?.msgBody)
      ? main.actionData.msgBody
      : []
    if (bodies.length !== entries.length) {
      throw new Error(
        `QQNT 原生模板节点数不匹配（${bodies.length}/${entries.length}）`,
      )
    }
    bodies.forEach((body, index) => {
      patchNativeForwardBody(body, resolvedEntries[index])
    })
    const upload = (Array.isArray(actions) ? actions : []).map((action) => ({
      actionCommand: asText(action?.actionCommand),
      actionMsgBody: Array.isArray(action?.actionData?.msgBody)
        ? action.actionData.msgBody
        : [],
    }))
    const destinationGroup = destinationPeer.chatType === CHAT_TYPE_GROUP
      ? Number(destinationPeer.peerUid)
      : 0
    newResId = asText(await operation.UploadForwardMsgV2(
      upload,
      destinationGroup,
    )).trim()
  }
  if (!newResId) throw new Error('QQNT 未返回重建后的聊天记录资源')

  return sendNativeForwardResourceCard(
    element,
    card,
    newResId,
    destinationPeer,
    entries.length,
  )
}

async function replayNativeForwardPayload(
  destinationPeer,
  nativePayload,
  archivedCard,
  uploadPeer = destinationPeer,
) {
  const operation = state.ctx?.core?.apis?.PacketApi?.pkt?.operation
  if (typeof operation?.sendPacket !== 'function') {
    throw new Error('当前 NapCat 未提供原始长消息上传接口')
  }
  const nodeCount = validateNativeForwardProtoPayload(nativePayload)
  const selfUid = asText(state.ctx?.core?.selfInfo?.uid).trim()
  if (!selfUid) throw new Error('无法读取当前 QQ 登录账号')
  const selfPeer = { chatType: CHAT_TYPE_C2C, peerUid: selfUid, guildId: '' }
  const [seedId] = await stageNativeForwardLeaf(
    destinationPeer,
    selfPeer,
    [{ type: 'text', data: { text: '聊天记录' } }],
  )
  const seedEntry = nativeForwardEntry({
    data: {
      nickname: asText(state.ctx?.core?.selfInfo?.nick || 'QQ用户'),
      user_id: asText(state.ctx?.core?.selfInfo?.uin),
      omm_sender_uid: selfUid,
    },
  }, seedId)
  const template = await qqNativeMultiForward(selfPeer, selfPeer, [seedEntry])
  const { element, card } = await nativeForwardCard(template, selfPeer)
  const newResId = await uploadNativeForwardProto(
    operation,
    nativePayload,
    uploadPeer,
  )
  return sendNativeForwardResourceCard(
    element,
    card,
    newResId,
    destinationPeer,
    nodeCount,
    archivedCard,
  )
}

async function qqNativeMultiForward(sourcePeer, destinationPeer, entries) {
  const wrapper = state.ctx?.core?.eventWrapper
  if (typeof wrapper?.callNormalEventV2 !== 'function') {
    throw new Error('当前 NapCat 未提供 QQNT 原生合并转发接口')
  }
  const selfUid = asText(state.ctx?.core?.selfInfo?.uid)
  const msgInfos = entries.map((entry) => ({
    msgId: entry.msgId,
    senderShowName: entry.senderName,
  }))
  const [, msgList] = await wrapper.callNormalEventV2(
    'NodeIKernelMsgService/multiForwardMsgWithComment',
    'NodeIKernelMsgListener/onMsgInfoListUpdate',
    [msgInfos, sourcePeer, destinationPeer, [], new Map()],
    () => true,
    (records) => Array.isArray(records) && records.some((message) => (
      asText(message?.peerUid) === asText(destinationPeer.peerUid)
      && asText(message?.senderUid) === selfUid
    )),
  )
  const message = (Array.isArray(msgList) ? msgList : []).find((item) => (
    asText(item?.peerUid) === asText(destinationPeer.peerUid)
    && asText(item?.senderUid) === selfUid
    && isForwardCardMessage(item)
  ))
  if (!message) throw new Error('QQNT 原生合并转发没有返回可识别的卡片')
  return message
}

async function buildQQNativeForward(destinationPeer, nodes, depth = 0) {
  if (depth > MAX_NATIVE_FORWARD_DEPTH) {
    throw new Error('原生重组嵌套层级超过安全上限')
  }
  const selfUid = asText(state.ctx?.core?.selfInfo?.uid)
  if (!selfUid) throw new Error('无法读取当前 QQ 登录账号')
  const selfPeer = { chatType: CHAT_TYPE_C2C, peerUid: selfUid, guildId: '' }
  const entries = []
  for (const node of nodes) {
    const content = node.data.content
    const children = content.filter((item) => item?.type === 'node')
    if (children.length) {
      const nested = await buildQQNativeForward(selfPeer, children, depth + 1)
      entries.push(nativeForwardEntry(node, nested?.msgId))
      continue
    }
    const msgIds = await stageNativeForwardLeaf(
      destinationPeer,
      selfPeer,
      content,
    )
    entries.push(...msgIds.map((msgId) => nativeForwardEntry(node, msgId)))
  }
  if (!entries.length) throw new Error('原生重组没有生成可转发消息')
  const template = await qqNativeMultiForward(selfPeer, selfPeer, entries)
  return rebuildNativeForwardCard(template, entries, destinationPeer)
}

/** 用 QQNT 内核生成桌面端可识别的合并转发资源。 */
export async function sendQQNativeForward(payload) {
  if (!state.active || !state.ctx) throw new Error('插件尚未启动')
  const peer = await oneBotPeer(
    payload?.destination_type,
    payload?.destination_peer_id,
  )
  validateNativeForwardNodes(payload?.messages)
  const replay = Buffer.isBuffer(payload?.nativePayload)
  const nativeUploadPeer = payload?.nativeSourceType === 'group'
    ? {
        chatType: CHAT_TYPE_GROUP,
        peerUid: payload.nativeSourcePeerId,
        guildId: '',
      }
    : {
        chatType: CHAT_TYPE_C2C,
        peerUid: asText(state.ctx?.core?.selfInfo?.uid),
        guildId: '',
      }
  const message = replay
    ? await replayNativeForwardPayload(
      peer,
      payload.nativePayload,
      payload.nativeCard,
      nativeUploadPeer,
    )
    : await buildQQNativeForward(peer, payload.messages)
  return {
    ok: true,
    message_id: asText(message?.id || message?.msgId),
    strategy: replay ? 'qqnt_native_replay' : 'qqnt_native_rebuild',
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
    forward_card_compat: {
      active: Boolean(state.sendMsgWrapper || state.packetConverterPatches.length),
      outer_hook: Boolean(state.sendMsgWrapper),
      packet_hooks: state.packetConverterPatches.map((patch) => patch.name),
      rewrite_count: state.forwardCardRewriteCount,
    },
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
  state.sourceGroupNames = new Map()
  state.forwardCardRewriteCount = 0

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
  ctx.router.get('/send-native-forward', async (req, res) => {
    try {
      const payload = loadNativeForwardPayload(queryValue(req, 'payload_path'))
      res.json(await sendQQNativeForward(payload))
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      ctx.logger.error('QQNT 原生重组发送失败', error)
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
  installForwardCardCompatibility(ctx)
  ctx.logger.info('OhMyMeme 当前会话桥接已启动（已启用桌面端转发卡片兼容）')
}

export async function plugin_cleanup() {
  // createListenerFunction 会在热重载时复用底层 listener。只移除本模块
  // 自己安装的函数，避免旧闭包在新模块加载后继续处理事件。
  if (state.listener && state.listenerHandlers) {
    for (const [name, handler] of Object.entries(state.listenerHandlers)) {
      if (state.listener[name] === handler) delete state.listener[name]
    }
  }
  removeForwardCardCompatibility()
  state.active = false
  state.current = null
  state.source = ''
  state.updatedAt = 0
  state.mediaDiagnostics = []
  state.mediaUpdatedAt = 0
  state.sourceGroupNames = new Map()
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
  normalizeForwardCardBytes,
  normalizeForwardArkElement,
  normalizeForwardArkElements,
  normalizeForwardCardsInRawMessage,
  encodeProtoVarint,
  protoWireFields,
  protoBytesField,
  protoVarintField,
  firstProtoBytes,
  firstProtoString,
  patchNativeForwardProtoPayload,
  downloadNativeForwardProto,
  uploadNativeForwardProto,
  validateNativeForwardProtoPayload,
  replayNativeForwardPayload,
}
