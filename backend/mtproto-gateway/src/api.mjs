import { TLReader, TLWriter, VECTOR_CONSTRUCTOR } from './tl.mjs';

const C = Object.freeze({
  invokeWithLayer: 0xda9b0d0d,
  invokeAfterMsg: 0xcb9f372d,
  invokeWithGooglePlayIntegrity: 0x1df92984,
  invokeWithReCaptcha: 0xadbb0f94,
  initConnection: 0xc1cd5ea9,
  inputClientProxy: 0x75588b3f,
  jsonObject: 0x99c1d49d,
  jsonObjectValue: 0xc0de1bd9,
  jsonBool: 0xc7345e6a,
  jsonNull: 0x3f6d7b68,
  jsonString: 0xb71e767a,
  jsonArray: 0xf7444763,
  jsonNumber: 0x2be0dfa4,
  helpGetConfig: 0xc4f9186b,
  ping: 0x7abe77ec,
  pingDelayDisconnect: 0xf3427b8c,
  getFutureSalts: 0xb921bd04,
  rpcResult: 0xf35c6d01,
  rpcError: 0x2144ca19,
  pong: 0x347773c5,
  futureSalts: 0xae500895,
  futureSalt: 0x0949d9dc,
  config: 0xcc1a241e,
  dcOption: 0x18b7a10d,
});

function skipJsonValue(reader, constructor, depth = 0) {
  if (depth > 24) throw new Error('initConnection JSON nesting limit exceeded');
  switch (constructor) {
    case C.jsonBool: {
      const boolConstructor = reader.uint32();
      if (boolConstructor !== 0x997275b5 && boolConstructor !== 0xbc799737) {
        throw new Error('Invalid JSON bool constructor');
      }
      return;
    }
    case C.jsonNull:
      return;
    case C.jsonString:
      reader.string(64 * 1024);
      return;
    case C.jsonNumber:
      reader.double();
      return;
    case C.jsonArray: {
      if (reader.uint32() !== VECTOR_CONSTRUCTOR) throw new Error('Invalid TL JSON array vector');
      const count = reader.int32();
      if (count < 0 || count > 4096) throw new Error('TL JSON array too large');
      for (let i = 0; i < count; i += 1) skipJsonValue(reader, reader.uint32(), depth + 1);
      return;
    }
    case C.jsonObject: {
      if (reader.uint32() !== VECTOR_CONSTRUCTOR) throw new Error('Invalid TL JSON object vector');
      const count = reader.int32();
      if (count < 0 || count > 4096) throw new Error('TL JSON object too large');
      for (let i = 0; i < count; i += 1) {
        if (reader.uint32() !== C.jsonObjectValue) throw new Error('Invalid TL JSON object-value constructor');
        reader.string(16 * 1024);
        skipJsonValue(reader, reader.uint32(), depth + 1);
      }
      return;
    }
    default:
      throw new Error(`Unsupported initConnection JSON type 0x${constructor.toString(16)}`);
  }
}

function unwrapQuery(buffer) {
  let query = Buffer.from(buffer);
  let layer = null;
  let initialized = false;

  for (let depth = 0; depth < 12; depth += 1) {
    const reader = new TLReader(query);
    const constructor = reader.uint32();

    if (constructor === C.invokeWithLayer) {
      layer = reader.int32();
      query = query.subarray(reader.offset);
      continue;
    }
    if (constructor === C.invokeAfterMsg) {
      reader.int64();
      query = query.subarray(reader.offset);
      continue;
    }
    if (constructor === C.invokeWithGooglePlayIntegrity) {
      reader.string(64 * 1024);
      reader.string(64 * 1024);
      query = query.subarray(reader.offset);
      continue;
    }
    if (constructor === C.invokeWithReCaptcha) {
      reader.string(64 * 1024);
      query = query.subarray(reader.offset);
      continue;
    }
    if (constructor === C.initConnection) {
      const flags = reader.int32();
      reader.int32(); // api_id; deliberately not logged or retained
      reader.string(4096); // device_model
      reader.string(4096); // system_version
      reader.string(4096); // app_version
      reader.string(4096); // system_lang_code
      reader.string(4096); // lang_pack
      reader.string(4096); // lang_code
      if (flags & 1) {
        if (reader.uint32() !== C.inputClientProxy) throw new Error('Invalid initConnection proxy object');
        reader.string(4096);
        reader.int32();
      }
      if (flags & 2) skipJsonValue(reader, reader.uint32());
      initialized = true;
      query = query.subarray(reader.offset);
      continue;
    }
    return { query, layer, initialized };
  }
  throw new Error('Too many nested MTProto query wrappers');
}

