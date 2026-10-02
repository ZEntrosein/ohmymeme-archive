import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { deflateSync, gzipSync, gunzipSync } from 'node:zlib'

import {
  __test,
  chooseCurrentPeer,
  extractPeers,
  plugin_cleanup,
  plugin_init,
} from '../napcat-plugin-ohmymeme-current-peer/index.mjs'

function fakeForwardArk(extra = { filename: 'test-forward', tsum: 2 }) {
  return {
    elementType: 10,
    elementId: '',
    arkElement: {
      bytesData: JSON.stringify({
        app: 'com.tencent.multimsg',
        config: { forward: 1, type: 'normal' },
        extra,
        meta: { detail: { resid: 'test-resource', uniseq: 'test-forward' } },
        ver: '0.0.0.5',
        view: 'contact',
      }),
    },
  }
}

test('native forward captures only the delivered destination copy', async () => {
  const previous = { ctx: __test.state.ctx, active: __test.state.active }
  __test.state.active = true
  const sourcePeer = { chatType: 2, peerUid: '456', guildId: '' }
  const destinationPeer = { chatType: 2, peerUid: '789', guildId: '' }
  const source = { msgId: 'original', elements: [fakeForwardArk()] }
  const sent = {
    msgId: '9876543210', msgSeq: '200', chatType: 2, peerUid: '789',
    senderUid: 'u_self', senderUin: '123', msgTime: String(Math.floor(Date.now() / 1000)),
    sendStatus: 2, elements: [fakeForwardArk()],
  }
  let calls = 0
  __test.state.ctx = { core: { selfInfo: { uid: 'u_self', uin: '123' }, apis: {
    MsgApi: { forwardMsg: async (...args) => {
      calls += 1
      assert.deepEqual(args, [sourcePeer, destinationPeer, ['original']])
      __test.recordMessagePeers([
        { ...sent, msgId: 'wrong-peer', peerUid: '456' },
        { ...sent, msgId: 'wrong-sender', senderUid: 'u_other' },
        { ...sent, msgId: 'still-sending', sendStatus: 1 },
        { ...sent, msgId: 'old-message', msgTime: '1710000000' },
        sent,
      ])
      return { result: 0 }
    } },
  } } }
  try {
    const output = await __test.forwardNativeWithReceipt(sourcePeer, destinationPeer, source)
    assert.equal(output, sent)
    assert.deepEqual(__test.nativeMessageLocation(output, 'group', '789'), {
      self_id: '123', message_id: '9876543210', message_type: 'group',
      group_id: '789', real_seq: '200', time: sent.msgTime,
      sender: { user_id: '123' },
    })
    assert.equal(calls, 1)
    assert.equal(__test.state.forwardReceipts.size, 0)
    assert.equal(__test.nativeMessageLocation({ ...sent, sendStatus: 1 }, 'group', '789'), null)
    assert.equal(__test.nativeMessageLocation({ ...sent, msgSeq: '0' }, 'group', '789'), null)
  } finally {
    Object.assign(__test.state, previous)
    __test.state.messagePeers.clear()
  }
})

test('ambiguous destination receipts do not invent a backup position', async () => {
  const previous = { ctx: __test.state.ctx, active: __test.state.active }
  __test.state.active = true
  const source = { msgId: 'original', elements: [fakeForwardArk()] }
  const sent = {
    msgId: '100', msgSeq: '200', chatType: 2, peerUid: '789',
    senderUid: 'u_self', msgTime: String(Math.floor(Date.now() / 1000)),
    sendStatus: 2, elements: [fakeForwardArk()],
  }
  let calls = 0
  __test.state.ctx = { core: { selfInfo: { uid: 'u_self', uin: '123' }, apis: {
    MsgApi: { forwardMsg: async () => {
      calls += 1
      __test.recordMessagePeers([sent, { ...sent, msgId: '101', msgSeq: '201' }])
      return { result: 0 }
    } },
  } } }
  try {
    const output = await __test.forwardNativeWithReceipt(
      { chatType: 2, peerUid: '456' }, { chatType: 2, peerUid: '789' }, source,
    )
    assert.equal(output, null)
    assert.equal(calls, 1)
    assert.equal(__test.state.forwardReceipts.size, 0)
  } finally {
    Object.assign(__test.state, previous)
    __test.state.messagePeers.clear()
  }
})

test('accepted forward without a receipt is not resent', async () => {
  const previous = __test.state.ctx
  let calls = 0
  __test.state.ctx = { core: { selfInfo: { uid: 'u_self', uin: '123' }, apis: {
    MsgApi: { forwardMsg: async () => { calls += 1; return { result: 0 } } },
  } } }
  try {
    assert.equal(await __test.forwardNativeWithReceipt(
      { chatType: 2, peerUid: '456' }, { chatType: 2, peerUid: '789' },
      { msgId: 'original', elements: [fakeForwardArk()] },
    ), null)
    assert.equal(calls, 1)
    assert.equal(__test.state.forwardReceipts.size, 0)
  } finally {
    __test.state.ctx = previous
  }
})

test('forward dispatch failures are marked unsafe to resend', async () => {
  const previous = __test.state.ctx
  __test.state.ctx = { core: { selfInfo: { uid: 'u_self', uin: '123' }, apis: {
    MsgApi: { forwardMsg: async () => { throw new Error('transport lost') } },
  } } }
  try {
    await assert.rejects(__test.forwardNativeWithReceipt(
      { chatType: 2, peerUid: '456' }, { chatType: 2, peerUid: '789' },
      { msgId: 'original', elements: [fakeForwardArk()] },
    ), (error) => error.sendStarted === true)
    assert.equal(__test.state.forwardReceipts.size, 0)
  } finally {
    __test.state.ctx = previous
  }
})

test('normalizes NapCat forward card extra to the native QQNT string form', () => {
  const element = fakeForwardArk()
  const normalized = __test.normalizeForwardArkElement(element)

  assert.equal(normalized.changed, true)
  assert.notEqual(normalized.value, element)
  const card = JSON.parse(normalized.value.arkElement.bytesData)
  assert.equal(typeof card.extra, 'string')
  assert.equal(card.extra.endsWith('\n'), true)
  assert.deepEqual(JSON.parse(card.extra), { filename: 'test-forward', tsum: 2 })
  assert.equal(typeof JSON.parse(element.arkElement.bytesData).extra, 'object')
})

test('leaves native forward cards and unrelated ARK cards unchanged', () => {
  const native = fakeForwardArk('{"filename":"native","tsum":1}\n')
  const unrelated = {
    arkElement: { bytesData: JSON.stringify({ app: 'com.tencent.music.lua', extra: {} }) },
  }

  assert.equal(__test.normalizeForwardArkElement(native).changed, false)
  assert.equal(__test.normalizeForwardArkElement(unrelated).changed, false)
})

test('plugin normalizes outer and nested packet forward cards and restores hooks', async () => {
  const sent = []
  const converted = []
  const listener = {}
  const msgApi = {
    async sendMsg(peer, elements) {
      sent.push({ peer, elements })
      return { msgId: 'sent-message' }
    },
  }
  const converter = {
    rawMsgWithSendMsgToPacketMsg(raw) {
      converted.push(raw)
      return { converted: true }
    },
    rawMsgToPacketMsg(raw) {
      converted.push(raw)
      return { converted: true }
    },
  }
  const originalSendMsg = msgApi.sendMsg
  const originalRawConvert = converter.rawMsgWithSendMsgToPacketMsg
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get() {} },
    core: {
      eventWrapper: { createListenerFunction() { return listener } },
      apis: {
        MsgApi: msgApi,
        PacketApi: { pkt: { msgConverter: converter } },
        UserApi: {},
      },
    },
  }

  await plugin_init(ctx)
  try {
    await msgApi.sendMsg({ chatType: 2, peerUid: 'group' }, [fakeForwardArk()])
    converter.rawMsgWithSendMsgToPacketMsg({ msg: [fakeForwardArk()] })

    const outer = JSON.parse(sent[0].elements[0].arkElement.bytesData)
    const nested = JSON.parse(converted[0].msg[0].arkElement.bytesData)
    assert.equal(typeof outer.extra, 'string')
    assert.equal(typeof nested.extra, 'string')
    assert.equal(__test.state.forwardCardRewriteCount, 2)
  } finally {
    await plugin_cleanup()
  }
  assert.equal(msgApi.sendMsg, originalSendMsg)
  assert.equal(converter.rawMsgWithSendMsgToPacketMsg, originalRawConvert)
})

