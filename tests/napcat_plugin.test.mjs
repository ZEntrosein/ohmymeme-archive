import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { deflateSync } from 'node:zlib'

import {
  __test,
  chooseCurrentPeer,
  extractPeers,
  plugin_cleanup,
  plugin_init,
} from '../napcat-plugin-ohmymeme-current-peer/index.mjs'

test('extracts an explicitly selected group peer', () => {
  const payload = {
    tabs: [
      { chatType: 2, peerUid: '123456', groupName: '测试群', isCurrent: true },
      { chatType: 1, peerUid: 'u_private', peerName: '其他会话' },
    ],
  }
  const candidates = extractPeers(payload)
  assert.equal(candidates[0].chatType, 2)
  assert.equal(candidates[0].peer, '123456')
  assert.equal(chooseCurrentPeer(payload).peer, '123456')
})

test('extracts input status peer UIN', () => {
  const candidate = chooseCurrentPeer(
    { chatType: 1, toUin: '456789', statusText: '正在输入' },
    true,
  )
  assert.equal(candidate.chatType, 1)
  assert.equal(candidate.userUin, '456789')
})

test('does not guess among unmarked tab lists', () => {
  const payload = {
    tabs: [
      { chatType: 2, peerUid: '123456' },
      { chatType: 1, peerUid: '654321' },
    ],
  }
  assert.equal(chooseCurrentPeer(payload, false), null)
})

test('accepts one unique unmarked message-box peer', () => {
  const payload = [
    { chatType: 1, peerUid: 'u_private', peerName: '当前备注' },
  ]
  const candidate = chooseCurrentPeer(payload, true)
  assert.equal(candidate.chatType, 1)
  assert.equal(candidate.peer, 'u_private')
})

test('plugin route exposes the converted current target', async () => {
  const routes = new Map()
  const listener = {}
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      eventWrapper: {
        createListenerFunction() { return listener },
      },
      apis: {
        UserApi: {
          async getUinByUidV2(uid) { return uid === 'u_private' ? '456789' : '' },
        },
      },
    },
  }

  await plugin_init(ctx)
  listener.onUserTabStatusChanged({
    currentTab: { chatType: 1, peerUid: 'u_private', peerName: '备注名', isCurrent: true },
  })
  const response = {}
  await routes.get('/current-peer')({}, {
    json(value) { response.value = value },
    status() { return this },
  })

  assert.equal(response.value.available, true)
  assert.deepEqual(response.value.target, {
    message_type: 'private',
    peer_id: '456789',
    label: '备注名',
  })
  await plugin_cleanup()
})

test('plugin route converts an internal group uid when no group code is present', async () => {
  const routes = new Map()
  const listener = {}
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      eventWrapper: { createListenerFunction() { return listener } },
      apis: {
        UserApi: {
          async getUinByUidV2(uid) { return uid === 'g_internal' ? '789012' : '' },
        },
      },
    },
  }

  await plugin_init(ctx)
  listener.onUserTabStatusChanged({
    currentTab: { chatType: 2, peerUid: 'g_internal', groupName: '测试群', isCurrent: true },
  })
  const response = {}
  await routes.get('/current-peer')({}, {
    json(value) { response.value = value },
    status() { return this },
  })

  assert.deepEqual(response.value.target, {
    message_type: 'group',
    peer_id: '789012',
    label: '测试群',
  })
  await plugin_cleanup()
})

test('an unparseable tab switch invalidates the previous target', async () => {
  const routes = new Map()
  const listener = {}
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      eventWrapper: { createListenerFunction() { return listener } },
      apis: { UserApi: {} },
    },
  }
  await plugin_init(ctx)
  listener.onUserTabStatusChanged({
    currentTab: { chatType: 2, peerUid: '123456', isCurrent: true },
  })
  listener.onUserTabStatusChanged({ tabs: [{ status: 'selected-without-peer' }] })

  const response = {}
  await routes.get('/current-peer')({}, {
    json(value) { response.value = value },
    status() { return this },
  })
  assert.equal(response.value.available, false)
  assert.match(response.value.error, /切换事件确认/)
  await plugin_cleanup()
})

