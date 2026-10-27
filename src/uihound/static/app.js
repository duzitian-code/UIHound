const $ = (selector) => document.querySelector(selector);
const state = {
  connected: false,
  width: 0,
  height: 0,
  hierarchy: [],
  tree: null,
  treeExpanded: new Set(),
  matches: [],
  selected: null,
  locatorMode: true,
  regionMode: false,
  region: null,
  pointerStart: null,
  frozen: false,
  socket: null,
  frame: null,
  frameSerial: 0,
  streamRetry: null,
  resizing: null,
  searchQuery: '',
  connectionEpoch: 0,
  mode: 'hierarchy',
  snapshotId: null,
  captureSerial: 0,
  capturing: false,
  regionVersion: 0,
  savingRegion: false,
  remote: null,
  color: null,
  wheelGesture: null,
  showBounds: true,
  displayId: 0,
  importedFiles: null,
  lastInspection: null,
  directories: null,
  importSelection: {},
};

const layoutLimits = {
  preview: { min: 48, initial: Math.round(Math.max(1000, innerWidth) * .28) },
  inspector: { min: 48, initial: 340 },
};
const layoutWidths = {
  preview: layoutLimits.preview.initial,
  inspector: layoutLimits.inspector.initial,
};
Object.assign(layoutWidths, loadLayoutWidths());

const groups = [
  ['基础信息', ['packageName', 'className', 'resourceId', 'text', 'contentDescription', 'description']],
  ['位置尺寸', ['position', 'width', 'height', 'bounds', 'center']],
  ['状态属性', ['enabled', 'clickable', 'checkable', 'checked', 'longClickable', 'scrollable', 'focusable', 'focused', 'selected', 'password']],
];

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error('服务器返回了无效响应');
  }
  if (!response.ok) throw new Error(data.detail || `请求失败 (${response.status})`);
  return data;
}

const directoryFields = {
  project_directory: '#project-directory', snapshot_directory: '#snapshot-directory',
  recording_directory: '#recording-directory', inspection_directory: '#capture-directory',
  import_directory: '#import-directory',
};

function showDirectorySettings(settings) {
  for (const [key, selector] of Object.entries(directoryFields)) {
    $(selector).value = settings[key];
    $(selector).dataset.inherited = String(key !== 'project_directory' && !settings.overrides?.[key]);
  }
}

function updateInheritedDirectories() {
  const project = $('#project-directory').value.trim().replace(/[\\/]+$/, '');
  const separator = project.includes('\\') ? '\\' : '/';
  for (const [selector, child] of [['#snapshot-directory', 'screenshots'], ['#recording-directory', 'recordings'], ['#capture-directory', 'snapshots']]) {
    if ($(selector).dataset.inherited === 'true') $(selector).value = `${project}${separator}${child}`;
  }
  if ($('#import-directory').dataset.inherited === 'true') $('#import-directory').value = $('#capture-directory').value;
}

async function loadDirectorySettings() {
  state.directories = await api('/internal/settings');
  showDirectorySettings(state.directories);
}

function toast(message, error = /失败|异常|无效|无法|未找到|未安装|未启用|不存在/.test(message)) {
  const node = $('#toast');
  node.textContent = message;
  node.classList.toggle('error', error);
  node.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove('show'), 2400);
}

function loadLayoutWidths() {
  try {
    const saved = JSON.parse(localStorage.getItem('uihound-layout-v2') || '{}');
    return {
      preview: clampLayoutWidth('preview', saved.preview),
      inspector: clampLayoutWidth('inspector', saved.inspector),
    };
  } catch {
    return {
      preview: layoutLimits.preview.initial,
      inspector: layoutLimits.inspector.initial,
    };
  }
}

function clampLayoutWidth(panel, width) {
  const limits = layoutLimits[panel];
  const workspace = $('.workspace');
  const total = workspace ? workspace.clientWidth - 32 : innerWidth - 32;
  const reserved = panel === 'preview' ? layoutLimits.inspector.min : layoutWidths.preview;
  const max = Math.max(limits.min, total - reserved - 48);
  return Number.isFinite(width) ? Math.min(max, Math.max(limits.min, width)) : Math.min(max, limits.initial);
}

function applyLayoutWidths() {
  layoutWidths.preview = clampLayoutWidth('preview', layoutWidths.preview);
  layoutWidths.inspector = clampLayoutWidth('inspector', layoutWidths.inspector);
  layoutWidths.preview = clampLayoutWidth('preview', layoutWidths.preview);
  $('.workspace').style.setProperty('--preview-width', `${layoutWidths.preview}px`);
  $('.workspace').style.setProperty('--inspector-width', `${layoutWidths.inspector}px`);
  fitScreen();
}

function saveLayoutWidths() {
  try {
    localStorage.setItem('uihound-layout-v2', JSON.stringify(layoutWidths));
  } catch (error) {
    toast(`无法保存面板宽度：${error.message}`);
  }
}

function resizePanel(pointerX) {
  if (!state.resizing) return;
  const { panel, startX, startWidth } = state.resizing;
  const delta = pointerX - startX;
  layoutWidths[panel] = clampLayoutWidth(panel, startWidth + delta);
  applyLayoutWidths();
}

function bindPanelResize() {
  document.querySelectorAll('.resize-handle').forEach((handle) => {
    handle.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      const panel = handle.dataset.resize;
      state.resizing = {
        panel,
        startX: event.clientX,
        startWidth: layoutWidths[panel],
      };
      handle.classList.add('dragging');
      $('.workspace').classList.add('resizing');
      handle.setPointerCapture(event.pointerId);
    });
    handle.addEventListener('pointermove', (event) => {
      if (state.resizing) resizePanel(event.clientX);
    });
    const endResize = (event) => {
      if (!state.resizing) return;
      state.resizing = null;
      handle.classList.remove('dragging');
      $('.workspace').classList.remove('resizing');
      saveLayoutWidths();
      if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
    };
    handle.addEventListener('pointerup', endResize);
    handle.addEventListener('pointercancel', endResize);
    handle.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      const sign = event.key === 'ArrowRight' ? 1 : -1;
      const direction = sign;
      const panel = handle.dataset.resize;
      layoutWidths[panel] = clampLayoutWidth(panel, layoutWidths[panel] + direction * (event.shiftKey ? 30 : 10));
      applyLayoutWidths();
      saveLayoutWidths();
    });
  });
}

function setConnection(connected, serial = '') {
  state.connectionEpoch += 1;
  state.connected = connected;
  $('#connection-indicator').classList.toggle('online', connected);
  $('#connection-label').textContent = connected ? `已连接 ${serial}` : '未连接设备';
  $('#connection-label').title = serial;
  $('#disconnect-device').disabled = !connected;
  $('#display-id').disabled = !connected && !state.importedFiles;
  document.querySelectorAll('[data-device]').forEach((button) => { button.disabled = !connected; });
  if (!connected && state.mode === 'remote' && state.lastInspection) {
    state.remote?.stop();
    state.remote = null;
    state.recorder?.stop();
    state.captureSerial += 1;
    showInspection(state.lastInspection).then(() => {
      $('#stream-status').textContent = '离线快照 · 保留最近层级画面';
    }).catch(error => toast(`恢复快照失败：${error.message}`, true));
    return;
  }
  if (!connected && state.snapshotId) {
    state.recorder?.stop();
    cancelPointer();
    state.remote?.stop();
    state.remote = null;
    state.captureSerial += 1;
    state.capturing = false;
    $('#screen-empty').classList.add('hidden');
    $('#screen-wrap').classList.remove('hidden');
    $('#stream-status').textContent = '离线快照 · 可继续分析';
    updateOfflineControls();
    return;
  }
  $('#screen-empty').classList.toggle('hidden', connected);
  $('#screen-wrap').classList.toggle('hidden', !connected);
  if (!connected) {
    state.recorder?.stop();
    cancelPointer();
    stopScreenStream();
    state.snapshotId = null;
    state.captureSerial += 1;
    state.capturing = false;
    state.mode = 'hierarchy';
    state.locatorMode = true;
    state.color?.reset();
    state.hierarchy = [];
    state.tree = null;
    state.treeExpanded.clear();
    state.matches = [];
    state.selected = null;
    state.pointerStart = null;
    clearRegion();
    state.regionMode = false;
    updateRegionMode();
    renderHierarchy();
    renderResults();
    $('#tree-node-count').textContent = '0';
    $('#tree-empty').textContent = '连接设备后查看 UI 层级结构';
    $('#tree-empty').classList.remove('error');
    $('#element-detail').replaceChildren();
    $('#detail-subtitle').textContent = '连接设备后选择元素';
    $('#click-element').disabled = true;
    $('#save-element').disabled = true;
    $('#accessibility-status').textContent = '无障碍：待连接';
    $('#accessibility-status').classList.remove('error');
    $('#screen-size').textContent = '等待连接';
    $('#mouse-coordinate').textContent = '设备坐标：--';
    $('#screen-message').textContent = '';
    $('#search-count').textContent = '输入定位表达式查找';
    for (const id of ['screen-canvas', 'overlay-canvas']) {
      const canvas = $(`#${id}`);
      canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
    }
  }
  updateLocatorButton();
}

