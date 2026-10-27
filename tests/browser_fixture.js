window.fixtureRequests = [];
window.fixtureErrors = [];
window.fixtureConnected = false;
window.fixtureDisplayId = 0;
window.fixtureArchives = [];
window.fixtureSettings = {
  project_directory: 'D:\\UiHound', snapshot_directory: 'D:\\snapshots', recording_directory: 'D:\\recordings',
  inspection_directory: 'D:\\UiHound\\snapshots', import_directory: 'D:\\UiHound\\snapshots', overrides: {},
};
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
  else if (url === '/internal/settings' || url === '/internal/settings/snapshot-directory') result = fixtureSettings;
  else if (url === '/internal/settings/directories') {
    const root = data.project_directory;
    fixtureSettings = {
      project_directory: root,
      snapshot_directory: data.snapshot_directory || `${root}\\screenshots`,
      recording_directory: data.recording_directory || `${root}\\recordings`,
      inspection_directory: data.inspection_directory || `${root}\\snapshots`,
      import_directory: data.import_directory || data.inspection_directory || `${root}\\snapshots`,
      overrides: data,
    };
    result = fixtureSettings;
  }
  else if (url === '/internal/capture/select-file') result = {
    cancelled: false, name: data.kind === 'image' ? 'screen.png' : 'hierarchy.xml',
    content: data.kind === 'image' ? fixtureImage.toDataURL('image/png').split(',')[1] : '<hierarchy><node display-id="3"/></hierarchy>',
  };
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
  else if (url === '/internal/connect') { fixtureConnected = true; fixtureDisplayId = 0; result = {connected: true, serial: 'demo-device'}; }
  else if (url === '/internal/displays') result = {displays: [0, 3], display_id: fixtureDisplayId};
  else if (url === '/internal/display') { fixtureDisplayId = data.display_id; result = {display_id: data.display_id}; }
  else if (url === '/internal/disconnect') { fixtureConnected = false; result = {message: 'disconnected'}; }
  else if (url.startsWith('/internal/capture/archives?')) result = {
    directory: new URL(url, location.href).searchParams.get('directory') || 'D:\\snapshots\\inspections', files: fixtureArchives,
  };
  else if (url === '/internal/capture/archive/save') {
    result = {directory: data.directory || fixtureSettings.inspection_directory, path: `${fixtureSettings.inspection_directory}\\capture_demo`, name: 'capture_demo'};
    fixtureArchives.push(result);
  }
  else if (url === '/internal/capture/archive/load') result = {
    tree: fixtureTree, elements: [fixtureElement], empty: false, node_count: 34,
    snapshot_id: `snapshot-${fixtureRequests.length}`, display_id: 3,
    image: fixtureImage.toDataURL('image/png').split(',')[1], width: 1080, height: 2400,
    xml: '<hierarchy><node display-id="3"/></hierarchy>',
  };
  else if (url === '/internal/capture' || url === '/internal/capture/import') result = {
    tree: data.hierarchy || data.xml ? fixtureTree : null,
    elements: data.hierarchy || data.xml ? [fixtureElement] : [], empty: !(data.hierarchy || data.xml),
    snapshot_id: `snapshot-${fixtureRequests.length}`, captured_at: new Date().toISOString(),
    node_count: data.hierarchy || data.xml ? 34 : 0,
    display_id: data.display_id ?? fixtureDisplayId,
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