test('raw native forward patch preserves unknown protobuf fields and native flags', () => {
  const b = __test.protoBytesField
  const v = __test.protoVarintField
  const text = (field, value) => b(field, Buffer.from(value, 'utf8'))
  const responseUnknown = text(99, 'opaque-response')
  const contentUnknown = text(77, 'opaque-content')
  const bodyUnknown = text(55, 'opaque-body')
  const actionUnknown = text(88, 'opaque-action')
  const payloadUnknown = text(90, 'opaque-payload')
  const response = Buffer.concat([
    v(1, 10000),
    text(2, 'self-uid'),
    b(7, text(6, 'Me')),
    responseUnknown,
  ])
  const forward = Buffer.concat([
    v(1, 0),
    v(2, 0),
    v(3, 2),
    text(5, 'self-avatar'),
    text(101, 'opaque-forward'),
  ])
  const content = Buffer.concat([
    v(1, 9),
    v(3, 4),
    v(6, 999),
    b(15, forward),
    contentUnknown,
  ])
  const body = Buffer.concat([
    b(1, response),
    b(2, content),
    b(3, text(44, 'message-body')),
    bodyUnknown,
  ])
  const data = b(1, body)
  const action = Buffer.concat([
    text(1, 'MultiMsg'),
    b(2, data),
    actionUnknown,
  ])
  const payload = Buffer.concat([b(2, action), payloadUnknown])

  const patched = __test.patchNativeForwardProtoPayload(payload, [{
    senderUin: '301',
    senderUid: 'uid-301',
    senderName: 'Alice',
    time: 1710000001,
    sourceType: 'private',
    sourcePeerId: '',
  }])
  const fields = (value) => __test.protoWireFields(value)
  const firstBytes = (value, field) => (
    fields(value).find((item) => item.number === field && item.wire === 2)?.value
  )
  const firstVarint = (value, field) => (
    fields(value).find((item) => item.number === field && item.wire === 0)?.value
  )
  const patchedAction = firstBytes(patched, 2)
  const patchedData = firstBytes(patchedAction, 2)
  const patchedBody = firstBytes(patchedData, 1)
  const patchedResponse = firstBytes(patchedBody, 1)
  const patchedContent = firstBytes(patchedBody, 2)
  const patchedForward = firstBytes(patchedContent, 15)

  assert.equal(firstVarint(patchedResponse, 1), 301n)
  assert.equal(firstBytes(patchedResponse, 2).toString(), 'uid-301')
  assert.equal(firstBytes(firstBytes(patchedResponse, 7), 6).toString(), 'Alice')
  assert.equal(firstVarint(patchedContent, 6), 1710000001n)
  assert.equal(firstVarint(patchedForward, 3), 2n)
  assert.equal(firstBytes(patchedForward, 101).toString(), 'opaque-forward')
  assert.equal(firstBytes(patchedResponse, 99).toString(), 'opaque-response')
  assert.equal(firstBytes(patchedContent, 77).toString(), 'opaque-content')
  assert.equal(firstBytes(patchedBody, 55).toString(), 'opaque-body')
  assert.equal(firstBytes(patchedAction, 88).toString(), 'opaque-action')
  assert.equal(firstBytes(patched, 90).toString(), 'opaque-payload')
})

test('raw native forward patch rebuilds a group-compatible source header', () => {
  const b = __test.protoBytesField
  const v = __test.protoVarintField
  const text = (field, value) => b(field, Buffer.from(value, 'utf8'))
  const response = Buffer.concat([
    v(1, 10000),
    text(2, 'self-uid'),
    text(6, 'self-uid'),
    b(7, text(6, 'Me')),
  ])
  const forward = Buffer.concat([v(1, 0), v(2, 0), v(3, 2)])
  const content = Buffer.concat([
    v(1, 9),
    v(2, 175),
    v(3, 4),
    v(6, 999),
    v(9, 4),
    v(12, 123),
    b(15, forward),
  ])
  const messageBody = b(1, b(2, text(1, 'message')))
  const body = Buffer.concat([b(1, response), b(2, content), b(3, messageBody)])
  const action = Buffer.concat([text(1, 'MultiMsg'), b(2, b(1, body))])
  const payload = b(2, action)

  const patched = __test.patchNativeForwardProtoPayload(payload, [{
    senderUin: '301',
    senderUid: 'uid-301',
    senderName: 'Alice',
    time: 1710000001,
    sourceType: 'group',
    sourcePeerId: '90001',
    sourcePeerName: 'Source Group',
  }])
  const fields = (value) => __test.protoWireFields(value)
  const firstBytes = (value, field) => (
    fields(value).find((item) => item.number === field && item.wire === 2)?.value
  )
  const firstVarint = (value, field) => (
    fields(value).find((item) => item.number === field && item.wire === 0)?.value
  )
  const patchedAction = firstBytes(patched, 2)
  const patchedData = firstBytes(patchedAction, 2)
  const patchedBody = firstBytes(patchedData, 1)
  const patchedResponse = firstBytes(patchedBody, 1)
  const patchedContent = firstBytes(patchedBody, 2)
  const group = firstBytes(patchedResponse, 8)
  const patchedForward = firstBytes(patchedContent, 15)
  const patchedMessageBody = firstBytes(patchedBody, 3)
  const richText = firstBytes(patchedMessageBody, 1)
  const elements = fields(richText).filter((item) => item.number === 2)

  assert.equal(firstBytes(patchedResponse, 6), undefined)
  assert.equal(firstBytes(patchedResponse, 7).length, 0)
  assert.equal(firstVarint(group, 1), 90001n)
  assert.equal(firstBytes(group, 4).toString(), 'Alice')
  assert.equal(firstVarint(group, 5), 2n)
  assert.equal(firstBytes(group, 7).toString(), 'Source Group')
  assert.equal(firstVarint(patchedContent, 1), 82n)
  assert.equal(firstVarint(patchedContent, 2), undefined)
  assert.equal(firstVarint(patchedContent, 3), undefined)
  assert.equal(firstVarint(patchedContent, 9), undefined)
  assert.equal(firstVarint(patchedContent, 12), undefined)
  assert.equal(firstVarint(patchedForward, 3), 1n)
  assert.equal(fields(elements[0].value)[0].number, 37)
  assert.equal(fields(elements[1].value)[0].number, 9)
})

test('raw native forward transport uses QQ long-message packet envelopes', async () => {
  const b = __test.protoBytesField
  const v = __test.protoVarintField
  const text = (field, value) => b(field, Buffer.from(value, 'utf8'))
  const payload = Buffer.concat([text(90, 'native-payload'), v(91, 7)])
  let uploadRequest = null
  const operation = {
    async sendPacket(packet) {
      if (packet.cmd.endsWith('.SsoRecvLongMsg')) {
        const result = Buffer.concat([text(3, 'template-res'), b(4, gzipSync(payload))])
        return b(1, result)
      }
      if (packet.cmd.endsWith('.SsoSendLongMsg')) {
        uploadRequest = packet.data
        return b(2, text(3, 'rebuilt-res'))
      }
      throw new Error('unexpected packet')
    },
  }
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get() {} },
    core: {
      selfInfo: { uid: 'self-uid', uin: '10000' },
      eventWrapper: { createListenerFunction() { return {} } },
      apis: {},
    },
  }

  try {
    await plugin_init(ctx)
    const downloaded = await __test.downloadNativeForwardProto(
      operation,
      'template-res',
    )
    assert.deepEqual(downloaded, payload)
    const newResId = await __test.uploadNativeForwardProto(
      operation,
      payload,
      { chatType: 1, peerUid: 'friend-uid', guildId: '' },
    )
    assert.equal(newResId, 'rebuilt-res')
    const firstBytes = (value, field) => (
      __test.protoWireFields(value)
        .find((item) => item.number === field && item.wire === 2)?.value
    )
    const info = firstBytes(uploadRequest, 2)
    assert.deepEqual(gunzipSync(firstBytes(info, 4)), payload)
    assert.equal(firstBytes(firstBytes(info, 2), 2).toString(), 'self-uid')
  } finally {
    await plugin_cleanup()
  }
})

test('native replay accepts one untouched non-empty MultiMsg action', () => {
  const b = __test.protoBytesField
  const text = (field, value) => b(field, Buffer.from(value, 'utf8'))
  const body = b(1, b(1, Buffer.alloc(0)))
  const action = Buffer.concat([
    text(1, 'MultiMsg'),
    b(2, b(1, body)),
  ])
  const payload = b(2, action)

  assert.equal(__test.validateNativeForwardProtoPayload(payload), 1)
  assert.throws(
    () => __test.validateNativeForwardProtoPayload(b(2, text(1, 'Other'))),
    /MultiMsg/,
  )
})