function updateOfflineControls() {
  $('#display-id').disabled = !state.connected && !state.importedFiles;
  for (const id of ['save-full', 'region-mode']) $(`#${id}`).disabled = !state.snapshotId;
  $('#save-element').disabled = !state.selected || !state.snapshotId;
  $('#search-form button').disabled = !state.snapshotId;
  for (const id of ['expand-tree', 'collapse-tree', 'locator-mode', 'color-mode']) {
    $(`#${id}`).disabled = !state.connected && !state.snapshotId;
  }
  $('#click-element').disabled = !state.connected || !state.selected;
}

async function showInspection(result) {
  const requestId = ++state.captureSerial;
  const epoch = state.connectionEpoch;
  const bytes = Uint8Array.from(atob(result.image), char => char.charCodeAt(0));
  const frame = await createImageBitmap(new Blob([bytes], {type: 'image/png'}));
  if (requestId !== state.captureSerial || epoch !== state.connectionEpoch) { frame.close(); return; }
  state.capturing = false;
  state.frozen = false;
  state.regionMode = false;
  clearRegion();
  state.snapshotId = result.snapshot_id;
  state.lastInspection = result;
  state.mode = 'hierarchy';
  state.locatorMode = true;
  state.displayId = result.display_id ?? state.displayId;
  $('#display-id').value = state.displayId;
  clearSelection();
  state.matches = [];
  state.color?.reset();
  renderFrame(frame);
  state.hierarchy = result.elements;
  state.tree = result.tree;
  state.treeExpanded = collectExpandedNodes(result.tree);
  renderHierarchy();
  renderResults();
  $('#tree-node-count').textContent = result.node_count.toLocaleString();
  $('#screen-empty').classList.add('hidden');
  $('#screen-wrap').classList.remove('hidden');
  activateTab('detail');
  updateLocatorButton();
  updateOfflineControls();
  updateOverlay();
}

async function applyDisplay() {
  const input = $('#display-id');
  if (input.disabled) return;
  const id = Number(input.value);
  if (!Number.isInteger(id) || id < 0 || id > 2147483647 || !input.value) {
    input.value = state.displayId;
    return toast('Display ID 必须是有效的非负整数', true);
  }
  input.disabled = true;
  const requestId = ++state.captureSerial;
  const epoch = state.connectionEpoch;
  try {
    await state.recorder?.stop();
    if (requestId !== state.captureSerial || epoch !== state.connectionEpoch) return;
    state.remote?.stop();
    state.remote = null;
    if (state.importedFiles) {
      const result = await api('/internal/capture/import', {
        method: 'POST', body: JSON.stringify({...state.importedFiles, display_id: id}),
      });
      if (requestId !== state.captureSerial || epoch !== state.connectionEpoch) return;
      await showInspection(result);
    } else {
      await api('/internal/display', {method: 'POST', body: JSON.stringify({display_id: id})});
      if (requestId !== state.captureSerial || epoch !== state.connectionEpoch) return;
      state.displayId = id;
      await switchMode(state.mode);
    }
  } catch (error) {
    input.value = state.displayId;
    toast(`切换屏幕失败：${error.message}`, true);
  } finally { input.disabled = !state.connected && !state.importedFiles; }
}

async function disconnect() {
  try {
    await state.recorder?.stop();
    await api('/internal/disconnect', { method: 'POST' });
    setConnection(false);
    toast('设备已断开');
  } catch (error) {
    toast(`断开失败：${error.message}`);
  }
}

async function scanDevices() {
  try {
    const { devices } = await api('/internal/scanAdb', { method: 'POST' });
    const select = $('#device-select');
    $('#device-options').replaceChildren(...devices.map(device => new Option(device.serial, device.serial)));
    if (!select.value && devices.length === 1) select.value = devices[0].serial;
    if (!devices.length) toast('未发现在线 ADB 设备');
  } catch (error) {
    toast(`ADB 扫描失败：${error.message}`);
  }
}

async function connect(serial) {
  if (!serial.trim()) return toast('请先选择或输入设备地址');
  $('#connect-device').disabled = true;
  try {
    await state.recorder?.stop();
    const status = await api('/internal/connect', {
      method: 'POST',
      body: JSON.stringify({ serial: serial.trim() }),
    });
    setConnection(false);
    setConnection(true, status.serial);
    state.importedFiles = null;
    state.lastInspection = null;
    await loadDisplays();
    $('#device-select').value = serial.trim();
    toast('设备连接成功');
    await switchMode('hierarchy');
  } catch (error) {
    setConnection(false);
    toast(`连接失败：${error.message}`);
  } finally {
    $('#connect-device').disabled = false;
  }
}

async function loadDisplays() {
  const displays = await api('/internal/displays');
  state.displayId = displays.display_id;
  setDisplayOptions(displays.displays, state.displayId);
}

function setDisplayOptions(ids, selected) {
  $('#display-id').replaceChildren(...ids.map(id => new Option(id === 0 ? '0（主屏）' : String(id), String(id))));
  $('#display-id').value = selected;
}

async function refreshScreen() {
  return switchMode(state.mode);
}

function configureScreen(width, height) {
  const canvas = $('#screen-canvas');
  const stage = $('#screen-stage');
  if (state.width !== width || state.height !== height) {
    state.width = width;
    state.height = height;
    canvas.width = width;
    canvas.height = height;
    $('#overlay-canvas').width = width;
    $('#overlay-canvas').height = height;
    clearRegion();
    $('#screen-size').textContent = `${width} × ${height}`;
  }
  stage.style.aspectRatio = `${width} / ${height}`;
  fitScreen();
  updateOverlay();
}

function fitScreen() {
  if (!state.width || !state.height) return;
  const wrap = $('#screen-wrap');
  const stage = $('#screen-stage');
  const surface = $('.screen-surface');
  const availableWidth = Math.max(1, surface.clientWidth - 8);
  const availableHeight = Math.max(1, surface.clientHeight - 8);
  const scale = Math.min(availableWidth / state.width, availableHeight / state.height, 1);
  stage.style.width = `${Math.floor(state.width * scale)}px`;
  stage.style.height = `${Math.floor(state.height * scale)}px`;
  wrap.style.width = `${Math.floor(state.width * scale)}px`;
  wrap.style.height = `${Math.floor(state.height * scale)}px`;
  renderRegionBox();
}

function renderFrame(frame) {
  if (state.frame && state.frame !== frame) state.frame.close();
  state.frame = frame;
  const width = frame.displayWidth || frame.width;
  const height = frame.displayHeight || frame.height;
  if (state.width !== width || state.height !== height) configureScreen(width, height);
  drawCanvas();
}

function stopScreenStream() {
  state.remote?.stop();
  state.remote = null;
  if (state.frame) {
    state.frame.close();
    state.frame = null;
  }
  state.width = 0;
  state.height = 0;
  state.frozen = false;
  $('#freeze-screen').textContent = '冻结画面';
  $('#freeze-screen').setAttribute('aria-pressed', 'false');
  $('#stream-status').textContent = '实时画面未连接';
  $('#stream-status').classList.remove('live', 'frozen');
}

async function loadHierarchy() {
  return switchMode('hierarchy');
}