test('background message updates cannot overwrite the active target', async () => {
  const routes = new Map()
  const listener = {}
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      eventWrapper: { createListenerFunction() { return listener } },
      apis: { UserApi: {} },
    },
  }
  await plugin_init(ctx)
  listener.onUserTabStatusChanged({
    currentTab: { chatType: 2, peerUid: '123456', isCurrent: true },
  })
  assert.equal(listener.onMsgInfoListUpdate, undefined)
  assert.equal(listener.onMsgInfoListAdd, undefined)
  assert.equal(listener.onInputStatusPush, undefined)

  const response = {}
  await routes.get('/current-peer')({}, {
    json(value) { response.value = value },
    status() { return this },
  })
  assert.equal(response.value.confidence, 'active_tab')
  assert.equal(response.value.target.peer_id, '123456')
  await plugin_cleanup()
})

test('a unique message-box peer is accepted without a separate tab event', async () => {
  const routes = new Map()
  const listener = {}
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      eventWrapper: { createListenerFunction() { return listener } },
      apis: { UserApi: {} },
    },
  }
  await plugin_init(ctx)
  listener.onMsgBoxChanged([{ chatType: 2, peerUid: '777888' }])

  const response = {}
  await routes.get('/current-peer')({}, {
    json(value) { response.value = value },
    status() { return this },
  })
  assert.equal(response.value.available, true)
  assert.equal(response.value.target.peer_id, '777888')
  await plugin_cleanup()
})

test('plugin cleanup detaches only its own native listener callbacks', async () => {
  const listener = { unrelatedCallback() {} }
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get() {} },
    core: {
      eventWrapper: { createListenerFunction() { return listener } },
      apis: { UserApi: {} },
    },
  }
  await plugin_init(ctx)
  assert.equal(typeof listener.onUserTabStatusChanged, 'function')
  assert.equal(typeof listener.onMsgBoxChanged, 'function')

  await plugin_cleanup()
  assert.equal(listener.onUserTabStatusChanged, undefined)
  assert.equal(listener.onMsgBoxChanged, undefined)
  assert.equal(typeof listener.unrelatedCallback, 'function')
})

test('debug state never exposes raw event payloads', () => {
  const value = __test.debugResponse()
  assert.equal(Object.prototype.hasOwnProperty.call(value, 'raw'), false)
})

test('native Electron window handles are decoded as little-endian integers', () => {
  assert.equal(
    __test.nativeHandleText(Uint8Array.from([0x78, 0x56, 0x34, 0x12])),
    String(0x12345678),
  )
})

test('plugin restores a native forward from persisted peer and real sequence', async () => {
  const routes = new Map()
  const listener = {}
  const calls = []
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      eventWrapper: { createListenerFunction() { return listener } },
      apis: {
        UserApi: {
          async getUidByUinV2(uin) { return uin === '456789' ? 'u_private' : '' },
        },
        MsgApi: {
          async getMsgsBySeqAndCount(peer, seq, count, desc, reverse) {
            calls.push(['lookup', peer, seq, count, desc, reverse])
            return {
              msgList: [{
                msgSeq: '7788',
                msgId: 'raw-message-id',
                msgTime: '1710000000',
                senderUin: '111222',
                elements: [{ multiForwardMsgElement: { resId: 'forward-resource' } }],
              }],
            }
          },
          async forwardMsg(source, destination, ids) {
            calls.push(['forward', source, destination, ids])
            return { result: 0, errMsg: '' }
          },
        },
      },
    },
  }

  await plugin_init(ctx)
  const response = {}
  await routes.get('/forward-message')({
    query: {
      source_type: 'group',
      source_peer_id: '123456',
      source_real_seq: '7788',
      source_time: '1710000000',
      source_sender_id: '111222',
      destination_type: 'private',
      destination_peer_id: '456789',
    },
  }, {
    json(value) { response.value = value },
  })

  assert.equal(response.value.ok, true)
  assert.deepEqual(calls[0], [
    'lookup',
    { chatType: 2, peerUid: '123456', guildId: '' },
    '7788',
    1,
    true,
    true,
  ])
  assert.deepEqual(calls[1], [
    'forward',
    { chatType: 2, peerUid: '123456', guildId: '' },
    { chatType: 1, peerUid: 'u_private', guildId: '' },
    ['raw-message-id'],
  ])
  await plugin_cleanup()
})