test('native replay filters outer nodes without rewriting retained protobuf bodies', () => {
  const b = __test.protoBytesField
  const text = (field, value) => b(field, Buffer.from(value, 'utf8'))
  const bodies = [
    Buffer.concat([text(1, 'alice'), text(90, 'keep-a')]),
    Buffer.concat([text(1, 'advertisement'), text(91, 'drop')]),
    Buffer.concat([text(1, 'bob'), text(92, 'keep-b')]),
  ]
  const main = Buffer.concat([
    text(1, 'MultiMsg'),
    b(2, Buffer.concat(bodies.map((body) => b(1, body)))),
  ])
  const child = Buffer.concat([
    text(1, 'child-action'),
    b(2, b(1, text(1, 'nested-unchanged'))),
  ])
  const opaque = text(90, 'payload-unknown')
  const payload = Buffer.concat([b(2, main), b(2, child), opaque])

  const filtered = __test.filterNativeForwardProtoPayload(payload, ['n1'])
  const actions = __test.protoWireFields(filtered)
    .filter((field) => field.number === 2)
    .map((field) => field.value)
  const filteredMain = actions.find((action) => (
    __test.firstProtoString(action, 1) === 'MultiMsg'
  ))
  const filteredChild = actions.find((action) => (
    __test.firstProtoString(action, 1) === 'child-action'
  ))
  const retained = __test.protoWireFields(__test.firstProtoBytes(filteredMain, 2))
    .filter((field) => field.number === 1)
    .map((field) => field.value)

  assert.equal(retained.length, 2)
  assert.deepEqual(retained[0], bodies[0])
  assert.deepEqual(retained[1], bodies[2])
  assert.deepEqual(filteredChild, child)
  assert.deepEqual(__test.firstProtoBytes(filtered, 90), Buffer.from('payload-unknown'))
  assert.equal(__test.validateNativeForwardProtoPayload(filtered), 2)
  assert.throws(
    () => __test.filterNativeForwardProtoPayload(payload, ['n3']),
    /节点不存在/,
  )
  assert.throws(
    () => __test.filterNativeForwardProtoPayload(payload, ['n0', 'n1', 'n2']),
    /没有可发送/,
  )
  assert.throws(
    () => __test.filterNativeForwardProtoPayload(payload, ['n0/s0/n0']),
    /最外层/,
  )
})

test('native replay uploads the archived payload without rewriting its nodes', async () => {
  const b = __test.protoBytesField
  const text = (field, value) => b(field, Buffer.from(value, 'utf8'))
  const archivedPayload = b(2, Buffer.concat([
    text(1, 'MultiMsg'),
    b(2, b(1, b(1, b(1, Buffer.alloc(0))))),
  ]))
  let uploadedPayload = null
  let uploadType = null
  let uploadGroup = null
  let sent = null
  const templateArk = fakeForwardArk('{"filename":"template","tsum":1}\n')
  templateArk.multiForwardMsgElement = { resId: 'template-resource' }
  const templateCard = JSON.parse(templateArk.arkElement.bytesData)
  templateCard.meta.detail.resid = 'template-resource'
  templateArk.arkElement.bytesData = JSON.stringify(templateCard)
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get() {} },
    core: {
      selfInfo: { uid: 'self-uid', uin: '10000', nick: 'Me' },
      eventWrapper: {
        createListenerFunction() { return {} },
        async callNormalEventV2() {
          return [null, [{
            msgId: 'template-card',
            peerUid: 'self-uid',
            senderUid: 'self-uid',
            elements: [templateArk],
          }]]
        },
      },
      apis: {
        MsgApi: {
          async sendMsg(peer, elements) {
            sent = { peer, elements }
            return { msgId: 'sent-card' }
          },
        },
        PacketApi: {
          pkt: {
            operation: {
              async sendPacket(packet) {
                assert.match(packet.cmd, /SsoSendLongMsg$/)
                const info = __test.firstProtoBytes(packet.data, 2)
                uploadedPayload = gunzipSync(__test.firstProtoBytes(info, 4))
                const infoFields = __test.protoWireFields(info)
                uploadType = infoFields.find((field) => field.number === 1)?.value
                uploadGroup = infoFields.find((field) => field.number === 3)?.value
                return b(2, text(3, 'replayed-resource'))
              },
            },
          },
        },
      },
    },
    oneBot: {
      apis: {
        MsgApi: {
          async createSendElements() {
            return {
              sendElements: [{ elementType: 1, textElement: { content: 'seed' } }],
              deleteAfterSentFiles: [],
            }
          },
          async sendMsgWithOb11UniqueId() { return { msgId: 'seed-message' } },
        },
      },
    },
  }
  const archivedCard = {
    app: 'com.tencent.multimsg',
    config: { forward: 1, type: 'normal' },
    extra: '{"filename":"old","tsum":3}\n',
    meta: {
      detail: {
        resid: 'old-resource',
        source: 'Archived source title',
        uniseq: 'old',
      },
    },
    ver: '0.0.0.5',
    view: 'contact',
  }

  try {
    await plugin_init(ctx)
    const result = await __test.replayNativeForwardPayload(
      { chatType: 1, peerUid: 'friend-uid', guildId: '' },
      archivedPayload,
      archivedCard,
      { chatType: 2, peerUid: '90001', guildId: '' },
    )
    assert.equal(result.msgId, 'sent-card')
    assert.deepEqual(uploadedPayload, archivedPayload)
    assert.equal(uploadType, 3n)
    assert.equal(uploadGroup, 90001n)
    assert.equal(sent.peer.peerUid, 'friend-uid')
    const finalCard = JSON.parse(sent.elements[0].arkElement.bytesData)
    assert.equal(finalCard.meta.detail.resid, 'replayed-resource')
    assert.equal(finalCard.meta.detail.source, 'Archived source title')
    assert.equal(JSON.parse(finalCard.extra).tsum, 3)
    assert.equal(
      sent.elements[0].multiForwardMsgElement.resId,
      'replayed-resource',
    )

    await __test.replayNativeForwardPayload(
      { chatType: 1, peerUid: 'friend-uid', guildId: '' },
      archivedPayload,
      null,
      { chatType: 2, peerUid: '90001', guildId: '' },
      true,
      [{
        type: 'node',
        data: {
          nickname: 'Alice',
          content: [{ type: 'image', data: { file: 'local-image' } }],
        },
      }],
    )
    const generatedCard = JSON.parse(sent.elements[0].arkElement.bytesData)
    assert.deepEqual(generatedCard.meta.detail.news, [{ text: 'Alice: [图片]' }])
    assert.equal(generatedCard.meta.detail.summary, '查看1条转发消息')
  } finally {
    await plugin_cleanup()
  }
})

test('native replay preview describes text, media, and nested records', () => {
  assert.deepEqual(__test.nativeForwardPreviewNews([
    {
      type: 'node',
      data: {
        nickname: '用户1',
        content: [{ type: 'image', data: {} }],
      },
    },
    {
      type: 'node',
      data: {
        nickname: '用户2',
        content: [{ type: 'text', data: { text: ' 文本\n内容 ' } }],
      },
    },
    {
      type: 'node',
      data: {
        nickname: '用户3',
        content: [{
          type: 'node',
          data: {
            nickname: '内层用户',
            content: [{ type: 'text', data: { text: '内层' } }],
          },
        }],
      },
    },
    {
      type: 'node',
      data: {
        nickname: '不会显示',
        content: [{ type: 'text', data: { text: '第四条' } }],
      },
    },
  ]), [
    { text: '用户1: [图片]' },
    { text: '用户2: 文本 内容' },
    { text: '用户3: [聊天记录]' },
  ])
})

test('native replay preview uses the human-readable prompt from JSON cards', () => {
  const encoded = JSON.stringify(JSON.stringify({
    app: 'com.tencent.qqshare',
    prompt: '发现超好看的装扮',
    meta: { detail: { title: '不应优先的标题' } },
  }))
  assert.deepEqual(__test.nativeForwardPreviewNews([{
    type: 'node',
    data: {
      nickname: 'Alice',
      content: [{ type: 'json', data: { data: encoded } }],
    },
  }]), [{ text: 'Alice: 发现超好看的装扮' }])
})