async function switchMode(mode) {
  if (!state.connected) {
    if (state.snapshotId && mode !== 'remote') {
      state.mode = mode;
      state.locatorMode = mode === 'hierarchy';
      activateTab(mode === 'color' ? 'color' : 'detail');
      updateLocatorButton();
      updateOfflineControls();
      updateOverlay();
    }
    return;
  }
  const requestId = ++state.captureSerial;
  const epoch = state.connectionEpoch;
  await state.recorder?.stop();
  if (!state.connected || requestId !== state.captureSerial || epoch !== state.connectionEpoch) return;
  cancelPointer();
  state.remote?.stop();
  state.remote = null;
  state.mode = mode;
  state.locatorMode = mode === 'hierarchy';
  state.frozen = false;
  state.regionMode = false;
  state.pointerStart = null;
  clearRegion();
  updateLocatorButton();
  state.capturing = mode !== 'remote';
  $('#screen-message').textContent = '';
  if (mode === 'remote') {
    state.snapshotId = null;
    $('#stream-status').textContent = '正在启动 scrcpy…';
    state.remote = new window.ScrcpyRemote({
      frame: (frame) => {
        if (state.mode !== 'remote') { frame.close(); return; }
        state.recorder?.draw(frame);
        if (state.frozen) {
          state.frame?.close();
          state.frame = frame;
        } else renderFrame(frame);
      },
      status: (text, error = false) => {
        if (state.mode !== 'remote') return;
        $('#stream-status').textContent = text;
        $('#stream-status').classList.toggle('live', !error);
        if (error) { state.recorder?.stop(); $('#screen-message').textContent = text; toast(text, true); }
      },
    });
    state.remote.start();
    $('#click-element').disabled = true;
    $('#save-element').disabled = true;
    updateOverlay();
    return;
  }
  activateTab(mode === 'color' ? 'color' : 'detail');
  $('#stream-status').textContent = '正在获取快照…';
  try {
    const result = await api('/internal/capture', { method: 'POST', body: JSON.stringify({ hierarchy: mode === 'hierarchy' }) });
    if (!state.connected || epoch !== state.connectionEpoch || requestId !== state.captureSerial) return;
    const bytes = Uint8Array.from(atob(result.image), char => char.charCodeAt(0));
    const frame = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
    if (requestId !== state.captureSerial) { frame.close(); return; }
    state.lastInspection = result;
    state.displayId = result.display_id ?? state.displayId;
    $('#display-id').value = state.displayId;
    state.snapshotId = result.snapshot_id;
    clearSelection();
    state.matches = [];
    renderResults();
    state.color?.reset();
    renderFrame(frame);
    state.hierarchy = result.elements;
    state.tree = result.empty ? null : result.tree;
    state.treeExpanded = collectExpandedNodes(result.tree);
    renderHierarchy();
    $('#tree-node-count').textContent = countTreeNodes(result.tree).toLocaleString();
    $('#accessibility-status').textContent = mode === 'color' ? '颜色快照（不读取层级）' : result.empty ? '无障碍：UI 树为空' : '层级快照已获取';
    $('#accessibility-status').classList.toggle('error', result.empty && mode === 'hierarchy');
    $('#tree-empty').classList.toggle('error', result.empty);
    if (result.empty) $('#tree-empty').textContent = 'UI 树为空，请检查当前用户无障碍服务是否启用';
    $('#stream-status').textContent = `快照 ${new Date(result.captured_at).toLocaleTimeString()}`;
    if (result.screenshot_method === 'scrcpy') {
      $('#stream-status').textContent += ' · scrcpy 回退（有损取帧）';
    }
    $('#stream-status').classList.remove('live', 'frozen');
    $('#search-count').textContent = '仅搜索当前快照';
    updateTreeHighlights();
    updateOverlay();
  } catch (error) {
    if (epoch !== state.connectionEpoch || requestId !== state.captureSerial) return;
    state.snapshotId = null;
    state.tree = null;
    state.hierarchy = [];
    state.matches = [];
    renderHierarchy();
    renderResults();
    clearSelection();
    $('#tree-node-count').textContent = '0';
    $('#tree-empty').textContent = error.message;
    $('#tree-empty').classList.remove('hidden');
    $('#tree-empty').classList.add('error');
    $('#accessibility-status').textContent = '无障碍：读取失败';
    $('#accessibility-status').classList.add('error');
    $('#stream-status').textContent = '获取失败，请刷新';
    toast(`获取画面失败：${error.message}`);
  } finally {
    if (requestId === state.captureSerial) state.capturing = false;
  }
}

function labelFor(element) {
  return element.text || element.contentDescription || element.resourceId || element.className || element.nodeName || '未命名元素';
}

function boundsText(element) {
  const b = element.bounds;
  if (!b) return '无坐标';
  return `[${b.left},${b.top}][${b.right},${b.bottom}]`;
}

function renderHierarchy() {
  const list = $('#tree-list');
  list.replaceChildren();
  $('#tree-empty').classList.toggle('hidden', Boolean(state.tree));
  if (!state.tree) {
    $('#tree-empty').textContent = '当前页面没有可显示的 UI 元素';
    return;
  }
  list.append(createTreeNode(state.tree, true));
}

function countTreeNodes(node) {
  if (!node) return 0;
  return 1 + (node.children || []).reduce((count, child) => count + countTreeNodes(child), 0);
}

function collectExpandedNodes(node) {
  const expanded = new Set();
  const visit = (current, depth) => {
    if ((depth < 2 || (!current.text && !current.resourceId)) && current.children?.length) expanded.add(current.xpath);
    current.children?.forEach((child) => visit(child, depth + 1));
  };
  if (node) visit(node, 0);
  return expanded;
}

function createTreeNode(element, expanded = false) {
  const node = document.createElement('div');
  node.className = 'tree-node';
  const row = document.createElement('div');
  row.className = 'tree-row';
  row.dataset.xpath = element.xpath || '';
  const children = element.children || [];
  const toggle = document.createElement('button');
  toggle.className = 'tree-toggle';
  toggle.type = 'button';
  const isExpanded = expanded || state.treeExpanded.has(element.xpath);
  toggle.textContent = children.length ? (isExpanded ? '▾' : '▸') : '·';
  toggle.disabled = children.length === 0;
  toggle.setAttribute('aria-label', isExpanded ? '折叠节点' : '展开节点');
  const select = document.createElement('button');
  select.className = 'tree-select';
  select.type = 'button';
  const title = document.createElement('strong');
  title.textContent = labelFor(element);
  const meta = document.createElement('span');
  meta.textContent = `${element.className || element.nodeName || element.packageName || '节点'}${element.bounds ? ` · ${boundsText(element)}` : ''}`;
  select.append(title, meta);
  row.append(toggle, select);
  node.append(row);

  if (children.length) {
    const childList = document.createElement('div');
    childList.className = `tree-children${isExpanded ? '' : ' collapsed'}`;
    children.forEach((child) => childList.append(createTreeNode(child)));
    node.append(childList);
    toggle.addEventListener('click', () => {
      const isExpanded = !childList.classList.contains('collapsed');
      childList.classList.toggle('collapsed', isExpanded);
      if (isExpanded) state.treeExpanded.delete(element.xpath);
      else state.treeExpanded.add(element.xpath);
      toggle.textContent = isExpanded ? '▸' : '▾';
      toggle.setAttribute('aria-label', isExpanded ? '展开节点' : '折叠节点');
    });
  }
  select.addEventListener('click', () => selectElement(element, 'hierarchy'));
  return node;
}

function findTreeNodeByXPath(xpath, node = state.tree) {
  if (!node) return null;
  if (node.xpath === xpath) return node;
  for (const child of node.children || []) {
    const match = findTreeNodeByXPath(xpath, child);
    if (match) return match;
  }
  return null;
}

function selectorFor(element) {
  return element.u2Selector || element.xpath || '';
}

function selectElement(element, source = 'element') {
  const treeElement = element.xpath ? findTreeNodeByXPath(element.xpath) : null;
  const selected = treeElement ? { ...treeElement, ...element } : element;
  state.selected = { ...selected, _selector: selected._selector || selectorFor(selected) };
  if (source !== 'refresh') activateTab('detail');
  $('#detail-subtitle').textContent = `${selected.className || selected.nodeName || 'UI 元素'} · ${boundsText(selected)}`;
  $('#click-element').disabled = !state.connected || !element.bounds || state.mode !== 'hierarchy';
  $('#save-element').disabled = !element.bounds || state.mode !== 'hierarchy' || !state.snapshotId;
  renderDetail(state.selected);
  updateOverlay();
  document.querySelectorAll('.tree-row').forEach((row) => {
    row.classList.toggle('active', Boolean(state.selected.xpath) && row.dataset.xpath === state.selected.xpath);
  });
  if (!['hierarchy', 'refresh'].includes(source) && state.selected.xpath) revealTreeSelection(state.selected.xpath);
  document.querySelectorAll('.result-item').forEach((button) => button.classList.remove('active'));
  if (source === 'search') {
    const index = state.matches.indexOf(element);
    $('#search-results').children[index]?.classList.add('active');
  }
}