test('plugin refuses to forward a non-forward message found at the sequence', async () => {
  const routes = new Map()
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      eventWrapper: { createListenerFunction() { return {} } },
      apis: {
        UserApi: { async getUidByUinV2() { return '' } },
        MsgApi: {
          async getMsgsBySeqAndCount() {
            return { msgList: [{ msgSeq: '12', msgId: 'plain', elements: [] }] }
          },
          async forwardMsg() { throw new Error('must not forward') },
        },
      },
    },
  }

  await plugin_init(ctx)
  const response = {}
  await routes.get('/forward-message')({
    query: {
      source_type: 'group', source_peer_id: '123', source_real_seq: '12',
      destination_type: 'group', destination_peer_id: '456',
    },
  }, {
    json(value) { response.value = value },
  })
  assert.equal(response.value.ok, false)
  assert.match(response.value.error, /不是合并转发卡片/)
  await plugin_cleanup()
})

test('plugin recovers an empty nested node from persisted source identity', async () => {
  const routes = new Map()
  const raw = {
    msgSeq: '7788',
    msgId: 'raw-nested-id',
    msgTime: '1710000000',
    senderUin: '111222',
    elements: [{ multiForwardMsgElement: { resId: 'nested-resource' } }],
  }
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      eventWrapper: { createListenerFunction() { return {} } },
      apis: {
        UserApi: {},
        MsgApi: {
          async getMsgsBySeqAndCount() { return { msgList: [raw] } },
        },
      },
    },
    oneBot: {
      apis: {
        MsgApi: {
          async parseMessage(message, format, parseMultMsg) {
            assert.equal(message, raw)
            assert.equal(format, 'array')
            assert.equal(parseMultMsg, true)
            return {
              message: [{ type: 'forward', data: { content: [] } }],
            }
          },
        },
      },
    },
  }

  await plugin_init(ctx)
  const response = {}
  await routes.get('/recover-message')({
    query: {
      source_type: 'group',
      source_peer_id: '123456',
      source_real_seq: '7788',
      source_time: '1710000000',
      source_sender_id: '111222',
    },
  }, {
    json(value) { response.value = value },
  })
  assert.equal(response.value.ok, true)
  assert.equal(response.value.message.message[0].type, 'forward')
  assert.equal(raw.id, 'raw-nested-id')
  await plugin_cleanup()
})

test('plugin restores nested forward actions from one long-message resource', async () => {
  const routes = new Map()
  const xml = '<msg m_resid="nested-res" m_fileName="child-action" />'
  const richData = Buffer.concat([
    Buffer.from([1]),
    deflateSync(Buffer.from(xml, 'utf8')),
  ])
  const packetNode = (elements, sequence) => ({
    responseHead: {
      fromUin: 111,
      fromUid: 'uid-111',
      grp: { groupUin: 222, memberName: 'Alice' },
    },
    contentHead: { sequence, timeStamp: 1710000000 },
    body: { richText: { elems: elements } },
  })
  const actions = [
    {
      actionCommand: 'MultiMsg',
      actionData: {
        msgBody: [packetNode([{ richMsg: { template1: richData, serviceId: 35 } }], 1)],
      },
    },
    {
      actionCommand: 'child-action',
      actionData: {
        msgBody: [packetNode([{ text: { str: 'inside' } }], 2)],
      },
    },
  ]
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      eventWrapper: { createListenerFunction() { return {} } },
      apis: {
        PacketApi: {
          pkt: {
            operation: { async FetchForwardMsgRaw() { return actions } },
            msgConverter: {
              packetMsgToRaw(elements) {
                return elements.flatMap((element) => element.text
                  ? [[{
                    textElement: { content: element.text.str, atType: 0 },
                    elementType: 1,
                    elementId: '',
                  }, element]]
                  : [])
              },
            },
          },
        },
      },
    },
    oneBot: {
      apis: {
        MsgApi: {
          async parseMessage(raw) {
            return {
              sender: { nickname: raw.sendNickName },
              message: raw.elements.map((element) => ({
                type: 'text', data: { text: element.textElement.content },
              })),
            }
          },
        },
      },
    },
  }

  await plugin_init(ctx)
  const response = {}
  await routes.get('/recover-forward-resource')({
    query: { res_id: 'outer-resource' },
  }, {
    json(value) { response.value = value },
  })

  assert.equal(response.value.ok, true)
  assert.equal(response.value.action_count, 2)
  const forward = response.value.messages[0].message[0]
  assert.equal(forward.type, 'forward')
  assert.equal(forward.data.content[0].message[0].data.text, 'inside')
  await plugin_cleanup()
})