export function serializeConfig({ advertiseHost = '127.0.0.1', port = 5050, dcId = 1 } = {}) {
  const now = Math.floor(Date.now() / 1000);
  const dcOption = new TLWriter()
    .uint32(C.dcOption)
    .int32(16) // isStatic
    .int32(dcId)
    .string(advertiseHost)
    .int32(port)
    .toBuffer();

  return new TLWriter()
    .uint32(C.config)
    .int32(0) // no optional flags set
    .int32(now)
    .int32(now + 3600)
    .bool(false)
    .int32(dcId)
    .uint32(VECTOR_CONSTRUCTOR)
    .int32(1)
    .fixed(dcOption)
    .string('') // dc_txt_domain_name
    .int32(200) // chat_size_max
    .int32(200000) // megagroup_size_max
    .int32(100) // forwarded_count_max
    .int32(60000) // online_update_period_ms
    .int32(5000) // offline_blur_timeout_ms
    .int32(30000) // offline_idle_timeout_ms
    .int32(300000) // online_cloud_timeout_ms
    .int32(30000) // notify_cloud_delay_ms
    .int32(1500) // notify_default_delay_ms
    .int32(60000) // push_chat_period_ms
    .int32(2) // push_chat_limit
    .int32(172800) // edit_time_limit
    .int32(172800) // revoke_time_limit
    .int32(172800) // revoke_pm_time_limit
    .int32(2419200) // rating_e_decay
    .int32(20) // stickers_recent_limit
    .int32(604800) // channels_read_media_period
    .int32(20000) // call_receive_timeout_ms
    .int32(90000) // call_ring_timeout_ms
    .int32(30000) // call_connect_timeout_ms
    .int32(10000) // call_packet_timeout_ms
    .string('') // me_url_prefix: intentionally no Telegram domain
    .int32(1024) // caption_length_max
    .int32(4096) // message_length_max
    .int32(dcId) // webfile_dc_id
    .toBuffer();
}

function makeFutureSalts(requestMessageId, requestedCount = 1, serverSalt = Buffer.alloc(8)) {
  const now = Math.floor(Date.now() / 1000);
  const count = Math.max(1, Math.min(10, requestedCount));
  const writer = new TLWriter()
    .uint32(C.futureSalts)
    .int64(requestMessageId)
    .int32(now)
    .uint32(VECTOR_CONSTRUCTOR)
    .int32(count);
  for (let i = 0; i < count; i += 1) {
    writer.uint32(C.futureSalt)
      .int32(now - 5)
      .int32(now + 1800 * (i + 1))
      .fixed(serverSalt, 8);
  }
  return writer.toBuffer();
}

export function dispatchApiRequest(buffer, requestMessageId, config) {
  const { query, layer, initialized } = unwrapQuery(buffer);
  const reader = new TLReader(query);
  const constructor = reader.uint32();

  if (constructor === C.helpGetConfig) {
    reader.expectEnd();
    return {
      method: 'help.getConfig',
      layer,
      initialized,
      result: serializeConfig(config),
    };
  }

  if (constructor === C.ping || constructor === C.pingDelayDisconnect) {
    const pingId = reader.int64();
    if (constructor === C.pingDelayDisconnect) reader.int32();
    reader.expectEnd();
    const result = new TLWriter()
      .uint32(C.pong)
      .int64(requestMessageId)
      .int64(pingId)
      .toBuffer();
    return { method: constructor === C.ping ? 'ping' : 'ping_delay_disconnect', layer, initialized, result };
  }

  if (constructor === C.getFutureSalts) {
    const count = reader.int32();
    reader.expectEnd();
    return {
      method: 'get_future_salts',
      layer,
      initialized,
      result: makeFutureSalts(requestMessageId, count, config.serverSalt),
    };
  }

  const error = new TLWriter()
    .uint32(C.rpcError)
    .int32(400)
    .string('METHOD_NOT_IMPLEMENTED')
    .toBuffer();
  return { method: 'unsupported', layer, initialized, result: error };
}

export function serializeRpcResult(requestMessageId, result) {
  return new TLWriter()
    .uint32(C.rpcResult)
    .int64(requestMessageId)
    .fixed(result)
    .toBuffer();
}

export const API_CONSTRUCTORS = C;
