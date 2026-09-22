import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
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
  } finally {
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