test('plugin exposes an existing QQ image cache path without network metadata', async () => {
  const temp = mkdtempSync(join(tmpdir(), 'omm-napcat-cache-'))
  const cachedImage = join(temp, 'cached.jpg')
  writeFileSync(cachedImage, Buffer.from('image'))
  const routes = new Map()
  const packetImage = { legacyImageWithoutCommonElem: true }
  const actions = [{
    actionCommand: 'MultiMsg',
    actionData: {
      msgBody: [{
        responseHead: {
          fromUin: 111,
          fromUid: 'uid-111',
          grp: { groupUin: 222, memberName: 'Alice' },
        },
        contentHead: { sequence: 1, timeStamp: 1710000000 },
        body: { richText: { elems: [packetImage] } },
      }],
    },
  }]
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      context: {
        session: {
          getMsgService() {
            return { getRichMediaFilePathForGuild() { return cachedImage } }
          },
        },
      },
      eventWrapper: { createListenerFunction() { return {} } },
      apis: {
        PacketApi: {
          pkt: {
            operation: { async FetchForwardMsgRaw() { return actions } },
            msgConverter: {
              packetMsgToRaw() {
                return [[{
                  picElement: {
                    md5HexStr: 'abc',
                    fileName: 'abc',
                    picSubType: 0,
                    fileUuid: '',
                  },
                  elementType: 2,
                  elementId: 'image-1',
                }, packetImage]]
              },
            },
          },
        },
      },
    },
    oneBot: {
      apis: {
        MsgApi: {
          async parseMessage() {
            return { message: [{ type: 'image', data: { file: 'image-id' } }] }
          },
        },
      },
    },
  }

  try {
    await plugin_init(ctx)
    const response = {}
    await routes.get('/recover-forward-resource')({
      query: { res_id: 'outer-resource' },
    }, {
      json(value) { response.value = value },
    })

    assert.equal(response.value.ok, true)
    assert.equal(response.value.messages[0].message[0].data.path, cachedImage)
    assert.equal(response.value.media_diagnostics[0].cache_hit, true)
    assert.equal(response.value.media_diagnostics[0].result, 'local')
  } finally {
    await plugin_cleanup()
    rmSync(temp, { recursive: true, force: true })
  }
})

test('plugin uses QCE-style native forward messages to localize packet images', async () => {
  const temp = mkdtempSync(join(tmpdir(), 'omm-napcat-native-forward-'))
  const cachedImage = join(temp, 'native.jpg')
  writeFileSync(cachedImage, Buffer.from('image'))
  const routes = new Map()
  const packetImage = { packetImage: true }
  const actions = [{
    actionCommand: 'MultiMsg',
    actionData: {
      msgBody: [{
        responseHead: {
          fromUin: 111,
          fromUid: 'uid-111',
          grp: { groupUin: 222, memberName: 'Alice' },
        },
        contentHead: { sequence: 1, timeStamp: 1710000000 },
        body: { richText: { elems: [packetImage] } },
      }],
    },
  }]
  const outer = {
    msgId: 'native-outer',
    msgSeq: '77',
    msgTime: '1710000001',
    senderUin: '111',
    elements: [{
      elementId: 'forward-card',
      multiForwardMsgElement: { resId: 'outer-resource' },
    }],
  }
  const nativeInner = {
    msgId: 'native-inner',
    msgSeq: '1',
    chatType: 2,
    peerUid: '222',
    elements: [{
      elementId: 'native-image',
      elementType: 2,
      picElement: {
        fileName: 'same-md5',
        md5HexStr: 'same-md5',
        sourcePath: '',
      },
    }],
  }
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      eventWrapper: { createListenerFunction() { return {} } },
      apis: {
        MsgApi: {
          async getMsgsBySeqAndCount() { return { msgList: [outer] } },
          async getMultiMsg() { return { msgList: [nativeInner] } },
        },
        FileApi: {
          async downloadMedia(...args) {
            assert.equal(args[0], 'native-inner')
            assert.equal(args[3], 'native-image')
            return cachedImage
          },
        },
        PacketApi: {
          pkt: {
            operation: { async FetchForwardMsgRaw() { return actions } },
            msgConverter: {
              packetMsgToRaw() {
                return [[{
                  picElement: {
                    md5HexStr: 'same-md5',
                    fileName: 'same-md5',
                    picSubType: 0,
                  },
                  elementType: 2,
                  elementId: '',
                }, packetImage]]
              },
            },
          },
        },
      },
    },
    oneBot: {
      apis: {
        MsgApi: {
          async parseMessage() {
            return { message: [{ type: 'image', data: { file: 'image-id' } }] }
          },
        },
      },
    },
  }

  try {
    await plugin_init(ctx)
    const response = {}
    await routes.get('/recover-forward-resource')({
      query: {
        res_id: 'outer-resource',
        source_type: 'group',
        source_peer_id: '222',
        source_real_seq: '77',
        source_time: '1710000001',
        source_sender_id: '111',
      },
    }, {
      json(value) { response.value = value },
    })

    assert.equal(response.value.ok, true)
    assert.equal(response.value.native_tree_attempted, true)
    assert.equal(response.value.native_tree_image_count, 1)
    assert.equal(response.value.native_tree_localized_count, 1)
    assert.equal(response.value.messages[0].message[0].data.path, cachedImage)
    assert.equal(response.value.media_diagnostics[0].native_tree_match, true)
  } finally {
    await plugin_cleanup()
    rmSync(temp, { recursive: true, force: true })
  }
})