function revealTreeSelection(xpath) {
  const row = [...document.querySelectorAll('.tree-row')].find((item) => item.dataset.xpath === xpath);
  if (!row) return;
  let parent = row.parentElement.closest('.tree-node');
  while (parent) {
    const children = parent.querySelector(':scope > .tree-children');
    const toggle = parent.querySelector(':scope > .tree-row > .tree-toggle');
    if (children) children.classList.remove('collapsed');
    if (children) state.treeExpanded.add(parent.querySelector(':scope > .tree-row')?.dataset.xpath || '');
    if (toggle && toggle.disabled === false) {
      toggle.textContent = '▾';
      toggle.setAttribute('aria-label', '折叠节点');
    }
    parent = parent.parentElement.closest('.tree-node');
  }
  row.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

function renderDetail(element) {
  const root = $('#element-detail');
  root.replaceChildren();
  const detailElement = { ...element };
  delete detailElement.x;
  delete detailElement.y;
  if (element.x !== undefined || element.y !== undefined) {
    detailElement.position = `(${element.x ?? '—'}, ${element.y ?? '—'})`;
  }
  delete detailElement.centerX;
  delete detailElement.centerY;
  if (element.centerX !== undefined || element.centerY !== undefined) {
    detailElement.center = `(${element.centerX ?? '—'}, ${element.centerY ?? '—'})`;
  }
  const known = new Set(groups.flatMap(([, keys]) => keys));
  const definitions = [
    ['定位方式', element.u2Selector && element.xpath ? ['locator', 'xpath'] : ['locator']],
    ...groups.map(([name, keys]) => [name, keys.filter((key) => key in detailElement)]),
  ];
  const extras = Object.keys(element).filter((key) =>
    !known.has(key) && !['xpath', 'u2Selector', 'index', 'nodeName', 'children', '_selector'].includes(key)
      && !['x', 'y', 'centerX', 'centerY'].includes(key)
  );
  if (extras.length) definitions.push(['其他原生属性', extras]);

  for (const [title, keys] of definitions) {
    if (!keys.length) continue;
    const section = document.createElement('section');
    section.className = 'property-group';
    const heading = document.createElement('h3');
    heading.textContent = title;
    section.append(heading);
    keys.forEach((key) => {
      const label = key === 'locator'
        ? element.u2Selector ? 'u2 selector' : element.xpath ? 'XPath（兜底）' : '无法定位'
        : key === 'xpath' ? 'XPath' : key === 'center' ? '中心点' : key;
      const value = key === 'locator'
        ? element.u2Selector || element.xpath || ''
        : detailElement[key];
      const row = document.createElement('div');
      row.className = 'property-row';
      row.classList.toggle('locator-row', ['locator', 'xpath'].includes(key));
      const name = document.createElement('span');
      name.className = 'property-name';
      name.textContent = label;
      const content = document.createElement('span');
      content.className = 'property-value';
      if (typeof value === 'boolean') content.classList.add(`boolean-${value}`);
      content.textContent = typeof value === 'object' ? JSON.stringify(value) : String(value);
      content.title = content.textContent;
      const copy = document.createElement('button');
      copy.className = 'copy-property';
      copy.type = 'button';
      copy.title = '复制属性';
      copy.setAttribute('aria-label', `复制 ${label}`);
      copy.textContent = '⧉';
      copy.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(content.textContent);
          toast(`${label} 已复制`);
        } catch {
          toast('复制失败，请检查浏览器剪贴板权限');
        }
      });
      row.append(name, content, copy);
      section.append(row);
    });
    root.append(section);
  }
}

async function search(event) {
  event.preventDefault();
  const query = $('#search-query').value.trim();
  if (!query) return toast('请输入 u2 selector 或 XPath');
  if (state.mode !== 'hierarchy' || !state.snapshotId || state.capturing) return toast('请先点击 hierarchy 获取层级快照');
  const epoch = state.connectionEpoch;
  try {
    const snapshotId = state.snapshotId;
    const result = await api('/internal/capture/find', { method: 'POST', body: JSON.stringify({ query, snapshot_id: snapshotId }) });
    if (snapshotId !== state.snapshotId || epoch !== state.connectionEpoch) return;
    state.searchQuery = query;
    state.matches = result.elements;
    $('#search-count').textContent = `找到 ${result.count} 个元素`;
    renderResults();
    updateOverlay();
    updateTreeHighlights();
    if (result.count > 50) toast('匹配元素超过 50 个，建议缩小搜索范围');
    if (!result.count) toast('未找到匹配元素');
  } catch (error) {
    toast(`搜索失败：${error.message}`);
  }
}

function renderResults() {
  const root = $('#search-results');
  root.replaceChildren();
  state.matches.slice(0, 100).forEach((element) => {
    const button = document.createElement('button');
    button.className = 'result-item';
    button.type = 'button';
    const title = document.createElement('strong');
    title.textContent = labelFor(element);
    const meta = document.createElement('span');
    meta.textContent = `${element.className || 'unknown'} · ${boundsText(element)}`;
    button.append(title, meta);
    button.addEventListener('click', () => selectElement(element, 'search'));
    root.append(button);
  });
}

