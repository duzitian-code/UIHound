// Minimal WebM muxing for one VP8 video track. Each keyframe begins a seekable cluster.
const WebM = {
  bytes(value) {
    let hex = value.toString(16);
    if (hex.length % 2) hex = `0${hex}`;
    return Uint8Array.from(hex.match(/../g), byte => parseInt(byte, 16));
  },
  size(value) {
    let length = 1;
    while (value >= 2 ** (7 * length) - 1) length++;
    let encoded = BigInt(value) | (1n << BigInt(7 * length));
    const bytes = new Uint8Array(length);
    for (let i = length - 1; i >= 0; i--) {
      bytes[i] = Number(encoded & 255n);
      encoded >>= 8n;
    }
    return bytes;
  },
  element(id, payload) {
    const body = payload instanceof Blob ? payload : new Blob(
      Array.isArray(payload) ? payload : [typeof payload === 'string' ? new TextEncoder().encode(payload) : payload],
    );
    return new Blob([this.bytes(id), this.size(body.size), body]);
  },
  integer(id, value) { return this.element(id, this.bytes(value)); },
  float(id, value) {
    const bytes = new Uint8Array(8);
    new DataView(bytes.buffer).setFloat64(0, value);
    return this.element(id, bytes);
  },
  video(clusters, width, height, duration) {
    const header = this.element(0x1a45dfa3, [
      this.integer(0x4286, 1), this.integer(0x42f7, 1), this.integer(0x42f2, 4),
      this.integer(0x42f3, 8), this.element(0x4282, 'webm'),
      this.integer(0x4287, 4), this.integer(0x4285, 2),
    ]);
    const info = this.element(0x1549a966, [
      this.integer(0x2ad7b1, 1_000_000), this.float(0x4489, duration),
      this.element(0x4d80, 'UiHound'), this.element(0x5741, 'UiHound'),
    ]);
    const tracks = this.element(0x1654ae6b, [this.element(0xae, [
      this.integer(0xd7, 1), this.integer(0x73c5, 1), this.integer(0x83, 1),
      this.element(0x86, 'V_VP8'), this.integer(0x23e383, 33_333_333),
      this.element(0xe0, [this.integer(0xb0, width), this.integer(0xba, height)]),
    ])]);
    return new Blob([header, this.element(0x18538067, [info, tracks, ...clusters])], { type: 'video/webm' });
  },
};

class ScreenRecorder {
  constructor({ notify, changed, saved }) {
    Object.assign(this, { notify, changed, saved });
    this.canvas = document.createElement('canvas');
    this.pending = null;
    this.active = false;
  }

  async start(frame, directory, isCurrent = () => true) {
    if (this.pending || this.unsaved) throw Error('录屏正在保存或有尚未保存的视频');
    if (!window.VideoEncoder || !window.VideoFrame) throw Error('浏览器不支持录屏，请使用新版 Chrome / Edge，通过 localhost 或 HTTPS 打开');
    if (!directory.trim()) throw Error('请设置录屏保存目录');
    this.canvas.width = frame.displayWidth || frame.width;
    this.canvas.height = frame.displayHeight || frame.height;
    this.draw(frame, true);
    const encoderConfig = {
      codec: 'vp8', width: this.canvas.width, height: this.canvas.height,
      bitrate: 8_000_000, framerate: 30, latencyMode: 'realtime',
    };
    const support = await VideoEncoder.isConfigSupported(encoderConfig);
    if (!support.supported) throw Error('浏览器不支持 VP8 录屏编码');
    if (!isCurrent()) throw Error('设备或画面模式已变化，已取消开始录屏');
    this.directory = directory;
    this.clusters = [];
    this.blocks = [];
    this.bytes = 0;
    this.failure = null;
    this.lastKey = -Infinity;
    this.lastTime = 0;
    this.pending = new Promise(resolve => { this.resolve = resolve; });
    try {
      this.encoder = new VideoEncoder({
        output: chunk => this.append(chunk),
        error: error => { this.failure = error.message; this.stop(); },
      });
      this.encoder.configure(encoderConfig);
      this.clock = performance.now();
      this.active = true;
      this.pump();
      this.frameTimer = setInterval(() => this.pump(), 1000 / 30);
      this.timer = setInterval(() => this.changed('recording', performance.now() - this.clock), 500);
      this.changed('recording', 0);
    } catch (error) {
      this.active = false;
      if (this.encoder?.state !== 'closed') this.encoder?.close();
      this.pending = null;
      this.resolve();
      throw error;
    }
  }