test('plugin carries native forward video paths into recovered segments', async () => {
  const temp = mkdtempSync(join(tmpdir(), 'omm-napcat-native-video-'))
  const cachedVideo = join(temp, 'native.mp4')
  writeFileSync(cachedVideo, Buffer.from('video'))
  const routes = new Map()
  const packetVideo = { packetVideo: true }
  const actions = [{
    actionCommand: 'MultiMsg',
    actionData: {
      msgBody: [{
        responseHead: {
          fromUin: 111,
          fromUid: 'uid-111',
          grp: { groupUin: 222, memberName: 'Alice' },
        },
        contentHead: { sequence: 1, timeStamp: 1710000000 },
        body: { richText: { elems: [packetVideo] } },
      }],
    },
  }]
  const outer = {
    msgId: 'native-outer',
    msgSeq: '77',
    msgTime: '1710000001',
    senderUin: '111',
    elements: [{
      elementId: 'forward-card',
      multiForwardMsgElement: { resId: 'outer-resource' },
    }],
  }
  const nativeInner = {
    msgId: 'native-inner',
    msgSeq: '1',
    chatType: 2,
    peerUid: '222',
    elements: [{
      elementId: 'native-video',
      elementType: 5,
      videoElement: { filePath: cachedVideo, fileUuid: 'video-uuid' },
    }],
  }
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      eventWrapper: { createListenerFunction() { return {} } },
      apis: {
        MsgApi: {
          async getMsgsBySeqAndCount() { return { msgList: [outer] } },
          async getMultiMsg() { return { msgList: [nativeInner] } },
        },
        FileApi: {},
        PacketApi: {
          pkt: {
            operation: { async FetchForwardMsgRaw() { return actions } },
            msgConverter: {
              packetMsgToRaw() {
                return [[{
                  videoElement: { fileUuid: 'video-uuid' },
                  elementType: 5,
                  elementId: '',
                }, packetVideo]]
              },
            },
          },
        },
      },
    },
    oneBot: {
      apis: {
        MsgApi: {
          async parseMessage() {
            return {
              message: [{
                type: 'video',
                data: { file: 'video-id', url: 'https://old.invalid/video' },
              }],
            }
          },
        },
      },
    },
  }

  try {
    await plugin_init(ctx)
    const response = {}
    await routes.get('/recover-forward-resource')({
      query: {
        res_id: 'outer-resource',
        source_type: 'group',
        source_peer_id: '222',
        source_real_seq: '77',
        source_time: '1710000001',
        source_sender_id: '111',
      },
    }, {
      json(value) { response.value = value },
    })

    assert.equal(response.value.ok, true)
    assert.equal(response.value.native_tree_media_count, 1)
    assert.equal(response.value.native_tree_resolved_count, 1)
    assert.equal(response.value.messages[0].message[0].data.path, cachedVideo)
  } finally {
    await plugin_cleanup()
    rmSync(temp, { recursive: true, force: true })
  }
})