function auxiliaryBounds(elements) {
  const seen = new Set();
  return elements.filter(element => {
    const b = element.bounds;
    if (!b || ![b.left, b.top, b.right, b.bottom].every(Number.isFinite)) return false;
    if (b.right <= b.left || b.bottom <= b.top ||
        b.right <= 0 || b.bottom <= 0 || b.left >= state.width || b.top >= state.height) return false;
    if (element.nodeName === 'hierarchy' || element.visible === false || element.visibleToUser === false) return false;
    const hasLabel = [element.text, element.contentDescription, element.description]
      .some(value => typeof value === 'string' && value.trim());
    const interactive = ['clickable', 'longClickable', 'checkable', 'scrollable', 'focusable']
      .some(key => element[key] === true);
    const container = element.children?.length > 0 ||
      /(?:Layout|ViewGroup|ScrollView|RecyclerView|ListView|ViewPager)\d*$/.test(element.className || '');
    if (container && !hasLabel && !interactive) return false;
    const key = `${b.left},${b.top},${b.right},${b.bottom}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function updateOverlay() {
  const canvas = $('#overlay-canvas');
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, canvas.width, canvas.height);
  if (state.locatorMode) {
    if (state.showBounds) {
      const scale = state.width / Math.max(1, canvas.clientWidth);
      context.save();
      context.lineWidth = 0.75 * scale;
      context.setLineDash([2 * scale, 2 * scale]);
      context.globalAlpha = 0.65;
      for (const element of auxiliaryBounds(state.hierarchy)) {
        const b = element.bounds;
        context.strokeStyle = element.clickable ? '#6585d9' : '#d4a58a';
        context.strokeRect(b.left, b.top, b.right - b.left, b.bottom - b.top);
      }
      context.restore();
    }
    for (const element of state.matches) {
      if (element.bounds) drawHighlight(context, element.bounds, '#68a9ff', '#4285f433', 2);
    }
    if (state.selected?.bounds) drawHighlight(context, state.selected.bounds, '#ff626b', '#ff535344', 3);
  }
  if (state.mode === 'color' && state.color?.current) {
    const { x, y } = state.color.current;
    context.strokeStyle = '#ff67de';
    context.lineWidth = state.width / Math.max(1, canvas.clientWidth);
    context.beginPath();
    context.moveTo(x + .5, 0); context.lineTo(x + .5, state.height);
    context.moveTo(0, y + .5); context.lineTo(state.width, y + .5);
    context.stroke();
  }
}

function drawCanvas() {
  const canvas = $('#screen-canvas');
  if (!state.width || !state.height || !canvas.width || !canvas.height) return;
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, state.width, state.height);
  if (state.frame) context.drawImage(state.frame, 0, 0, state.width, state.height);
  updateOverlay();
}

function drawHighlight(context, bounds, stroke, fill, lineWidth) {
  const width = Math.max(1, bounds.right - bounds.left);
  const height = Math.max(1, bounds.bottom - bounds.top);
  context.save();
  context.fillStyle = fill;
  context.strokeStyle = stroke;
  context.lineWidth = lineWidth * state.width / Math.max(1, $('#screen-canvas').clientWidth);
  context.fillRect(bounds.left, bounds.top, width, height);
  context.strokeRect(bounds.left, bounds.top, width, height);
  context.restore();
}

function eventCoordinates(event) {
  const rect = $('#screen-canvas').getBoundingClientRect();
  const x = Math.max(0, Math.min(state.width, Math.round((event.clientX - rect.left) * state.width / rect.width)));
  const y = Math.max(0, Math.min(state.height, Math.round((event.clientY - rect.top) * state.height / rect.height)));
  return { x, y, clientX: event.clientX, clientY: event.clientY, rect };
}

function updateRegionBox(start, end) {
  const box = $('#selection-box');
  const canvas = $('#screen-canvas');
  const scaleX = canvas.clientWidth / state.width;
  const scaleY = canvas.clientHeight / state.height;
  const left = Math.min(start.x, end.x) * scaleX;
  const top = Math.min(start.y, end.y) * scaleY;
  box.style.left = `${left}px`;
  box.style.top = `${top}px`;
  box.style.width = `${Math.abs(end.x - start.x) * scaleX}px`;
  box.style.height = `${Math.abs(end.y - start.y) * scaleY}px`;
  box.classList.remove('hidden');
  const x1 = Math.min(start.x, end.x);
  const y1 = Math.min(start.y, end.y);
  const x2 = Math.max(start.x, end.x);
  const y2 = Math.max(start.y, end.y);
  $('#region-coordinate').textContent = `框选区域：${x1},${y1} → ${x2},${y2}`;
}

function setRegion(start, end) {
  const x1 = Math.min(start.x, end.x);
  const y1 = Math.min(start.y, end.y);
  const x2 = Math.max(start.x, end.x);
  const y2 = Math.max(start.y, end.y);
  if (x1 !== x2 && y1 !== y2) {
    applyRegion({ x1, y1, x2, y2 });
    state.regionMode = false;
    updateRegionMode();
    $('#gesture-status').textContent = '拖动边角或输入坐标微调；保存后清除';
  }
}

function applyRegion(region) {
  const { x1, y1, x2, y2 } = region;
  if (![x1, y1, x2, y2].every(Number.isInteger) || x1 < 0 || y1 < 0 ||
      x2 > state.width || y2 > state.height || x1 >= x2 || y1 >= y2) {
    toast('选区无效：坐标须为屏幕内的整数，且 x1 < x2、y1 < y2', true);
    return false;
  }
  state.region = { ...region };
  state.regionVersion += 1;
  $('#region-coordinate').textContent = `框选区域：${x1},${y1} → ${x2},${y2}`;
  $('#save-region').disabled = state.savingRegion;
  $('#clear-region').disabled = false;
  $('#region-editor').classList.remove('hidden');
  for (const key of ['x1', 'y1', 'x2', 'y2']) {
    $(`#region-${key}`).value = region[key];
    $(`#region-${key}`).max = key.startsWith('x') ? state.width : state.height;
  }
  $('#region-size').textContent = `${x2 - x1} × ${y2 - y1}px · 方向键移动，Shift 10px`;
  renderRegionBox();
  return true;
}

function moveRegion(region, dx, dy) {
  dx = Math.max(-region.x1, Math.min(state.width - region.x2, dx));
  dy = Math.max(-region.y1, Math.min(state.height - region.y2, dy));
  return { x1: region.x1 + dx, x2: region.x2 + dx, y1: region.y1 + dy, y2: region.y2 + dy };
}

function dragRegion(start, point) {
  const dx = point.x - start.x;
  const dy = point.y - start.y;
  if (start.handle === 'move') return applyRegion(moveRegion(start.region, dx, dy));
  const region = { ...start.region };
  if (start.handle.includes('w')) region.x1 = Math.max(0, Math.min(region.x2 - 1, start.region.x1 + dx));
  if (start.handle.includes('e')) region.x2 = Math.min(state.width, Math.max(region.x1 + 1, start.region.x2 + dx));
  if (start.handle.includes('n')) region.y1 = Math.max(0, Math.min(region.y2 - 1, start.region.y1 + dy));
  if (start.handle.includes('s')) region.y2 = Math.min(state.height, Math.max(region.y1 + 1, start.region.y2 + dy));
  return applyRegion(region);
}

function renderRegionBox() {
  const box = $('#selection-box');
  if (!state.region || !state.width || !state.height) {
    box.classList.add('hidden');
    return;
  }
  const canvas = $('#screen-canvas');
  const scaleX = canvas.clientWidth / state.width;
  const scaleY = canvas.clientHeight / state.height;
  box.style.left = `${state.region.x1 * scaleX}px`;
  box.style.top = `${state.region.y1 * scaleY}px`;
  box.style.width = `${(state.region.x2 - state.region.x1) * scaleX}px`;
  box.style.height = `${(state.region.y2 - state.region.y1) * scaleY}px`;
  box.classList.remove('hidden');
}

async function inspectAt(x, y) {
  const candidates = state.hierarchy.filter(({ bounds }) => bounds && x >= bounds.left &&
    x < bounds.right && y >= bounds.top && y < bounds.bottom);
  candidates.sort((a, b) => a.width * a.height - b.width * b.height);
  if (!candidates.length) return toast('当前快照此处没有可定位元素');
  selectElement(candidates[0], 'screen');
}

async function action(action, extra = {}) {
  if (!state.connected) return toast('离线分析模式不能操作设备', true);
  if (state.mode === 'remote' && ['home', 'back', 'recent'].includes(action)) {
    state.remote?.send({ type: 'key', key: action });
    return;
  }
  try {
    const routes = { click_element: 'clickElem', input: 'inputText', clear: 'clearText' };
    const result = await api(`/internal/${routes[action] || 'action'}`, {
      method: 'POST',
      body: JSON.stringify({ action, ...extra }),
    });
    toast(result.message);
    if (state.mode !== 'remote') $('#stream-status').textContent = '设备已操作 · 点击刷新更新快照';
  } catch (error) {
    toast(`操作失败：${error.message}`);
  }
}

async function saveSnapshot(kind, type) {
  if (kind === 'region' && (state.savingRegion || !state.region)) return;
  const regionVersion = state.regionVersion;
  const snapshotId = state.snapshotId;
  if (kind === 'region') {
    state.savingRegion = true;
    $('#save-region').disabled = true;
  }
  try {
    const directory = state.directories?.snapshot_directory;
    if (!directory) return toast('请先设置截图保存路径');
    if (state.capturing) return toast('正在获取画面，请稍后截图');
    let result;
    if (state.mode === 'remote' && kind === 'full') {
      result = await api(`/snapshot/full?custom_path=${encodeURIComponent(directory)}`);
    } else {
      if (!snapshotId) return toast('请先获取 hierarchy 或 color 静态画面');
      result = await api('/internal/capture/save', {
        method: 'POST', body: JSON.stringify({
          kind, snapshot_id: snapshotId, custom_path: directory,
          region: kind === 'region' ? { ...state.region } : undefined,
          xpath: kind === 'element' ? state.selected?.xpath : undefined,
        }),
      });
    }
    showSnapshotPreview(result, type);
    if (kind === 'region' && regionVersion === state.regionVersion && snapshotId === state.snapshotId) clearRegion();
    toast(`${type}已保存：${result.path}`);
  } catch (error) {
    toast(`截图失败：${error.message}`);
  } finally {
    if (kind === 'region') {
      state.savingRegion = false;
      $('#save-region').disabled = !state.region;
    }
  }
}

function showSnapshotPreview(snapshot, type) {
  state.snapshots.add({ ...snapshot, name: snapshot.path.split(/[\\/]/).pop() });
  activateTab('captures');
  $('#pane-captures').scrollTop = 0;
}

function activateTab(name) {
  document.querySelectorAll('[data-tab]').forEach((button) => {
    const active = button.dataset.tab === name;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
    $(`#pane-${button.dataset.tab}`).classList.toggle('hidden', !active);
  });
}

function resetLayout() {
  layoutWidths.preview = layoutLimits.preview.initial;
  layoutWidths.inspector = layoutLimits.inspector.initial;
  applyLayoutWidths();
  saveLayoutWidths();
}

function updateTreeHighlights() {
  const paths = new Set(state.matches.map((item) => item.xpath).filter(Boolean));
  document.querySelectorAll('.tree-row').forEach((row) => {
    row.classList.toggle('matched', paths.has(row.dataset.xpath));
    row.classList.toggle('active', row.dataset.xpath === state.selected?.xpath);
  });
}

function clearSelection() {
  state.selected = null;
  $('#detail-subtitle').textContent = '在画布或节点树中选择元素';
  $('#element-detail').innerHTML = '<div class="detail-placeholder">点击设备画面或节点树，查看元素属性</div>';
  $('#click-element').disabled = true;
  $('#save-element').disabled = true;
  updateOverlay();
  updateTreeHighlights();
}

function clearRegion() {
  state.region = null;
  state.regionVersion += 1;
  $('#save-region').disabled = true;
  $('#clear-region').disabled = true;
  $('#region-coordinate').textContent = '框选区域：--';
  $('#region-editor').classList.add('hidden');
  renderRegionBox();
}

function updateRegionMode() {
  $('#region-mode').setAttribute('aria-pressed', String(state.regionMode));
  $('#region-mode span').textContent = state.regionMode ? '框选中' : '框选';
  $('#mode-hint').textContent = state.regionMode ? '拖动框选，松开后可微调' :
    state.mode === 'hierarchy' ? '静态快照 · 页面变化后点击刷新' :
    state.mode === 'color' ? '移动取色 · 方向键微调 · 点击确认' : 'scrcpy · 点击 / 拖动 / 双指滚动';
}

function updateFreezeStatus() {
  $('#freeze-screen').textContent = state.frozen ? '▶' : 'Ⅱ';
  $('#freeze-screen').setAttribute('aria-pressed', String(state.frozen));
  $('#stream-status').textContent = state.frozen ? '显示已暂停 · 触摸已禁用' : 'scrcpy remote';
  $('#stream-status').classList.toggle('frozen', state.frozen);
  $('#stream-status').classList.toggle('live', !state.frozen);
}

function setImportFile(kind, file) {
  state.importSelection[kind] = file;
  $(`#import-${kind}-name`).textContent = file.name;
  $('#confirm-import').disabled = !(state.importSelection.image && state.importSelection.xml);
}

async function selectImportFile(kind) {
  const selection = state.importSelection;
  const button = $(`#choose-import-${kind}`);
  button.disabled = true;
  $('#capture-library-status').textContent = '请在服务所在电脑的系统窗口中选择文件';
  try {
    const result = await api('/internal/capture/select-file', {
      method: 'POST', body: JSON.stringify({kind}),
    });
    if (selection !== state.importSelection) return;
    if (!result.cancelled) {
      setImportFile(kind, result);
      if (result.directory) $('#import-directory-hint').textContent = `默认目录：${result.directory}`;
    }
    $('#capture-library-status').textContent = result.cancelled ? '已取消选择，可重新选择' : '';
  } catch (error) { $('#capture-library-status').textContent = error.message; }
  finally { button.disabled = false; }
}

async function activateImportedCapture(result, files, name) {
  if (state.connected) await disconnect();
  if (state.connected) throw Error('无法断开设备，导入已取消');
  const xmlDocument = new DOMParser().parseFromString(files.xml, 'application/xml');
  const ids = [...new Set([0, result.display_id, ...[...xmlDocument.querySelectorAll('[display-id]')]
    .map(node => Number(node.getAttribute('display-id')))
    .filter(id => Number.isInteger(id) && id >= 0 && id <= 2147483647)])].sort((a, b) => a - b);
  state.importedFiles = files;
  setDisplayOptions(ids, result.display_id);
  await showInspection(result);
  $('#stream-status').textContent = `离线导入 · ${name}`;
  toast('已导入图片和 XML，可离线分析');
}

function bindEvents() {
  $('#display-id').addEventListener('change', applyDisplay);
  $('#import-capture').addEventListener('click', async () => {
    state.importSelection = {};
    for (const kind of ['image', 'xml']) {
      $(`#import-${kind}-name`).textContent = kind === 'image' ? '未选择图片' : '未选择 XML';
    }
    $('#confirm-import').disabled = true;
    $('#capture-library').showModal();
    for (const kind of ['image', 'xml']) $(`#choose-import-${kind}`).disabled = true;
    $('#capture-library-status').textContent = '正在读取已保存的导入目录…';
    try {
      await loadDirectorySettings();
      $('#import-directory-hint').textContent = `默认目录：${state.directories.import_directory}`;
      $('#capture-library-status').textContent = '';
      for (const kind of ['image', 'xml']) $(`#choose-import-${kind}`).disabled = false;
    } catch (error) { $('#capture-library-status').textContent = `读取目录失败：${error.message}`; }
  });
  $('#close-capture-library').addEventListener('click', () => $('#capture-library').close());
  $('#open-settings').addEventListener('click', async () => {
    $('#settings-dialog').showModal();
    try { await loadDirectorySettings(); }
    catch (error) { $('#directory-settings-status').textContent = error.message; }
  });
  $('#close-settings').addEventListener('click', () => $('#settings-dialog').close());
  $('#project-directory').addEventListener('input', updateInheritedDirectories);
  for (const [key, selector] of Object.entries(directoryFields)) {
    if (key === 'project_directory') continue;
    $(selector).addEventListener('input', () => {
      $(selector).dataset.inherited = String(!$(selector).value.trim());
      if (key === 'inspection_directory' && $('#import-directory').dataset.inherited === 'true') {
        $('#import-directory').value = $(selector).value;
      }
    });
  }
  $('#directory-settings-form').addEventListener('submit', async event => {
    event.preventDefault();
    $('#save-directory-settings').disabled = true;
    try {
      if (state.recorder?.pending || state.recorder?.unsaved) throw Error('请先停止并保存录屏，再修改目录');
      const values = Object.fromEntries(Object.entries(directoryFields).map(([key, selector]) => {
        const input = $(selector);
        const value = input.value.trim();
        if (input.dataset.inherited === 'true') return [key, ''];
        const previous = state.directories;
        return [key, value === previous[key] ? previous.overrides?.[key] || value : value];
      }));
      state.directories = await api('/internal/settings/directories', {method: 'POST', body: JSON.stringify(values)});
      showDirectorySettings(state.directories);
      $('#directory-settings-status').textContent = '设置已保存并生效，已有文件未移动';
      await Promise.all([state.snapshots.refresh(), state.recordings.refresh()]);
    } catch (error) { $('#directory-settings-status').textContent = `保存失败：${error.message}`; }
    finally { $('#save-directory-settings').disabled = false; }
  });
  $('#save-capture').addEventListener('click', async () => {
    $('#save-capture').disabled = true;
    const epoch = state.connectionEpoch;
    const serial = state.captureSerial;
    try {
      let snapshotId = state.snapshotId;
      if (state.connected && state.mode !== 'hierarchy') {
        const capture = await api('/internal/capture', {method: 'POST', body: JSON.stringify({hierarchy: true})});
        snapshotId = capture.snapshot_id;
      }
      if (!snapshotId) throw Error('请先获取 hierarchy 快照或导入图片和 XML');
      if (epoch !== state.connectionEpoch || serial !== state.captureSerial) throw Error('设备或画面已变化，请重新保存');
      const result = await api('/internal/capture/archive/save', {
        method: 'POST', body: JSON.stringify({
          snapshot_id: snapshotId,
          directory: state.directories?.inspection_directory || null,
        }),
      });
      toast(`图片和 XML 已保存：${result.path}`);
    } catch (error) { toast(`保存失败：${error.message}`, true); }
    finally { $('#save-capture').disabled = false; }
  });
  for (const kind of ['image', 'xml']) {
    $(`#choose-import-${kind}`).addEventListener('click', () => selectImportFile(kind));
  }
  $('#confirm-import').addEventListener('click', async () => {
    const {image, xml} = state.importSelection;
    if (!image || !xml) return;
    const epoch = state.connectionEpoch;
    const requestId = ++state.captureSerial;
    $('#confirm-import').disabled = true;
    try {
      const files = {image: image.content, xml: xml.content};
      const document = new DOMParser().parseFromString(files.xml, 'application/xml');
      if (document.querySelector('parsererror')) throw Error('XML 格式无效');
      const displayIds = [...new Set([0, ...[...document.querySelectorAll('[display-id]')]
        .map(node => Number(node.getAttribute('display-id')))
        .filter(id => Number.isInteger(id) && id >= 0 && id <= 2147483647)])].sort((a, b) => a - b);
      const displayId = displayIds.includes(state.displayId) ? state.displayId : displayIds[0];
      const result = await api('/internal/capture/import', {
        method: 'POST', body: JSON.stringify({...files, display_id: displayId}),
      });
      if (epoch !== state.connectionEpoch || requestId !== state.captureSerial) throw Error('设备或画面已变化，请重新导入');
      await activateImportedCapture(result, files, `${image.name} · ${xml.name}`);
      $('#capture-library').close();
    } catch (error) { $('#capture-library-status').textContent = `导入失败：${error.message}`; }
    finally { $('#confirm-import').disabled = false; }
  });
  const mediaAction = handler => async () => {
    try { await handler(); } catch (error) { toast(error.message, true); }
  };
  for (const [kind, library, key] of [
    ['snapshots', state.snapshots, 'snapshot_directory'],
    ['recordings', state.recordings, 'recording_directory'],
  ]) {
    $(`#delete-${kind}`).addEventListener('click', mediaAction(() => library.remove()));
    $(`#refresh-${kind}`).addEventListener('click', mediaAction(() => library.refresh()));
    $(`#open-${kind}`).addEventListener('click', mediaAction(() => library.request('open-directory', { path: state.directories[key] })));
  }
  $('#record-screen').addEventListener('click', mediaAction(async () => {
    if (state.recorder.pending) { await state.recorder.stop(); return; }
    if (state.recorder.unsaved) throw Error('上次录屏尚未保存，请先到录屏页重试');
    $('#record-screen').disabled = true;
    try {
      const directory = await state.recordings.request('directory', { path: state.directories.recording_directory });
      if (state.mode !== 'remote' || !state.remote?.ready) await switchMode('remote');
      const remote = state.remote;
      const deadline = Date.now() + 45000;
      $('#recording-status').textContent = '等待 remote 首帧…';
      while (remote && state.remote === remote && !remote.ready && !remote.stopped && Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      if (state.remote !== remote || !remote?.ready || !state.frame) throw Error('remote 未就绪，未开始录屏');
      await state.recorder.start(state.frame, directory.directory,
        () => state.connected && state.remote === remote && remote.ready && state.mode === 'remote');
    } finally {
      $('#record-screen').disabled = !state.connected;
      if (!state.recorder.pending) $('#recording-status').textContent = '';
    }
  }));
  $('#retry-recording').addEventListener('click', mediaAction(async () => {
    $('#retry-recording').disabled = true;
    try {
      state.recorder.directory = state.directories.recording_directory;
      await state.recorder.upload();
      $('#retry-recording').classList.add('hidden');
      $('#recording-status').textContent = '已保存';
    } finally { $('#retry-recording').disabled = false; }
  }));
  window.addEventListener('beforeunload', event => {
    if (state.recorder.pending || state.recorder.unsaved) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  $('#toggle-bounds').addEventListener('click', () => {
    state.showBounds = !state.showBounds;
    updateBoundsButton();
    updateOverlay();
  });
  $('#theme-toggle').addEventListener('click', () => {
    window.UiHoundTheme.set(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');
    updateThemeButton();
  });
  applyLayoutWidths();
  bindPanelResize();
  $('#disconnect-device').addEventListener('click', disconnect);
  $('#reset-layout').addEventListener('click', resetLayout);
  $('#reset-layout-tools').addEventListener('click', resetLayout);
  $('#show-captures').addEventListener('click', mediaAction(async () => {
    activateTab('captures'); await state.snapshots.refresh();
  }));
  $('#clear-region').addEventListener('click', clearRegion);
  $('#refresh-capture').addEventListener('click', refreshScreen);
  $('#apply-region').addEventListener('click', () => {
    const region = Object.fromEntries(['x1', 'y1', 'x2', 'y2'].map(key => [key, $(`#region-${key}`).valueAsNumber]));
    applyRegion(region);
  });
  $('#region-editor').addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); $('#apply-region').click(); }
  });
  $('#clear-colors').addEventListener('click', () => state.color.clear());
  document.querySelectorAll('[data-tab]').forEach((button, index, buttons) => {
    button.addEventListener('click', () => {
      if (button.dataset.tab === 'color' && (state.connected || state.snapshotId) && state.mode !== 'color') switchMode('color');
      else activateTab(button.dataset.tab);
      const library = { captures: state.snapshots, recordings: state.recordings }[button.dataset.tab];
      library?.refresh().catch(error => toast(error.message, true));
    });
    button.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const target = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
      activateTab(buttons[target].dataset.tab);
      buttons[target].focus();
    });
  });
  for (const expand of [true, false]) {
    $(`#${expand ? 'expand' : 'collapse'}-tree`).addEventListener('click', () => {
      state.treeExpanded.clear();
      if (expand) {
        const visit = (node) => { if (node.children?.length) state.treeExpanded.add(node.xpath); node.children?.forEach(visit); };
        if (state.tree) visit(state.tree);
      }
      renderHierarchy();
      updateTreeHighlights();
    });
  }
  $('#refresh-devices').addEventListener('click', scanDevices);
  $('#connect-device').addEventListener('click', () => connect($('#device-select').value));
  $('#device-select').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') connect(event.target.value);
  });
  $('#freeze-screen').addEventListener('click', () => {
    if (state.mode !== 'remote') return;
    cancelPointer();
    state.frozen = !state.frozen;
    updateFreezeStatus();
    if (!state.frozen && state.frame) renderFrame(state.frame);
  });
  $('#locator-mode').addEventListener('click', () => switchMode('hierarchy'));
  $('#color-mode').addEventListener('click', () => switchMode('color'));
  $('#interaction-mode').addEventListener('click', () => switchMode('remote'));
  $('#region-mode').addEventListener('click', async () => {
    if (state.mode === 'remote') await switchMode('color');
    if (!state.snapshotId || state.capturing) return toast('请先获取静态画面');
    state.regionMode = !state.regionMode;
    updateRegionMode();
    $('#gesture-status').textContent = state.regionMode
      ? '拖动屏幕框选截图区域'
      : state.locatorMode ? '定位模式：点击查看元素' : '交互模式：点击、滑动或长按';
  });
  $('#reload-tree').addEventListener('click', loadHierarchy);
  $('#search-form').addEventListener('submit', search);
  $('#clear-highlights').addEventListener('click', () => {
    state.matches = [];
    $('#search-results').replaceChildren();
    $('#search-count').textContent = '已清除高亮';
    clearSelection();
  });
  document.querySelectorAll('[data-action]').forEach((button) => {
    button.addEventListener('click', () => action(button.dataset.action));
  });
  $('#click-element').addEventListener('click', () => {
    if (state.selected?._selector) {
      action('click_element', { selector: state.selected._selector });
    }
  });
  $('#send-text').addEventListener('click', () => {
    const text = $('#input-text').value;
    if (text) action('input', { text, selector: state.locatorMode ? state.selected?._selector : undefined });
    else toast('请输入要发送的文本');
  });
  $('#input-text').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && event.ctrlKey) $('#send-text').click();
  });
  $('#clear-text').addEventListener('click', () => action('clear', { selector: state.locatorMode ? state.selected?._selector : undefined }));
  $('#save-full').addEventListener('click', () => saveSnapshot('full', '全屏截图'));
  $('#save-element').addEventListener('click', () => {
    if (!state.selected?._selector) return toast('请先选择一个元素');
    saveSnapshot('element', '元素截图');
  });
  $('#save-region').addEventListener('click', () => {
    if (!state.region) return;
    saveSnapshot('region', '区域截图');
  });
  $('#screen-stage').addEventListener('pointerenter', () => {
    if (state.mode === 'color' && !document.activeElement?.matches('input, textarea, select')) {
      $('#screen-stage').focus({ preventScroll: true });
    }
  });
  $('#screen-stage').addEventListener('pointermove', (event) => {
    if ((!state.connected && !state.snapshotId) || !state.width || state.capturing) return;
    if (state.mode !== 'remote' && !state.snapshotId) return;
    const point = eventCoordinates(event);
    $('#mouse-coordinate').textContent = `设备坐标：x ${point.x} · y ${point.y}`;
    if (state.pointerStart?.handle) dragRegion(state.pointerStart, point);
    else if (state.pointerStart?.kind === 'region') updateRegionBox(state.pointerStart, point);
    else if (state.pointerStart?.kind === 'remote') sendTouch('move', point);
    else if (state.mode === 'color' && !state.regionMode) {
      state.color.sample(point.x, point.y);
      updateOverlay();
    }
  });
  $('#screen-stage').addEventListener('pointerdown', (event) => {
    if ((!state.connected && !state.snapshotId) || !state.width || state.capturing || event.button !== 0) return;
    if (state.mode === 'remote' && (state.frozen || !state.remote?.ready)) return;
    if (state.mode !== 'remote' && !state.snapshotId) return;
    event.preventDefault();
    endWheelGesture();
    $('#screen-stage').focus({ preventScroll: true });
    const point = eventCoordinates(event);
    const selectedBox = event.target.closest('#selection-box');
    const handle = selectedBox && !state.regionMode ? event.target.dataset.handle || 'move' : null;
    const kind = state.regionMode ? 'region' : state.mode;
    state.pointerStart = { ...point, kind, handle, region: state.region ? { ...state.region } : null };
    if (kind === 'remote') sendTouch('down', point);
    $('#screen-stage').setPointerCapture(event.pointerId);
  });
  $('#screen-stage').addEventListener('pointerup', async (event) => {
    if (!state.pointerStart) return;
    const start = state.pointerStart;
    const end = eventCoordinates(event);
    state.pointerStart = null;
    const deltaX = Math.abs(start.x - end.x);
    const deltaY = Math.abs(start.y - end.y);
    const moved = deltaX > 8 || deltaY > 8;
    if (start.handle) {
      dragRegion(start, end);
    } else if (start.kind === 'region' && moved) {
      setRegion(start, end);
    } else if (start.kind === 'region') {
      $('#gesture-status').textContent = '拖动屏幕框选截图区域';
    } else if (start.kind === 'remote') {
      sendTouch('up', end);
    } else if (!moved && start.kind === 'hierarchy') {
      await inspectAt(end.x, end.y);
    } else if (!moved && start.kind === 'color') {
      state.color.sample(end.x, end.y);
      state.color.confirm();
      updateOverlay();
    }
    if ($('#screen-stage').hasPointerCapture(event.pointerId)) $('#screen-stage').releasePointerCapture(event.pointerId);
    renderRegionBox();
  });
  $('#screen-stage').addEventListener('pointercancel', cancelPointer);
  $('#screen-stage').addEventListener('lostpointercapture', cancelPointer);
  window.addEventListener('blur', cancelPointer);
  $('#screen-stage').addEventListener('wheel', event => {
    if (state.mode !== 'remote') return;
    event.preventDefault();
    if (state.frozen || state.regionMode || event.ctrlKey || !state.remote?.ready) return;
    wheelTouch(event);
  }, { passive: false });
  $('#screen-stage').addEventListener('keydown', event => {
    if (state.capturing || (state.mode !== 'remote' && !state.snapshotId)) return;
    const delta = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
    if (delta && (state.region || state.mode === 'color')) {
      event.preventDefault();
      const step = event.shiftKey ? 10 : 1;
      if (state.region) applyRegion(moveRegion(state.region, delta[0] * step, delta[1] * step));
      else { state.color.nudge(delta[0] * step, delta[1] * step); updateOverlay(); }
    } else if (event.key === 'Enter' && state.mode === 'color' && !state.region) {
      event.preventDefault(); state.color.confirm();
    }
  });
  new ResizeObserver(fitScreen).observe($('.screen-surface'));
  window.addEventListener('resize', applyLayoutWidths);
}