test('self native relay rebuilds in self chat and forwards the genuine card', async () => {
  const routes = new Map()
  const staged = []
  const merged = []
  const relayed = []
  const generatedCards = []
  let cardIndex = 0
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      selfInfo: { uid: 'self-uid', uin: '10000', nick: 'Me' },
      eventWrapper: {
        createListenerFunction() { return {} },
        async callNormalEventV2(_service, _listener, args, _resolve, predicate) {
          const [msgInfos, source, destination] = args
          merged.push({ msgInfos, source, destination })
          cardIndex += 1
          const generated = {
            msgId: `self-card-${cardIndex}`,
            peerUid: 'self-uid',
            senderUid: 'self-uid',
            // QQNT 的新外层卡首次通知时可能还没回填 elements。
            elements: cardIndex === 1 ? [fakeForwardArk()] : [],
          }
          // 真实 QQNT 在组装外层时会把内层卡片也放在回调列表前部。
          const records = [...generatedCards, generated]
          generatedCards.push(generated)
          assert.equal(predicate(records), true)
          return [null, records]
        },
      },
      apis: {
        MsgApi: {
          async getMsgsByMsgId(_peer, msgIds) {
            const found = generatedCards.find((item) => msgIds.includes(item.msgId))
            return {
              msgList: found ? [{
                ...found,
                sendStatus: 2,
                elements: [fakeForwardArk()],
              }] : [],
            }
          },
          async forwardMsg(source, destination, msgIds) {
            relayed.push({ source, destination, msgIds })
            return { result: 0, errMsg: '' }
          },
        },
        UserApi: {
          async getUidByUinV2(uin) {
            return uin === '20000' ? 'friend-uid' : ''
          },
        },
      },
    },
    oneBot: {
      apis: {
        MsgApi: {
          async createSendElements(content, peer) {
            return {
              sendElements: content.map((segment) => ({
                elementType: 1,
                textElement: { content: segment.data.text },
                conversionPeer: peer.peerUid,
              })),
              deleteAfterSentFiles: [],
            }
          },
          async sendMsgWithOb11UniqueId(peer, elements) {
            const msgId = `self-leaf-${staged.length + 1}`
            staged.push({ peer, elements, msgId })
            return { msgId }
          },
        },
      },
    },
  }
  const payloadPath = join(
    tmpdir(),
    `ohmymeme-native-forward-${process.pid}-${Date.now()}-self.json`,
  )
  writeFileSync(payloadPath, JSON.stringify({
    schema_version: 1,
    destination_type: 'private',
    destination_peer_id: '20000',
    messages: [
      {
        type: 'node',
        data: {
          nickname: 'Alice',
          content: [{ type: 'text', data: { text: 'outer' } }],
        },
      },
      {
        type: 'node',
        data: {
          nickname: 'Bob',
          content: [{
            type: 'node',
            data: {
              nickname: 'Carol',
              content: [{ type: 'text', data: { text: 'inner' } }],
            },
          }],
        },
      },
    ],
  }), 'utf8')

  await plugin_init(ctx)
  try {
    const response = {}
    await routes.get('/send-self-native-forward')(
      { query: { payload_path: payloadPath } },
      {
        json(value) { response.value = value },
        status() { return this },
      },
    )

    assert.equal(response.value.ok, true)
    assert.equal(response.value.strategy, 'qqnt_self_native_relay')
    assert.equal(response.value.message_id, 'self-card-2')
    assert.equal(staged.length, 2)
    assert.ok(staged.every((item) => item.peer.peerUid === 'self-uid'))
    assert.deepEqual(
      staged.map((item) => item.elements[0].textElement.content),
      ['outer', 'inner'],
    )
    assert.deepEqual(
      merged.map((item) => item.msgInfos.map((info) => info.senderShowName)),
      [['Me'], ['Me', 'Me']],
    )
    assert.ok(merged.every((item) => (
      item.source.peerUid === 'self-uid'
      && item.destination.peerUid === 'self-uid'
    )))
    assert.deepEqual(relayed, [{
      source: { chatType: 1, peerUid: 'self-uid', guildId: '' },
      destination: { chatType: 1, peerUid: 'friend-uid', guildId: '' },
      msgIds: ['self-card-2'],
    }])
    assert.equal(existsSync(payloadPath), false)
  } finally {
    if (existsSync(payloadPath)) rmSync(payloadPath, { force: true })
    await plugin_cleanup()
  }
})

test('native rebuild route asks QQNT to build nested desktop-compatible forwards', async () => {
  const routes = new Map()
  const listener = {}
  const staged = []
  const forwarded = []
  const uploaded = []
  const finalCards = []
  const templateActions = new Map()
  const storedTemplates = new Map()
  let cardIndex = 0
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      selfInfo: { uid: 'self-uid', uin: '10000', nick: 'Me' },
      eventWrapper: {
        createListenerFunction() { return listener },
        async callNormalEventV2(_service, _listener, args) {
          const [msgInfos, , destination] = args
          forwarded.push({ msgInfos, destination })
          cardIndex += 1
          const resId = `template-${cardIndex}`
          templateActions.set(resId, [{
            actionCommand: 'MultiMsg',
            actionData: {
              msgBody: msgInfos.map(() => ({
                responseHead: {
                  fromUin: 10000,
                  fromUid: 'self-uid',
                  forward: { friendName: 'Me' },
                },
                contentHead: {
                  type: 9,
                  timeStamp: 999,
                  forward: { field1: 0, field2: 0, field3: 2 },
                },
                body: { richText: { elems: [] } },
              })),
            },
          }])
          const msgId = `card-${cardIndex}`
          const storedArk = fakeForwardArk('{"filename":"native","tsum":1}\n')
          const storedCard = JSON.parse(storedArk.arkElement.bytesData)
          storedCard.meta.detail.resid = resId
          storedArk.arkElement.bytesData = JSON.stringify(storedCard)
          storedTemplates.set(msgId, {
            msgId,
            msgSeq: String(cardIndex),
            peerUid: destination.peerUid,
            senderUid: 'self-uid',
            elements: [storedArk],
          })
          const callbackArk = fakeForwardArk('{"filename":"native","tsum":1}\n')
          const callbackCard = JSON.parse(callbackArk.arkElement.bytesData)
          // QQNT 的即时回调可能只有未补齐 resid 的 ARK；
          // 消息落库后再按 msgId 读取完整元素。
          callbackCard.meta.detail.resid = ''
          callbackArk.arkElement.bytesData = JSON.stringify(callbackCard)
          return [null, [{
            msgId,
            msgSeq: String(cardIndex),
            peerUid: destination.peerUid,
            senderUid: 'self-uid',
            elements: [callbackArk],
          }]]
        },
      },
      apis: {
        MsgApi: {
          async getMsgsByMsgId(_peer, msgIds) {
            return { msgList: msgIds.map((id) => storedTemplates.get(id)).filter(Boolean) }
          },
          async sendMsg(peer, elements) {
            const msgId = `sent-${finalCards.length + 1}`
            finalCards.push({ peer, elements, msgId })
            return { msgId }
          },
        },
        PacketApi: {
          pkt: {
            operation: {
              async FetchForwardMsgRaw(resId) {
                return templateActions.get(resId)
              },
              async UploadForwardMsgV2(actions, groupUin) {
                const resId = `rebuilt-${uploaded.length + 1}`
                uploaded.push({ actions, groupUin, resId })
                return resId
              },
            },
          },
        },
        UserApi: {
          async getUidByUinV2(uin) {
            return {
              20000: 'friend-uid',
              302: 'uid-302',
            }[uin] || ''
          },
        },
      },
    },
    oneBot: {
      apis: {
        MsgApi: {
          async createSendElements(content) {
            return {
              sendElements: content.map((segment) => ({
                elementType: 1,
                textElement: { content: segment.data.text },
              })),
              deleteAfterSentFiles: [],
            }
          },
          async sendMsgWithOb11UniqueId(peer, elements) {
            const msgId = `stage-${staged.length + 1}`
            staged.push({ peer, elements, msgId })
            return { msgId }
          },
        },
      },
    },
  }
  const payloadPath = join(
    tmpdir(),
    `ohmymeme-native-forward-${process.pid}-${Date.now()}.json`,
  )
  writeFileSync(payloadPath, JSON.stringify({
    schema_version: 1,
    destination_type: 'private',
    destination_peer_id: '20000',
    messages: [
      {
        type: 'node',
        data: {
          nickname: 'Alice',
          user_id: '301',
          omm_sender_uid: 'uid-301',
          omm_source_type: 'group',
          omm_source_peer_id: '90001',
          time: '1710000001',
          content: [{ type: 'text', data: { text: 'outer' } }],
        },
      },
      {
        type: 'node',
        data: {
          nickname: 'Bob',
          user_id: '302',
          time: '1710000002',
          content: [{
            type: 'node',
            data: {
              nickname: 'Carol',
              user_id: '303',
              omm_sender_uid: 'uid-303',
              time: '1710000003',
              content: [{ type: 'text', data: { text: 'inner' } }],
            },
          }],
        },
      },
    ],
  }), 'utf8')

  await plugin_init(ctx)
  try {
    const response = {}
    await routes.get('/send-native-forward')(
      { query: { payload_path: payloadPath } },
      {
        json(value) { response.value = value },
        status() { return this },
      },
    )

    assert.equal(response.value.ok, true)
    assert.equal(response.value.strategy, 'qqnt_native_rebuild')
    assert.equal(staged.length, 2)
    assert.deepEqual(
      forwarded.map((call) => call.msgInfos.map((item) => item.senderShowName)),
      [['Carol'], ['Alice', 'Bob']],
    )
    assert.deepEqual(
      forwarded.map((call) => call.destination.peerUid),
      ['self-uid', 'self-uid'],
    )
    assert.equal(uploaded.length, 2)
    const innerBody = uploaded[0].actions[0].actionMsgBody[0]
    assert.equal(innerBody.responseHead.fromUin, 303)
    assert.equal(innerBody.responseHead.fromUid, 'uid-303')
    assert.equal(innerBody.responseHead.forward.friendName, 'Carol')
    assert.equal(innerBody.contentHead.timeStamp, 1710000003)
    const outerBodies = uploaded[1].actions[0].actionMsgBody
    assert.deepEqual(
      outerBodies.map((body) => body.responseHead.fromUin),
      [301, 302],
    )
    assert.deepEqual(
      outerBodies.map((body) => body.responseHead.fromUid),
      ['uid-301', 'uid-302'],
    )
    assert.equal(outerBodies[0].responseHead.forward.friendName, undefined)
    assert.deepEqual(outerBodies[0].responseHead.grp, {
      groupUin: 90001,
      memberName: 'Alice',
      unknown5: 2,
    })
    assert.equal(outerBodies[0].contentHead.type, 82)
    assert.equal(outerBodies[0].contentHead.forward.field3, 1)
    assert.deepEqual(
      outerBodies[0].body.richText.elems.slice(0, 2),
      [{ generalFlags: {} }, { elemFlags2: {} }],
    )
    assert.equal(outerBodies[1].responseHead.forward.friendName, 'Bob')
    assert.deepEqual(
      finalCards.map((item) => item.peer.peerUid),
      ['self-uid', 'friend-uid'],
    )
    const finalCard = JSON.parse(finalCards[1].elements[0].arkElement.bytesData)
    assert.equal(finalCard.meta.detail.resid, 'rebuilt-2')
    assert.equal(typeof finalCard.extra, 'string')
    assert.equal(JSON.parse(finalCard.extra).tsum, 2)
    assert.equal(existsSync(payloadPath), false)
  } finally {
    if (existsSync(payloadPath)) rmSync(payloadPath, { force: true })
    await plugin_cleanup()
  }
})

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
  assert.equal(typeof listener.onMsgInfoListUpdate, 'function')
  assert.equal(typeof listener.onMsgInfoListAdd, 'function')
  assert.equal(listener.onInputStatusPush, undefined)
  listener.onMsgInfoListUpdate({
    chatType: 1,
    peerUid: 'background-uid',
    msgList: [{ msgId: '1234567890123456789' }],
  })

  const response = {}
  await routes.get('/current-peer')({}, {
    json(value) { response.value = value },
    status() { return this },
  })
  assert.equal(response.value.confidence, 'active_tab')
  assert.equal(response.value.target.peer_id, '123456')
  assert.equal(
    __test.state.messagePeers.get('1234567890123456789').peer,
    'background-uid',
  )
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