test('official QQ media downloader rejects other hosts and validates image bytes', async () => {
  const jpeg = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3, 4])
  let calls = 0
  const fetchImpl = async () => {
    calls += 1
    let delivered = false
    return {
      ok: true,
      status: 200,
      headers: {
        get(name) {
          if (name === 'content-length') return String(jpeg.length)
          if (name === 'content-type') return 'image/jpeg'
          return null
        },
      },
      body: {
        getReader() {
          return {
            async read() {
              if (delivered) return { done: true, value: undefined }
              delivered = true
              return { done: false, value: jpeg }
            },
            async cancel() {},
          }
        },
      },
    }
  }

  const result = await __test.fetchOfficialQQImage([
    'http://127.0.0.1/private.jpg',
    'https://example.com/untrusted.jpg',
    'https://multimedia.nt.qq.com.cn/download?fileid=test',
  ], { fetchImpl })

  assert.deepEqual(result, jpeg)
  assert.equal(calls, 1)
  assert.equal(__test.isOfficialQQMediaUrl('https://gchat.qpic.cn/path'), true)
  assert.equal(__test.isOfficialQQMediaUrl('https://qq.com.example.org/path'), false)
})

test('plugin media diagnostics redact URLs, paths, and opaque identifiers', () => {
  const result = __test.safeDiagnosticError(new Error(
    'bad C:\\QQ\\cache\\secret.jpg https://example.com/private '
      + 'Sovtw67Zf4wqy0wh4xnWEDmEPFroAvr+65IIlXxLo3cGJ40nzIYz0yoNGIsKpNVr',
  ))
  assert.equal(result.includes('secret.jpg'), false)
  assert.equal(result.includes('example.com'), false)
  assert.equal(result.includes('Sovtw67'), false)
})

test('plugin can use the native message and element ids to export an image', async () => {
  const temp = mkdtempSync(join(tmpdir(), 'omm-native-image-'))
  const downloaded = join(temp, 'native.jpg')
  writeFileSync(downloaded, Buffer.from('image'))
  let received = null
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get() {} },
    core: {
      eventWrapper: { createListenerFunction() { return {} } },
      apis: {
        FileApi: {
          async downloadMedia(...args) {
            received = args
            return downloaded
          },
        },
      },
    },
  }

  try {
    await plugin_init(ctx)
    const diagnostic = {}
    const result = await __test.downloadQQImageByMessage(
      { msgId: 1234567890123456789n, chatType: 2, peerUid: '222' },
      { elementId: 'image-element', picElement: { sourcePath: '' } },
      20000,
      diagnostic,
    )

    assert.equal(result, downloaded)
    assert.deepEqual(received.slice(0, 4), [
      '1234567890123456789',
      2,
      '222',
      'image-element',
    ])
    assert.equal(diagnostic.native_attempted, true)
    assert.equal(diagnostic.native_result, 'ok')
  } finally {
    await plugin_cleanup()
    rmSync(temp, { recursive: true, force: true })
  }
})

test('plugin can download a packet image through the visit media API', async () => {
  const temp = mkdtempSync(join(tmpdir(), 'omm-visit-image-'))
  const target = join(temp, 'visit.jpg')
  let received = null
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get() {} },
    core: {
      context: {
        session: {
          getRichMediaService() {
            return {
              async downloadRichMediaInVisit(request) {
                received = request
                writeFileSync(target, Buffer.from('image'))
                return { result: 0 }
              },
            }
          },
        },
      },
      eventWrapper: { createListenerFunction() { return {} } },
      apis: {},
    },
  }

  try {
    await plugin_init(ctx)
    const diagnostic = {}
    const element = { elementType: 2, picElement: { fileUuid: 'packet-uuid' } }
    const result = await __test.downloadQQImageInVisit(
      {
        msgId: '',
        msgRandom: '3',
        msgSeq: '4',
        msgTime: '5',
        chatType: 2,
        senderUid: 'sender',
        peerUid: '222',
      },
      element,
      target,
      1000,
      diagnostic,
    )

    assert.equal(result, target)
    assert.equal(received.ele, element)
    assert.equal(received.peerUid, '222')
    assert.equal(received.msgSeq, '4')
    assert.equal(received.downloadType, 1)
    assert.equal(diagnostic.visit_attempted, true)
    assert.equal(diagnostic.visit_result, 'ok')
  } finally {
    await plugin_cleanup()
    rmSync(temp, { recursive: true, force: true })
  }
})