function updateLocatorButton() {
  const button = $('#locator-mode');
  button.classList.toggle('active', state.mode === 'hierarchy');
  button.setAttribute('aria-pressed', String(state.mode === 'hierarchy'));
  for (const [id, mode] of [['color-mode', 'color'], ['interaction-mode', 'remote']]) {
    $(`#${id}`).classList.toggle('active', state.mode === mode);
    $(`#${id}`).setAttribute('aria-pressed', String(state.mode === mode));
  }
  $('#mode-label').textContent = state.mode;
  $('#freeze-screen').disabled = !state.connected || state.mode !== 'remote';
  $('#freeze-screen').textContent = 'Ⅱ';
  $('#freeze-screen').setAttribute('aria-pressed', 'false');
  $('#screen-canvas').classList.toggle('interaction-mode', state.mode === 'remote');
  $('#click-element').disabled = !state.connected || !state.selected || state.mode !== 'hierarchy';
  $('#save-element').disabled = !state.selected || state.mode !== 'hierarchy';
  updateRegionMode();
  $('#gesture-status').textContent = state.mode;
  updateOverlay();
}

function sendTouch(action, point) {
  state.remote?.send({
    type: 'touch', action, x: Math.min(state.width - 1, point.x), y: Math.min(state.height - 1, point.y),
    width: state.width, height: state.height,
  });
}