  draw(frame, force = false) {
    if (!force && !this.active) return;
    const width = frame.displayWidth || frame.width;
    const height = frame.displayHeight || frame.height;
    const scale = Math.min(this.canvas.width / width, this.canvas.height / height);
    const context = this.canvas.getContext('2d');
    context.fillStyle = '#000';
    context.fillRect(0, 0, this.canvas.width, this.canvas.height);
    context.drawImage(frame, (this.canvas.width - width * scale) / 2,
      (this.canvas.height - height * scale) / 2, width * scale, height * scale);
  }

  pump() {
    if (!this.active || this.encoder.encodeQueueSize > 2) return;
    const timestamp = Math.round((performance.now() - this.clock) * 1000);
    const keyFrame = timestamp - this.lastKey >= 2_000_000;
    const frame = new VideoFrame(this.canvas, { timestamp, duration: 33_333 });
    try {
      this.encoder.encode(frame, { keyFrame });
      if (keyFrame) this.lastKey = timestamp;
    } catch (error) {
      this.failure = error.message;
      this.stop();
    } finally { frame.close(); }
  }

  append(chunk) {
    const time = Math.round(chunk.timestamp / 1000);
    if (chunk.type === 'key' || !this.blocks.length) {
      this.flushCluster();
      this.clusterTime = time;
    }
    const payload = new Uint8Array(chunk.byteLength + 4);
    payload[0] = 0x81;
    new DataView(payload.buffer).setInt16(1, time - this.clusterTime);
    payload[3] = chunk.type === 'key' ? 0x80 : 0;
    chunk.copyTo(payload.subarray(4));
    this.blocks.push(WebM.element(0xa3, payload));
    this.bytes += payload.length + 12;
    this.lastTime = time + 34;
    if (this.bytes >= 240 * 1024 * 1024 && this.active) {
      this.notify('录屏接近容量上限，已自动停止并保存');
      this.stop();
    }
  }

  flushCluster() {
    if (this.blocks.length) this.clusters.push(WebM.element(0x1f43b675, [
      WebM.integer(0xe7, this.clusterTime), ...this.blocks,
    ]));
    this.blocks = [];
  }

  stop() {
    if (this.active) {
      this.endTime = Math.round((performance.now() - this.clock) * 1000);
      this.active = false;
      clearInterval(this.frameTimer);
      clearInterval(this.timer);
      this.changed('saving');
      this.finish();
    }
    return this.pending || Promise.resolve();
  }

  async finish() {
    let blob;
    try {
      if (this.failure) throw Error(this.failure);
      await this.encoder.flush();
      const finalFrame = new VideoFrame(this.canvas, { timestamp: this.endTime, duration: 33_333 });
      try { this.encoder.encode(finalFrame, { keyFrame: true }); } finally { finalFrame.close(); }
      await this.encoder.flush();
      if (!this.bytes) throw Error('录屏没有生成有效视频');
      this.flushCluster();
      blob = WebM.video(this.clusters, this.canvas.width, this.canvas.height, this.lastTime);
      await this.upload(blob);
    } catch (error) {
      this.unsaved = blob || null;
      this.notify(`录屏保存失败：${error.message}${blob ? '；可在录屏页重试保存' : '；请重新录制'}`, true);
    } finally {
      if (this.encoder.state !== 'closed') this.encoder.close();
      this.encoder = null;
      this.clusters = [];
      this.blocks = [];
      this.pending = null;
      this.changed(this.unsaved ? 'unsaved' : 'idle');
      this.resolve();
    }
  }

  async upload(blob = this.unsaved) {
    if (!blob) return;
    const response = await fetch(`/internal/recordings/save?directory=${encodeURIComponent(this.directory)}`, {
      method: 'POST', headers: { 'Content-Type': 'video/webm' }, body: blob,
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.detail || '保存视频失败');
    this.unsaved = null;
    await this.saved(result);
  }
}
window.ScreenRecorder = ScreenRecorder;