test('plugin supports the older nested getReq visit media shape', async () => {
  const temp = mkdtempSync(join(tmpdir(), 'omm-legacy-visit-image-'))
  const target = join(temp, 'visit.jpg')
  let received = null
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get() {} },
    core: {
      context: {
        session: {
          getRichMediaService() {
            return {
              async downloadRichMediaInVisit(request) {
                if (!request.getReq) throw new TypeError('getReq is required')
                received = request
                writeFileSync(target, Buffer.from('image'))
              },
            }
          },
        },
      },
      eventWrapper: { createListenerFunction() { return {} } },
      apis: {},
    },
  }

  try {
    await plugin_init(ctx)
    const diagnostic = {}
    const result = await __test.downloadQQImageInVisit(
      { chatType: 2, peerUid: '222', msgSeq: '4', msgTime: '5' },
      { elementType: 2, elementId: '', picElement: { fileUuid: 'uuid' } },
      target,
      1000,
      diagnostic,
    )
    assert.equal(result, target)
    assert.equal(received.getReq.peerUid, '222')
    assert.equal(received.downSourceType, 0)
    assert.equal(received.fileModelId, '0')
    assert.equal(diagnostic.visit_variant, 'nested_get_req')
    assert.equal(diagnostic.visit_attempts[0].result, 'error')
    assert.equal(diagnostic.visit_attempts[1].result, 'ok')
  } finally {
    await plugin_cleanup()
    rmSync(temp, { recursive: true, force: true })
  }
})

test('plugin can download a packet image through its QQ file uuid', async () => {
  const temp = mkdtempSync(join(tmpdir(), 'omm-uuid-image-'))
  const target = join(temp, 'uuid.jpg')
  let received = null
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get() {} },
    core: {
      context: {
        session: {
          getRichMediaService() {
            return {
              async downloadFileForFileUuid(...args) {
                received = args
                writeFileSync(target, Buffer.from('image'))
                return { data: { filePath: target } }
              },
            }
          },
        },
      },
      eventWrapper: { createListenerFunction() { return {} } },
      apis: {},
    },
  }

  try {
    await plugin_init(ctx)
    const diagnostic = {}
    const result = await __test.downloadQQImageByUuid(
      { chatType: 2, peerUid: '222' },
      {
        elementType: 2,
        picElement: { fileUuid: '', md5HexStr: 'abc', picSubType: 0 },
      },
      {
        index: {
          fileUuid: 'packet-file-uuid',
          info: {
            fileName: 'packet.jpg',
            fileSize: 5,
            fileHash: 'abc',
            fileSha1: 'def',
          },
        },
      },
      target,
      1000,
      diagnostic,
    )

    assert.equal(result, target)
    assert.deepEqual(received[0], { chatType: 2, peerUid: '222', guildId: '' })
    assert.equal(received[1], 'packet-file-uuid')
    assert.deepEqual(received[2], [{
      fileId: 'packet-file-uuid',
      fileName: 'packet.jpg',
      fileSize: '5',
      fileModelId: '0',
    }])
    assert.equal(diagnostic.uuid_attempted, true)
    assert.equal(diagnostic.uuid_result, 'ok')
  } finally {
    await plugin_cleanup()
    rmSync(temp, { recursive: true, force: true })
  }
})

test('media capability inspection exposes method names but no service data', async () => {
  class MessageService {
    downloadRichMedia() {}
    getRichMediaFilePathForGuild() {}
    unrelated() {}
  }
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get() {} },
    core: {
      context: {
        session: {
          getMsgService() { return new MessageService() },
          getRichMediaService() { return { saveMediaFile() {} } },
        },
      },
      eventWrapper: { createListenerFunction() { return {} } },
      apis: { FileApi: { downloadMedia() {}, secret: 'not returned' } },
    },
  }

  try {
    await plugin_init(ctx)
    const result = __test.mediaCapabilitiesResponse()
    assert.deepEqual(result.msg_service, [
      'downloadRichMedia',
      'getRichMediaFilePathForGuild',
    ])
    assert.deepEqual(result.rich_media_service, ['saveMediaFile'])
    assert.deepEqual(result.file_api, ['downloadMedia'])
    assert.equal(JSON.stringify(result).includes('not returned'), false)
  } finally {
    await plugin_cleanup()
  }
})