test('renderer title lookup safely falls back to one chat window behind a host HWND', async () => {
  const chatWindow = (handle, titles) => ({
    getNativeWindowHandle() {
      return Uint8Array.from([
        handle & 0xff,
        (handle >> 8) & 0xff,
        (handle >> 16) & 0xff,
        (handle >> 24) & 0xff,
      ])
    },
    webContents: {
      isDestroyed() { return false },
      async executeJavaScript() { return titles },
    },
  })
  const response = await __test.rendererTitleResponse('999', [
    chatWindow(100, []),
    chatWindow(200, ['目标群']),
  ])

  assert.equal(response.ok, true)
  assert.deepEqual(response.titleCandidates, [{ name: '目标群', score: 300 }])
})

test('renderer title lookup refuses to guess between multiple chat windows', async () => {
  const chatWindow = (handle, title) => ({
    getNativeWindowHandle() {
      return Uint8Array.from([handle, 0, 0, 0])
    },
    webContents: {
      isDestroyed() { return false },
      async executeJavaScript() { return [title] },
    },
  })
  const response = await __test.rendererTitleResponse('999', [
    chatWindow(10, '群 A'),
    chatWindow(20, '群 B'),
  ])

  assert.equal(response.ok, false)
  assert.match(response.error, /多个聊天窗口/)
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

test('plugin flips a persisted private peer for the other participant account', async () => {
  const routes = new Map()
  const calls = []
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      selfInfo: { uin: '10000', uid: 'uid-a' },
      eventWrapper: { createListenerFunction() { return {} } },
      apis: {
        UserApi: {
          async getUidByUinV2(uin) {
            calls.push(['uid', uin])
            return uin === '20000' ? 'uid-b' : ''
          },
        },
        MsgApi: {
          async getMsgsBySeqAndCount(peer, seq) {
            calls.push(['lookup', peer, seq])
            return {
              msgList: [{
                msgSeq: '7788',
                msgId: 'raw-private-card',
                msgTime: '1710000000',
                senderUin: '20000',
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
  try {
    const response = {}
    await routes.get('/forward-message')({
      query: {
        source_type: 'private',
        source_peer_id: '10000',
        source_self_id: '20000',
        source_real_seq: '7788',
        source_time: '1710000000',
        source_sender_id: '20000',
        destination_type: 'group',
        destination_peer_id: '30000',
      },
    }, {
      json(value) { response.value = value },
    })

    assert.equal(response.value.ok, true)
    assert.deepEqual(calls, [
      ['uid', '20000'],
      ['lookup', { chatType: 1, peerUid: 'uid-b', guildId: '' }, '7788'],
      [
        'forward',
        { chatType: 1, peerUid: 'uid-b', guildId: '' },
        { chatType: 2, peerUid: '30000', guildId: '' },
        ['raw-private-card'],
      ],
    ])
  } finally {
    await plugin_cleanup()
  }
})

test('plugin rejects a private archive owned by two other accounts', async () => {
  const routes = new Map()
  let lookedUp = false
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      selfInfo: { uin: '30000', uid: 'uid-c' },
      eventWrapper: { createListenerFunction() { return {} } },
      apis: {
        UserApi: { async getUidByUinV2() { return 'must-not-resolve' } },
        MsgApi: {
          async getMsgsBySeqAndCount() {
            lookedUp = true
            return { msgList: [] }
          },
        },
      },
    },
  }

  await plugin_init(ctx)
  try {
    const response = {}
    await routes.get('/forward-message')({
      query: {
        source_type: 'private',
        source_peer_id: '10000',
        source_self_id: '20000',
        source_real_seq: '7788',
        destination_type: 'group',
        destination_peer_id: '40000',
      },
    }, {
      json(value) { response.value = value },
    })

    assert.equal(response.value.ok, false)
    assert.match(response.value.error, /不属于当前 QQ 账号/)
    assert.equal(lookedUp, false)
  } finally {
    await plugin_cleanup()
  }
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
  const nativePayload = Buffer.from('multi-forward-only-payload')
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
        PacketApi: {
          pkt: {
            operation: {
              async sendPacket() {
                const b = __test.protoBytesField
                return b(1, b(4, gzipSync(nativePayload)))
              },
            },
          },
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
  assert.equal(response.value.native_payload_preserved, true)
  assert.deepEqual(
    Buffer.from(response.value.native_payload_b64, 'base64'),
    nativePayload,
  )
  assert.equal(response.value.native_card, null)
  assert.equal(raw.id, 'raw-nested-id')
  await plugin_cleanup()
})

test('plugin recovers a replied forward card by message id in its conversation', async () => {
  const routes = new Map()
  const calls = []
  const raw = {
    msgSeq: '7788',
    msgId: '7690600602430115722',
    msgTime: '1710000000',
    senderUin: '111222',
    elements: [fakeForwardArk()],
  }
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      selfInfo: { uin: '10000' },
      eventWrapper: { createListenerFunction() { return {} } },
      apis: {
        UserApi: {},
        MsgApi: {
          async getMsgsByMsgId(peer, ids) {
            calls.push([peer, ids])
            return { msgList: [raw] }
          },
          async getMultiMsg() { return { messages: [] } },
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
              message: [{
                type: 'forward',
                data: { content: [{ message: [{ type: 'text', data: { text: '内容' } }] }] },
              }],
            }
          },
        },
      },
    },
  }

  await plugin_init(ctx)
  try {
    const response = {}
    await routes.get('/recover-message-by-id')({
      query: {
        message_id: raw.msgId,
        source_type: 'group',
        source_peer_id: '123456',
        source_self_id: '10000',
      },
    }, {
      json(value) { response.value = value },
    })

    assert.equal(response.value.ok, true)
    assert.equal(response.value.message.real_seq, '7788')
    assert.equal(response.value.message.message_type, 'group')
    assert.equal(response.value.message.group_id, '123456')
    assert.deepEqual(calls, [[
      { chatType: 2, peerUid: '123456', guildId: '' },
      [raw.msgId],
    ]])
  } finally {
    await plugin_cleanup()
  }
})

test('plugin flips a persisted private peer while rebuilding the original message', async () => {
  const routes = new Map()
  const calls = []
  const nativePayload = Buffer.from('native-forward-payload')
  const raw = {
    msgSeq: '7788',
    msgId: 'raw-private-card',
    msgTime: '1710000000',
    senderUin: '20000',
    elements: [
      fakeForwardArk(),
      { multiForwardMsgElement: { resId: 'forward-resource' } },
    ],
  }
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      selfInfo: { uin: '10000', uid: 'uid-a' },
      eventWrapper: { createListenerFunction() { return {} } },
      apis: {
        UserApi: {
          async getUidByUinV2(uin) {
            calls.push(['uid', uin])
            return uin === '20000' ? 'uid-b' : ''
          },
        },
        MsgApi: {
          async getMsgsBySeqAndCount(peer, seq) {
            calls.push(['lookup', peer, seq])
            return { msgList: [raw] }
          },
        },
        PacketApi: {
          pkt: {
            operation: {
              async sendPacket(packet) {
                assert.match(packet.cmd, /\.SsoRecvLongMsg$/)
                const b = __test.protoBytesField
                const text = (field, value) => b(field, Buffer.from(value, 'utf8'))
                return b(1, Buffer.concat([
                  text(3, 'test-resource'),
                  b(4, gzipSync(nativePayload)),
                ]))
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
              message: [{ type: 'forward', data: { id: 'forward-resource' } }],
            }
          },
        },
      },
    },
  }

  await plugin_init(ctx)
  try {
    const response = {}
    await routes.get('/recover-message')({
      query: {
        source_type: 'private',
        source_peer_id: '10000',
        source_self_id: '20000',
        source_real_seq: '7788',
        source_time: '1710000000',
        source_sender_id: '20000',
      },
    }, {
      json(value) { response.value = value },
    })

    assert.equal(response.value.ok, true)
    assert.deepEqual(calls, [
      ['uid', '20000'],
      ['lookup', { chatType: 1, peerUid: 'uid-b', guildId: '' }, '7788'],
    ])
    assert.equal(response.value.message.self_id, '10000')
    assert.equal(response.value.message.message_type, 'private')
    assert.equal(response.value.message.target_id, '20000')
    assert.equal(response.value.message.real_seq, '7788')
    assert.equal(response.value.native_payload_preserved, true)
    assert.deepEqual(
      Buffer.from(response.value.native_payload_b64, 'base64'),
      nativePayload,
    )
    assert.equal(response.value.native_card.app, 'com.tencent.multimsg')
  } finally {
    await plugin_cleanup()
  }
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
  assert.equal(response.value.messages[0].omm_sender_uid, 'uid-111')
  const forward = response.value.messages[0].message[0]
  assert.equal(forward.type, 'forward')
  assert.equal(forward.data.content[0].omm_sender_uid, 'uid-111')
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

test('plugin preserves media from a reply source record inside forwarded chat history', async () => {
  const temp = mkdtempSync(join(tmpdir(), 'omm-napcat-reply-media-'))
  const quotedImage = join(temp, 'quoted.jpg')
  writeFileSync(quotedImage, Buffer.from('image'))
  const routes = new Map()
  let multiMsgCalls = 0
  const packetReply = { reply: true }
  const replyElement = {
    sourceMsgIdInRecords: 'quoted-message',
    replayMsgId: 'quoted-message',
    replayMsgSeq: '9',
    replyMsgTime: '1710000000',
    sourceMsgTextElems: [{ textElemContent: '[图片]' }],
  }
  const actions = [{
    actionCommand: 'MultiMsg',
    actionData: {
      msgBody: [{
        responseHead: {
          fromUin: 111,
          fromUid: 'uid-111',
          grp: { groupUin: 222, memberName: 'Bob' },
        },
        contentHead: { newId: 'reply-message', sequence: 10, timeStamp: 1710000001 },
        body: { richText: { elems: [packetReply] } },
      }],
    },
  }]
  const quotedRaw = {
    msgId: 'quoted-message',
    msgSeq: '9',
    msgTime: '1710000000',
    chatType: 2,
    peerUid: '222',
    senderUin: '333',
    sendNickName: 'Alice',
    records: [],
    elements: [{
      elementId: 'quoted-image-element',
      elementType: 2,
      picElement: {
        fileName: 'quoted-md5',
        md5HexStr: 'quoted-md5',
        sourcePath: quotedImage,
      },
    }],
  }
  const nativeInner = {
    msgId: 'reply-message',
    msgSeq: '10',
    msgTime: '1710000001',
    chatType: 2,
    peerUid: '222',
    senderUin: '111',
    records: [quotedRaw],
    elements: [{ elementType: 7, elementId: 'reply', replyElement }],
  }
  const outer = {
    msgId: 'outer-message',
    msgSeq: '77',
    msgTime: '1710000002',
    senderUin: '111',
    elements: [{ multiForwardMsgElement: { resId: 'outer-resource' } }],
  }
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get(path, handler) { routes.set(path, handler) } },
    core: {
      eventWrapper: { createListenerFunction() { return {} } },
      apis: {
        MsgApi: {
          async getMsgsBySeqAndCount() { return { msgList: [outer] } },
          async getMultiMsg() {
            multiMsgCalls += 1
            return { msgList: [nativeInner] }
          },
        },
        PacketApi: {
          pkt: {
            operation: { async FetchForwardMsgRaw() { return actions } },
            msgConverter: {
              packetMsgToRaw() {
                return [[{
                  elementType: 7,
                  elementId: 'reply',
                  replyElement: { ...replyElement },
                }, packetReply]]
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
              message: raw.elements.map((element) => {
                if (element.multiForwardMsgElement) {
                  return {
                    type: 'forward',
                    data: {
                      id: element.multiForwardMsgElement.resId,
                      content: [{
                        message: [{ type: 'reply', data: { id: 'simplified-only' } }],
                      }],
                    },
                  }
                }
                if (element.replyElement) {
                  return { type: 'reply', data: { id: element.replyElement.replayMsgId } }
                }
                if (element.picElement) {
                  return { type: 'image', data: { file: element.picElement.fileName } }
                }
                return { type: 'text', data: { text: '' } }
              }),
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
      },
    }, {
      json(value) { response.value = value },
    })

    const reply = response.value.messages[0].message[0]
    assert.equal(reply.type, 'reply')
    assert.equal(reply.data.omm_quoted_message.sender.nickname, 'Alice')
    assert.equal(reply.data.omm_quoted_message.message[0].type, 'image')
    assert.equal(reply.data.omm_quoted_message.message[0].data.path, quotedImage)
    assert.equal(multiMsgCalls, 1)

    const dragged = await __test.recoverStoredMessage({
      source_type: 'group',
      source_peer_id: '222',
      source_real_seq: '77',
      source_time: '1710000002',
      source_sender_id: '111',
    })
    const draggedReply = dragged.message.message[0].data.content[0].message[0]
    assert.equal(draggedReply.type, 'reply')
    assert.equal(draggedReply.data.omm_quoted_message.sender.nickname, 'Alice')
    assert.equal(draggedReply.data.omm_quoted_message.message[0].data.path, quotedImage)
    assert.equal(multiMsgCalls, 2)
  } finally {
    await plugin_cleanup()
    rmSync(temp, { recursive: true, force: true })
  }
})

test('plugin enriches replies in the final nested forward tree', async () => {
  const quotedRaw = {
    msgId: 'quoted-message',
    msgSeq: '1460',
    msgTime: '1710000000',
    senderUin: '333',
    sendNickName: 'Alice',
    records: [],
    elements: [{
      elementType: 2,
      elementId: 'quoted-image',
      picElement: { fileName: 'quoted.jpg', sourcePath: 'C:/cache/quoted.jpg' },
    }],
  }
  const replyElement = {
    sourceMsgIdInRecords: 'quoted-message',
    replayMsgSeq: '1460',
    replyMsgTime: '1710000000',
    senderUin: '333',
  }
  const nestedRaw = {
    msgId: 'nested-message',
    msgSeq: '1471',
    elements: [{ elementType: 7, elementId: 'reply', replyElement }],
  }
  const parsed = {
    message: [{
      type: 'forward',
      data: { content: [{
        real_seq: '1515',
        message: [{
          type: 'forward',
          data: { content: [{
            real_seq: '1471',
            message: [{ type: 'reply', data: { id: 'onebot-short-id' } }],
          }] },
        }],
      }] },
    }],
  }
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get() {} },
    core: {
      eventWrapper: { createListenerFunction() { return {} } },
      apis: { MsgApi: {}, UserApi: {} },
    },
    oneBot: {
      apis: {
        MsgApi: {
          async parseMessage(raw) {
            assert.equal(raw, quotedRaw)
            return { message: [{ type: 'image', data: { file: 'quoted.jpg' } }] }
          },
        },
      },
    },
  }

  await plugin_init(ctx)
  try {
    await __test.enrichParsedReplyQuotes(
      parsed,
      {
        messages: 2,
        rawById: new Map([
          ['nested-message', nestedRaw],
          ['quoted-message', quotedRaw],
        ]),
        rawBySeq: new Map([
          ['1471', nestedRaw],
          ['1460', quotedRaw],
        ]),
        rootMessages: [],
      },
      ctx.oneBot.apis.MsgApi.parseMessage,
    )
    const reply = parsed.message[0].data.content[0].message[0].data.content[0].message[0]
    assert.equal(reply.data.omm_quoted_message.sender.nickname, 'Alice')
    assert.equal(reply.data.omm_quoted_message.message[0].type, 'image')
  } finally {
    await plugin_cleanup()
  }
})

test('plugin merge keeps nested replies while adding recovered media', () => {
  const standard = [{
    message_id: 'outer-node',
    real_seq: '1515',
    message: [{
      type: 'forward',
      data: { content: [{
        message_id: 'nested-node',
        real_seq: '1471',
        message: [
          { type: 'reply', data: { id: 'onebot-reply-id' } },
          { type: 'image', data: { file: 'image.jpg' } },
        ],
      }] },
    }],
  }]
  const recovered = [{
    real_seq: '1515',
    message: [{
      type: 'forward',
      data: { content: [{
        real_seq: '1471',
        message: [{
          type: 'image',
          data: { file: 'image.jpg', path: 'C:/cache/image.jpg' },
        }],
      }] },
    }],
  }]

  const merged = __test.mergeParsedForwardNodes(standard, recovered)
  const nested = merged[0].message[0].data.content[0].message
  assert.equal(nested[0].type, 'reply')
  assert.equal(nested[0].data.id, 'onebot-reply-id')
  assert.equal(nested[1].type, 'image')
  assert.equal(nested[1].data.path, 'C:/cache/image.jpg')
})

test('native nested forwards keep the outer root and conversation when reading reply records', async () => {
  const sourcePeer = { chatType: 2, peerUid: 'source-peer', guildId: '' }
  const quoted = { msgId: 'quoted', msgSeq: '7', elements: [], records: [] }
  const leaf = {
    msgId: 'leaf', msgSeq: '8',
    elements: [{ replyElement: { sourceMsgIdInRecords: 'quoted' } }],
    records: [quoted],
  }
  const snapshot = { msgId: 'snapshot', msgSeq: '9', elements: [], records: [] }
  const middle = {
    msgId: 'middle', msgSeq: '10', chatType: 1, peerUid: 'synthetic-peer',
    elements: [
      { multiForwardMsgElement: { resId: 'inner-resource' } },
      { replyElement: { sourceMsgIdInRecords: 'snapshot' } },
    ],
    records: [snapshot],
  }
  const top = {
    msgId: 'top', msgSeq: '11', chatType: 1, peerUid: 'another-synthetic-peer',
    elements: [{ multiForwardMsgElement: { resId: 'middle-resource' } }],
    records: [],
  }
  const outer = {
    msgId: 'outer', msgSeq: '12',
    elements: [{ multiForwardMsgElement: { resId: 'outer-resource' } }],
  }
  const calls = []
  const ctx = {
    logger: { info() {}, error() {} },
    router: { get() {} },
    core: {
      eventWrapper: { createListenerFunction() { return {} } },
      apis: {
        MsgApi: {
          async getMultiMsg(peer, rootId, parentId) {
            assert.deepEqual(peer, sourcePeer)
            assert.equal(rootId, 'outer')
            calls.push(parentId)
            return { msgList: { outer: [top], top: [middle], middle: [leaf] }[parentId] || [] }
          },
        },
      },
    },
  }
  await plugin_init(ctx)
  try {
    const native = await __test.downloadNativeForwardMedia({
      source_type: 'group', source_peer_id: '222', source_real_seq: '12',
      _source_peer: sourcePeer, _outer_message: outer,
    })
    assert.deepEqual(calls, ['outer', 'top', 'middle'])
    assert.equal(native.messages, 5)
    assert.equal(native.rawById.get('leaf'), leaf)
    assert.equal(native.rawById.get('quoted'), quoted)
    assert.equal(native.rawBySeq.get('8'), leaf)
  } finally {
    await plugin_cleanup()
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

test('forward images try a fresh QQ URL before waiting for native download events', async () => {
  const temp = mkdtempSync(join(tmpdir(), 'omm-forward-image-order-'))
  try {
    for (const success of [true, false]) {
      const target = join(temp, `fresh-${success}.jpg`)
      const fallback = join(temp, `fallback-${success}.jpg`)
      writeFileSync(fallback, Buffer.from('image'))
      const calls = []
      const ctx = {
        logger: { info() {}, error() {} },
        router: { get() {} },
        core: {
          eventWrapper: { createListenerFunction() { return {} } },
          apis: {
            FileApi: {
              async getImageUrl() {
                calls.push('url')
                if (success) writeFileSync(target, Buffer.from('image'))
                return success ? 'https://gchat.qpic.cn/test.jpg' : ''
              },
              async downloadMedia() { calls.push('native'); return fallback },
            },
          },
        },
      }
      await plugin_init(ctx)
      try {
        const result = await __test.downloadQQImageByMessage(
          { msgId: 'inner', chatType: 2, peerUid: 'peer' },
          { elementId: 'image', picElement: { sourcePath: target } },
          20000, {}, true,
        )
        assert.equal(result, success ? target : fallback)
        assert.deepEqual(calls, success ? ['url'] : ['url', 'native'])
      } finally {
        await plugin_cleanup()
      }
    }
  } finally {
    rmSync(temp, { recursive: true, force: true })
  }
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

test('QQ drag bridge installs, reports status, and removes renderer listeners', async () => {
  let installed = false
  let count = 0
  const window = {
    webContents: {
      isDestroyed() { return false },
      async executeJavaScript(script) {
        if (script.includes('const action = "start"')) installed = true
        if (script.includes('const action = "stop"')) installed = false
        if (installed) count = 1
        return {
          enabled: installed,
          count,
          installed_at: installed ? 100 : 0,
          last: installed ? { at: 101, event_type: 'pointerdown', chain: [] } : null,
        }
      },
    },
  }

  try {
    const started = await __test.applyQQDragBridgeToWindows('start', [window])
    assert.equal(started[0].status.enabled, true)

    const status = await __test.applyQQDragBridgeToWindows('status', [window])
    assert.equal(status[0].status.enabled, true)

    const stopped = await __test.applyQQDragBridgeToWindows('stop', [window])
    assert.equal(stopped[0].status.enabled, false)
  } finally {
    await __test.applyQQDragBridgeToWindows('stop', [window])
  }
})

test('production QQ drag bridge does not collect renderer diagnostics or visible content', () => {
  const script = __test.qqDragBridgeScript('start')

  assert.doesNotThrow(() => new Function(script))
  assert.equal(script.includes('innerText'), false)
  assert.equal(script.includes('textContent'), false)
  assert.equal(script.includes('outerHTML'), false)
  assert.equal(script.includes('innerHTML'), false)
  assert.equal(script.includes('attribute_names'), false)
  assert.equal(script.includes('attribute_hints'), false)
  assert.equal(script.includes('getComputedStyle'), false)
  assert.equal(script.includes('state.last'), false)
  assert.equal(script.includes('market-face-element'), true)
  assert.equal(script.includes('chat_record'), true)
  assert.equal(script.includes("classList?.contains('ml-item')"), true)
  assert.equal(script.includes("getAttribute?.('msg-id')"), true)
  assert.equal(script.includes("addEventListener('pointerover'"), false)
  assert.equal(script.includes("addEventListener('dragstart'"), true)
  assert.equal(script.includes("addEventListener('pointerup'"), true)
  assert.equal(script.includes("consolePrefix + 'cancel:'"), true)
  assert.equal(script.includes("consolePrefix + 'prepare-chat:'"), true)
  assert.equal(script.includes('event.preventDefault()'), true)
})

test('reading a market face pending payload does not consume it before dragstart', () => {
  const script = __test.marketFacePendingScript(17)

  assert.doesNotThrow(() => new Function(script))
  assert.equal(script.includes('Number(pending.seq) !== 17'), true)
  assert.equal(script.includes('state.pending = null'), false)
})

test('chat record drag exports a one-time authenticated ticket', async () => {
  const raw = {
    msgId: '123456789',
    msgSeq: '4321',
    msgTime: '1700000000',
    senderUin: '223344',
    elements: [fakeForwardArk()],
  }
  const parsed = {
    message_id: 987,
    message: [{ type: 'forward', data: { id: 'test-resource' } }],
    sender: { user_id: 223344, nickname: '测试用户' },
    time: 1700000000,
  }
  const msgApi = {
    async getMsgsByMsgId(peer, ids) {
      assert.deepEqual(peer, { chatType: 2, peerUid: '778899', guildId: '' })
      assert.deepEqual(ids, ['123456789'])
      return { msgList: [raw] }
    },
    async getMsgsBySeqAndCount(peer, seq) {
      assert.deepEqual(peer, { chatType: 2, peerUid: '778899', guildId: '' })
      assert.equal(seq, '4321')
      return { msgList: [raw] }
    },
  }
  __test.state.active = true
  __test.state.ctx = {
    core: {
      selfInfo: { uin: '112233' },
      apis: { MsgApi: msgApi },
    },
    oneBot: {
      apis: {
        MsgApi: {
          async parseMessage(message) {
            assert.equal(message, raw)
            return structuredClone(parsed)
          },
        },
      },
    },
  }
  __test.state.messagePeers.set('123456789', {
    chatType: 2,
    peer: '778899',
    groupCode: '778899',
    userUin: '',
  })

  try {
    const output = __test.chatRecordTicketFromPending({
      kind: 'chat_record',
      msgId: '123456789',
    })
    assert.equal(output instanceof Promise, false)
    assert.equal(output.extension, '.ommchat')
    const descriptor = JSON.parse(output.buffer.toString('utf8'))
    assert.deepEqual(Object.keys(descriptor).sort(), [
      'plugin_version',
      'schema_version',
      'ticket_id',
      'type',
    ])
    assert.equal(descriptor.type, 'ohmymeme.qq-chat-record-drag')
    assert.equal(descriptor.schema_version, 1)

    const source = await __test.consumeChatRecordTicket(descriptor.ticket_id)
    assert.equal(source.message_id, 987)
    assert.equal(source.message_type, 'group')
    assert.equal(source.group_id, '778899')
    assert.equal(source.self_id, '112233')
    assert.equal(source.real_seq, '4321')
    await assert.rejects(
      __test.consumeChatRecordTicket(descriptor.ticket_id),
      /已过期/,
    )
  } finally {
    __test.state.active = false
    __test.state.ctx = null
    __test.state.messagePeers.clear()
    __test.cleanupMarketFaceTempFiles()
  }
})

test('the exact renderer title can resolve its peer for future drag exports', async () => {
  __test.state.ctx = {
    core: {
      apis: {
        FriendApi: {
          async getBuddy() {
            return [{
              coreInfo: {
                uid: 'friend-uid',
                uin: '123456',
                nick: '原始昵称',
                remark: '唯一备注',
              },
            }]
          },
        },
        GroupApi: {
          async getGroups() {
            return [{
              groupCode: '778899',
              groupName: '测试群',
              remarkName: '',
            }]
          },
        },
      },
    },
  }
  const webContents = {
    isDestroyed() { return false },
    async executeJavaScript() { return ['唯一备注'] },
  }

  try {
    assert.deepEqual(await __test.peerFromRendererTitle(webContents), {
      chatType: 1,
      peer: 'friend-uid',
      userUin: '123456',
      groupCode: '',
    })
  } finally {
    __test.state.ctx = null
  }
})

test('market face data URLs are decoded by image signature instead of declared type', () => {
  const gif = Buffer.from('GIF89a-test')
  const decoded = __test.decodeMarketFaceDataUrl(
    `data:image/png;base64,${gif.toString('base64')}`,
  )

  assert.equal(decoded.extension, '.gif')
  assert.deepEqual(decoded.buffer, gif)
  assert.throws(
    () => __test.decodeMarketFaceDataUrl('data:image/svg+xml;base64,PHN2Zz4='),
    /受支持/,
  )
})

test('market face native drag creates a temporary real image file', async () => {
  const gif = Buffer.from('GIF89a-test')
  let dragged = null
  let calls = 0
  const webContents = {
    isDestroyed() { return false },
    async executeJavaScript(script) {
      calls += 1
      if (calls === 1) {
        assert.equal(script.includes('Number(pending.seq) !== 7'), true)
        return { seq: 7, dataSrc: `data:image/gif;base64,${gif.toString('base64')}` }
      }
      assert.equal(script.includes('state.readySequence = 7'), true)
      return true
    },
    startDrag(item) {
      dragged = item
      assert.equal(existsSync(item.file), true)
      assert.equal(existsSync(item.icon), true)
    },
  }

  try {
    __test.state.qqDragBridgeEnabled = true
    assert.equal(await __test.prepareMarketFaceNativeDrag(webContents, 7), true)
    assert.equal(__test.startMarketFaceNativeDrag(webContents, 7), true)
    assert.equal(dragged.file.endsWith('.gif'), true)
    assert.equal(dragged.icon.endsWith('-drag-icon.png'), true)
    assert.notEqual(dragged.icon, dragged.file)
  } finally {
    __test.state.qqDragBridgeEnabled = false
    __test.cleanupMarketFaceTempFiles()
  }
  assert.equal(existsSync(dragged.file), false)
})

test('chat record native drag skips the renderer pending-data round trip', async () => {
  let dragged = null
  let executeCalls = 0
  const webContents = {
    isDestroyed() { return false },
    async executeJavaScript(script) {
      executeCalls += 1
      assert.equal(script.includes('state.readySequence = 11'), true)
      return true
    },
    startDrag(item) { dragged = item },
  }

  try {
    __test.state.qqDragBridgeEnabled = true
    assert.equal(await __test.prepareMarketFaceNativeDrag(webContents, 11, {
      seq: 11,
      kind: 'chat_record',
      msgId: '123456789',
    }), true)
    assert.equal(executeCalls, 1)
    assert.equal(__test.startMarketFaceNativeDrag(webContents, 11), true)
    assert.ok(dragged)
    assert.equal(dragged.file.endsWith('.ommchat'), true)
  } finally {
    __test.state.qqDragBridgeEnabled = false
    __test.cleanupMarketFaceTempFiles()
  }
})

test('market face native drag waits for asynchronous temporary-file preparation', async () => {
  const gif = Buffer.from('GIF89a-deferred')
  let dragged = null
  let calls = 0
  const webContents = {
    isDestroyed() { return false },
    async executeJavaScript(script) {
      calls += 1
      if (calls === 1) {
        return { seq: 9, dataSrc: `data:image/gif;base64,${gif.toString('base64')}` }
      }
      assert.equal(script.includes('state.readySequence = 9'), true)
      return true
    },
    startDrag(item) { dragged = item },
  }

  try {
    __test.state.qqDragBridgeEnabled = true
    assert.equal(__test.startMarketFaceNativeDrag(webContents, 9), true)
    assert.equal(dragged, null)
    assert.equal(__test.state.marketFaceRequestedDrags.get(webContents), 9)

    assert.equal(await __test.prepareMarketFaceNativeDrag(webContents, 9), true)
    assert.ok(dragged)
    assert.equal(dragged.file.endsWith('.gif'), true)
    assert.equal(__test.state.marketFaceRequestedDrags.has(webContents), false)
  } finally {
    __test.state.qqDragBridgeEnabled = false
    __test.cleanupMarketFaceTempFiles()
  }
})
