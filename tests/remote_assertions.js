async function testRemoteTransport() {
  const NativeRemote = window.ScrcpyRemote;
  const NativeDecoder = window.VideoDecoder;
  const NativeChunk = window.EncodedVideoChunk;
  const NativeSocket = window.WebSocket;
  const packets = [];
  let decoded = [];
  let config;
  class FakeDecoder {
    constructor(callbacks) { this.callbacks = callbacks; this.state = 'unconfigured'; this.decodeQueueSize = 0; }
    configure(value) { config = value; this.state = 'configured'; }
    decode(chunk) { decoded.push(chunk); this.callbacks.output({close() {}}); }
    close() { this.state = 'closed'; }
  }

  window.testNativeH264Decoder = async function () {
    if (!window.VideoEncoder || !window.VideoDecoder) throw Error('Edge WebCodecs unavailable');
    const encoderConfig = {codec: 'avc1.42001E', width: 64, height: 64, bitrate: 200000, framerate: 30, avc: {format: 'annexb'}};
    const support = await VideoEncoder.isConfigSupported(encoderConfig);
    if (!support.supported) throw Error('Edge H.264 encoder unavailable for native round trip');
    const image = document.createElement('canvas');
    image.width = image.height = 64;
    const ctx = image.getContext('2d');
    ctx.fillStyle = '#f02030'; ctx.fillRect(0, 0, 64, 64);
    const chunks = [];
    let encoderError;
    const encoder = new VideoEncoder({output: chunk => chunks.push(chunk), error: error => { encoderError = error; }});
    encoder.configure(encoderConfig);
    const frame = new VideoFrame(image, {timestamp: 1000});
    encoder.encode(frame, {keyFrame: true});
    frame.close();
    await Promise.race([encoder.flush(), new Promise((_, reject) => setTimeout(() => reject(Error('H264 encode timeout')), 5000))]);
    encoder.close();
    if (encoderError) throw encoderError;
    assert(chunks.length > 0, 'native encoder emitted H264');
    let decoded = 0;
    const remote = new window.ScrcpyRemote({
      frame: output => { assert(output.displayWidth === 64 && output.displayHeight === 64, 'native decoded dimensions'); decoded++; output.close(); },
      status: (message, error) => { if (error) encoderError = Error(message); },
    });
    remote.stopped = false;
    remote.configure({codec: encoderConfig.codec, width: 64, height: 64});
    for (const chunk of chunks) {
      const packet = new ArrayBuffer(9 + chunk.byteLength);
      const view = new DataView(packet);
      view.setUint8(0, chunk.type === 'key' ? 1 : 0);
      view.setBigUint64(1, BigInt(chunk.timestamp));
      chunk.copyTo(new Uint8Array(packet, 9));
      remote.decode(packet);
    }
    await Promise.race([remote.decoder.flush(), new Promise((_, reject) => setTimeout(() => reject(Error('H264 decode timeout')), 5000))]);
    remote.stop();
    if (encoderError) throw encoderError;
    assert(decoded > 0, 'real H264 frames decoded through remote client');
  };
  class FakeSocket extends EventTarget {
    static OPEN = 1;
    static CLOSING = 2;
    constructor() { super(); this.readyState = 1; packets.push(this); }
    send(value) { this.sent = JSON.parse(value); }
    close() { this.readyState = 3; }
  }
  try {
    window.VideoDecoder = FakeDecoder;
    window.EncodedVideoChunk = class { constructor(value) { Object.assign(this, value); } };
    window.WebSocket = FakeSocket;
    const messages = [];
    const remote = new NativeRemote({frame() {}, status(message) { messages.push(message); }});
    remote.start();
    packets[0].dispatchEvent(new MessageEvent('message', {data: JSON.stringify({type: 'config', codec: 'avc1.42E01E', width: 1080, height: 2400})}));
    assert(config.codec === 'avc1.42E01E' && config.optimizeForLatency, 'decoder H264 configured for low latency');
    const packet = new ArrayBuffer(14);
    const view = new DataView(packet);
    view.setUint8(0, 1); view.setBigUint64(1, 123456n);
    new Uint8Array(packet, 9).set([0, 0, 0, 1, 0x65]);
    packets[0].dispatchEvent(new MessageEvent('message', {data: packet}));
    assert(decoded.length === 1 && decoded[0].timestamp === 123456 && decoded[0].type === 'key', 'binary keyframe timestamp framing');
    assert(remote.ready, 'controls enabled only after first decoded frame');
    remote.send({type: 'key', key: 'home'});
    assert(packets[0].sent.key === 'home', 'control JSON forwarded');
    remote.stop();
    assert(!remote.ready && packets[0].readyState === 3 && remote.decoder === null, 'stop closes decoder and transport');
    remote.start();
    packets[1].dispatchEvent(new MessageEvent('message', {data: JSON.stringify({type: 'error', message: 'missing server'})}));
    assert(messages.includes('missing server') && !remote.ready, 'server error surfaced without JPEG fallback');
    remote.stop();
  } finally {
    window.VideoDecoder = NativeDecoder;
    window.EncodedVideoChunk = NativeChunk;
    window.WebSocket = NativeSocket;
  }
}