function cancelPointer() {
  endWheelGesture();
  if (state.pointerStart?.kind === 'remote') sendTouch('cancel', state.pointerStart);
  state.pointerStart = null;
  renderRegionBox();
}

function endWheelGesture() {
  const gesture = state.wheelGesture;
  if (!gesture) return;
  clearTimeout(gesture.timer);
  state.wheelGesture = null;
  sendTouch('up', gesture);
}

function wheelTouch(event) {
  if (state.pointerStart || (!event.deltaX && !event.deltaY)) return;
  const rect = $('#screen-canvas').getBoundingClientRect();
  const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.height : 1;
  let gesture = state.wheelGesture;
  if (!gesture) {
    const point = eventCoordinates(event);
    gesture = { x: Math.max(state.width * .1, Math.min(state.width * .9, point.x)),
      y: Math.max(state.height * .1, Math.min(state.height * .9, point.y)), distance: 0 };
    state.wheelGesture = gesture;
    sendTouch('down', gesture);
  }
  // A wheel burst is one finger drag, not dozens of independent Android scroll/fling events.
  const dx = -event.deltaX * unit * state.width / rect.width * .45;
  const dy = -event.deltaY * unit * state.height / rect.height * .45;
  const length = Math.hypot(dx, dy);
  const budget = Math.max(0, Math.min(state.width, state.height) * .55 - gesture.distance);
  const factor = length ? Math.min(1, budget / length) : 0;
  gesture.x = Math.max(1, Math.min(state.width - 2, gesture.x + dx * factor));
  gesture.y = Math.max(1, Math.min(state.height - 2, gesture.y + dy * factor));
  gesture.distance += length * factor;
  sendTouch('move', gesture);
  clearTimeout(gesture.timer);
  gesture.timer = setTimeout(endWheelGesture, 160);
}

