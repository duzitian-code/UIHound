window.fixtureRequests = [];
window.fixtureErrors = [];
window.fixtureConnected = false;
window.fixtureSaveFails = false;
window.fixtureConfirm = true;
window.confirm = () => window.fixtureConfirm;
window.fixtureMedia = { snapshot: [], recording: [] };
localStorage.setItem('uihound-snapshot-directory', 'D:\\old-project\\snapshots');
localStorage.setItem('uihound-recording-directory', 'D:\\old-project\\recordings');
window.addEventListener('error', (event) => fixtureErrors.push(event.message));
window.addEventListener('unhandledrejection', (event) => fixtureErrors.push(String(event.reason)));
const fixtureElement = {
  xpath: '//*[@resource-id="app:id/settings"]', u2Selector: 'resourceId="app:id/settings"',
  className: 'android.widget.TextView', resourceId: 'app:id/settings',
  text: '设置', contentDescription: 'Settings', packageName: 'com.example.device',
  bounds: { left: 80, top: 280, right: 1000, bottom: 460 },
  x: 80, y: 280, width: 920, height: 180, centerX: 540, centerY: 370,
  clickable: true, enabled: true, checked: false, children: [],
};
const fixtureTree = {
  xpath: '/hierarchy[1]', nodeName: 'hierarchy',
  children: [{ xpath: '/hierarchy[1]/*[1]', className: 'android.widget.FrameLayout', children: [
    fixtureElement,
    ...Array.from({length: 30}, (_, index) => ({
      ...fixtureElement, xpath: `/hierarchy[1]/*[1]/*[${index + 2}]`,
      text: `设备控件 ${index + 1}`, resourceId: `com.example.very.long.application.resource:id/vehicle_control_${index}`,
      className: 'com.example.very.long.application.widgets.VehicleConfigurationSettingsItem',
    })),
  ]}],
};
const fixtureImage = document.createElement('canvas');
fixtureImage.width = 1080;
fixtureImage.height = 2400;
const fixtureContext = fixtureImage.getContext('2d');
fixtureContext.fillStyle = '#172c42';
fixtureContext.fillRect(0, 0, 1080, 2400);
fixtureContext.fillStyle = '#c6dbef';
fixtureContext.font = '36px sans-serif';
fixtureContext.fillText('10:30                         Android', 65, 80);
fixtureContext.font = 'bold 76px sans-serif';
fixtureContext.fillText('设备设置', 75, 220);
['连接与网络', '显示与亮度', '声音与通知', '系统与更新', '开发者选项', '无障碍设置', '关于设备'].forEach((text, index) => {
  const top = 300 + index * 240;
  fixtureContext.fillStyle = '#233f59';
  fixtureContext.fillRect(60, top, 960, 185);
  fixtureContext.fillStyle = '#adcee9';
  fixtureContext.font = '44px sans-serif';
  fixtureContext.fillText(text, 105, top + 110);
  fixtureContext.fillStyle = '#668cae';
  fixtureContext.fillText('›', 930, top + 110);
});
fixtureImage.close = () => {};
window.WebSocket = class extends EventTarget {
  static OPEN = 1;
  static CLOSED = 3;
  constructor() { super(); this.readyState = 1; setTimeout(() => this.dispatchEvent(new Event('open')), 0); }
  send(value) { fixtureRequests.push({url: 'ws', data: JSON.parse(value)}); }
  close() { this.readyState = 3; }
};
window.fetch = async (url, options = {}) => {
  const data = options.body instanceof Blob ? options.body : options.body ? JSON.parse(options.body) : null;
  fixtureRequests.push({url, data});
  let result;
  if (url === '/device/status') result = {connected: fixtureConnected, serial: 'demo-device'};
  else if (url === '/internal/scanAdb') result = {devices: [{serial: 'demo-device'}]};
  else if (url === '/internal/settings' || url === '/internal/settings/snapshot-directory') result = {snapshot_directory: 'D:\\snapshots', recording_directory: 'D:\\recordings'};
  else if (url.startsWith('/internal/media/list?')) {
    const params = new URL(url, location.href).searchParams;
    result = {files: fixtureMedia[params.get('kind')], directory: params.get('directory')};
  }
  else if (url === '/internal/media/directory') {
    if (data.path.startsWith('D:\\old-project\\')) {
      return {ok: false, status: 400, json: async () => ({detail: '路径超出允许的媒体根目录'})};
    }
    result = {directory: data.path};
  }
  else if (url === '/internal/media/delete') {
    fixtureMedia[data.kind] = fixtureMedia[data.kind].filter(file => !data.paths.includes(file.path));
    result = {deleted: data.paths, errors: []};
  }
  else if (url.startsWith('/internal/recordings/save?')) {
    if (fixtureSaveFails) return {ok: false, json: async () => ({detail: 'simulated video save failure'})};
    window.fixtureVideo = data;
    result = {path: 'D:\\recordings\\record.webm', name: 'record.webm', url: URL.createObjectURL(data)};
    fixtureMedia.recording.unshift(result);
  }
  else if (url === '/internal/connect') { fixtureConnected = true; result = {connected: true, serial: 'demo-device'}; }
  else if (url === '/internal/disconnect') { fixtureConnected = false; result = {message: 'disconnected'}; }
  else if (url === '/internal/capture') result = {
    tree: data.hierarchy ? fixtureTree : null,
    elements: data.hierarchy ? [fixtureElement] : [], empty: !data.hierarchy,
    snapshot_id: `snapshot-${fixtureRequests.length}`, captured_at: new Date().toISOString(),
    image: fixtureImage.toDataURL('image/png').split(',')[1], width: 1080, height: 2400,
  };
  else if (url === '/internal/capture/find') result = {count: 1, elements: [fixtureElement]};
  else if (url === '/internal/capture/save') {
    if (fixtureSaveFails) return {ok: false, json: async () => ({detail: 'simulated save failure'})};
    result = {path: `D:\\snapshots\\capture-${fixtureRequests.length}.png`, image: fixtureImage.toDataURL('image/png').split(',')[1]};
    fixtureMedia.snapshot.unshift(result);
  }
  else if (url === '/internal/tree') result = {tree: fixtureTree, elements: [fixtureElement], empty: false};
  else if (url === '/internal/inspect') result = fixtureElement;
  else if (url === '/internal/find') result = {count: 1, elements: [fixtureElement]};
  else if (url.startsWith('/snapshot/')) result = {path: 'D:\\snapshots\\capture.png', image: fixtureImage.toDataURL('image/png').split(',')[1]};
  else if (url.startsWith('/internal/')) result = {message: '完成'};
  else throw Error(`Unexpected URL: ${url}`);
  return {ok: true, json: async () => result};
};
Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => {window.fixtureCopied = text;}}});
