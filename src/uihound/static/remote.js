class ScrcpyRemote {
  constructor({ frame, status }) {
    this.onFrame = frame;
    this.onStatus = status;
    this.socket = null;
    this.decoder = null;
    this.ready = false;
    this.stopped = true;
    this.waitingKeyframe = true;
  }

  start() {
    this.stop();
    this.stopped = false;
    if (!window.VideoDecoder || !window.EncodedVideoChunk) {
      this.fail('当前浏览器不支持 H.264 WebCodecs；请用新版 Chrome / Edge，通过 localhost 或 HTTPS 打开');
      return;
    }
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${protocol}//${location.host}/internal/ws/remote`);
    this.socket = socket;
    socket.binaryType = 'arraybuffer';
    socket.addEventListener('message', event => {
      if (this.socket !== socket || this.stopped) return;
      try {
        if (typeof event.data === 'string') {
          const message = JSON.parse(event.data);
          if (message.type === 'error') return this.fail(message.message);
          if (message.type === 'status') this.onStatus(message.message);
          if (message.type === 'config') this.configure(message);
          return;
        }
        this.decode(event.data);
      } catch (error) {
        this.fail(`scrcpy 视频处理失败：${error.message}`);
      }
    });
    socket.addEventListener('error', () => {
      if (this.socket === socket && !this.stopped) this.fail('scrcpy 连接失败，请检查 ADB 和服务日志');
    });
    socket.addEventListener('close', () => {
      if (this.socket === socket && !this.stopped) this.fail('scrcpy 已断开，点击 remote 重新连接');
    });
  }

  configure(message) {
    if (this.decoder?.state !== 'closed') this.decoder?.close();
    this.waitingKeyframe = true;
    this.decoder = new VideoDecoder({
      output: frame => {
        if (this.stopped) { frame.close(); return; }
        this.ready = true;
        this.onFrame(frame);
      },
      error: error => this.fail(`H.264 解码失败：${error.message}`),
    });
    this.decoder.configure({
      codec: message.codec,
      codedWidth: message.width,
      codedHeight: message.height,
      optimizeForLatency: true,
      hardwareAcceleration: 'no-preference',
    });
    this.onStatus('scrcpy · H.264');
  }

  decode(buffer) {
    if (!this.decoder || this.decoder.state !== 'configured') throw Error('缺少 H.264 配置');
    const view = new DataView(buffer);
    if (buffer.byteLength < 10) throw Error('视频帧不完整');
    const keyframe = Boolean(view.getUint8(0) & 1);
    if (this.waitingKeyframe && !keyframe) return;
    // Never skip a delta frame inside a GOP; reconnect rather than accumulating unbounded latency.
    if (this.decoder.decodeQueueSize > 90) throw Error('解码积压，请降低 scrcpy 分辨率或帧率后重试');
    const timestamp = Number(view.getBigUint64(1, false));
    this.decoder.decode(new EncodedVideoChunk({
      type: keyframe ? 'key' : 'delta', timestamp,
      data: new Uint8Array(buffer, 9),
    }));
    this.waitingKeyframe = false;
  }

  send(message) {
    if (!this.ready || this.socket?.readyState !== WebSocket.OPEN) {
      this.onStatus('scrcpy 尚未就绪，请等待首帧或重新连接', true);
      return false;
    }
    this.socket.send(JSON.stringify(message));
    return true;
  }

  fail(message) {
    this.stop();
    this.onStatus(message, true);
  }

  stop() {
    this.stopped = true;
    this.ready = false;
    const socket = this.socket;
    this.socket = null;
    if (socket && socket.readyState < WebSocket.CLOSING) socket.close();
    if (this.decoder && this.decoder.state !== 'closed') this.decoder.close();
    this.decoder = null;
  }
}

window.ScrcpyRemote = ScrcpyRemote;