async function initialize() {
  updateThemeButton();
  state.snapshots = new window.MediaLibrary({
    kind: 'snapshot', root: $('#snapshot-previews'), count: $('#capture-count'), empty: $('#captures-empty'),
    directory: () => state.directories?.snapshot_directory, notify: toast,
  });
  state.recordings = new window.MediaLibrary({
    kind: 'recording', root: $('#recording-previews'), count: $('#recording-count'), empty: $('#recordings-empty'),
    directory: () => state.directories?.recording_directory, notify: toast,
  });
  state.recorder = new window.ScreenRecorder({
    notify: toast,
    changed: (status, elapsed = 0) => {
      const recording = status === 'recording';
      $('#record-screen').classList.toggle('recording', recording);
      $('#record-screen').setAttribute('aria-pressed', String(recording));
      $('#record-screen').title = recording ? '停止并保存录屏' : '开始录屏';
      $('#record-screen').disabled = status === 'saving' || !state.connected;
      $('#recording-status').textContent = recording ? `录屏 ${Math.floor(elapsed / 1000)}s` :
        status === 'saving' ? '正在保存录屏…' : status === 'unsaved' ? '录屏待保存' : '';
      $('#retry-recording').classList.toggle('hidden', status !== 'unsaved');
      $('#recording-directory').disabled = recording || status === 'saving';
    },
    saved: async result => {
      state.recordings.add(result);
      activateTab('recordings');
      toast(`录屏已保存：${result.path}`);
    },
  });
  state.color = new window.ColorInspector({
    canvas: $('#screen-canvas'), magnifier: $('#color-magnifier'), detail: $('#color-detail'),
    history: $('#color-history'), notify: toast,
  });
  bindEvents();
  updateLocatorButton();
  await Promise.all([scanDevices(), loadDirectorySettings().catch(error => toast(`读取目录设置失败：${error.message}`, true)), api('/device/status').then(async (status) => {
    if (status.connected) {
      setConnection(true, status.serial || '');
      $('#device-select').value = status.serial || '';
      await loadDisplays();
      await loadHierarchy();
    }
  }).catch((error) => toast(`读取设备状态失败：${error.message}`))]);
  try {
    await Promise.all([state.snapshots.refresh(), state.recordings.refresh()]);
  } catch (error) { toast(`读取媒体目录失败：${error.message}`, true); }
  setInterval(async () => {
    try {
      const status = await api('/device/status');
      if (!status.connected && state.connected) setConnection(false);
    } catch (error) {
      $('#screen-message').textContent = error.message;
    }
  }, 5000);
}

function updateThemeButton() {
  const light = document.documentElement.dataset.theme === 'light';
  const button = $('#theme-toggle');
  button.title = light ? '切换到黑夜模式' : '切换到白天模式';
  button.setAttribute('aria-label', button.title);
  button.setAttribute('aria-pressed', String(light));
  button.querySelector('use').setAttribute('href', light ? '#i-moon' : '#i-sun');
  if (window.UiHoundTheme.error) {
    toast(window.UiHoundTheme.error, true);
    window.UiHoundTheme.error = null;
  }
}

function updateBoundsButton() {
  const button = $('#toggle-bounds');
  const label = state.showBounds ? '隐藏节点虚线边框' : '显示节点虚线边框';
  button.title = label;
  button.setAttribute('aria-label', label);
  button.setAttribute('aria-pressed', String(state.showBounds));
}

initialize();
