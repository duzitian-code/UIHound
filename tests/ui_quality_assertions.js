async function uiQualityTests() {
  document.body.dataset.phase = 'ui-quality';
  if (!state.connected) await connect('demo-device');
  else await switchMode('hierarchy');
  activateTab('detail');
  await tick();
  assert(state.snapshotId && !state.snapshotStale, 'fresh capture starts without a stale warning');
  assert(!$('#snapshot-notice').classList.contains('hidden'), 'snapshot provenance is visible near the canvas');
  assert($('#snapshot-status').textContent.includes('静态快照'), 'live device capture is explicitly static');
  assert($('#snapshot-status').getAttribute('role') === 'status', 'snapshot changes have a live status region');
  assert(document.documentElement.scrollWidth <= innerWidth, 'workspace fits the viewport without horizontal clipping');
  assert(document.documentElement.scrollHeight <= innerHeight, 'desktop workspace never requires whole-page vertical scrolling');
  const preview = $('#preview-column').getBoundingClientRect();
  const inspector = $('#inspector-column').getBoundingClientRect();
  assert(preview.right <= inspector.left, 'device preview and inspector retain the desktop side-by-side layout');
  assert($('#snapshot-notice').getBoundingClientRect().height <= 30, 'snapshot provenance stays on one compact line');
  assert($('#snapshot-status').title === $('#snapshot-status').textContent, 'truncated snapshot text remains available in full');
  const footer = $('.statusbar').getBoundingClientRect();
  assert(footer.bottom <= innerHeight && footer.top >= inspector.bottom, 'status bar remains visible below the full-height workspace');
  const toolbarControls = [...document.querySelectorAll('.topbar button, .topbar input, .topbar select')];
  for (const control of toolbarControls) {
    const rect = control.getBoundingClientRect();
    assert(rect.left >= 0 && rect.right <= innerWidth && rect.bottom <= 56, `${control.id} fits the single-row toolbar`);
  }
  const canvas = $('#screen-canvas').getBoundingClientRect();
  const surface = $('.screen-surface').getBoundingClientRect();
  assert(canvas.bottom <= surface.bottom && canvas.right <= surface.right, 'device canvas fits without page scrolling');
  const tree = $('#tree-panel').getBoundingClientRect();
  const details = $('#detail-column').getBoundingClientRect();
  assert(details.right <= tree.left, 'properties remain between device preview and hierarchy');
  assert(details.width >= layoutLimits.inspector.min, 'properties remain draggable to the minimum rail width');
  assert(tree.width >= layoutLimits.preview.min, 'hierarchy remains draggable to the minimum rail width');
  assert(getComputedStyle($('#preview-column')).borderRadius === '0px', 'desktop panels use flat continuous surfaces');
  assert(getComputedStyle($('.screen-surface')).backgroundImage === 'none', 'device backdrop has no decorative dot pattern');
  assert(getComputedStyle($('#tab-detail')).borderRadius === '0px', 'tabs stay distinct from rounded action buttons');
  assert(!$('.workspace-heading > span'), 'workspace headings contain no decorative English labels');
  assert($('#freeze-screen use').getAttribute('href') === '#i-pause', 'freeze control uses the shared SVG icon set');
  selectElement(fixtureElement);
  assert($('.copy-property use').getAttribute('href') === '#i-copy', 'property copy uses a consistent vector icon');
  const expectedChrome = document.documentElement.dataset.theme === 'light' ? '#f5f8fc' : '#0f2b48';
  assert(document.querySelector('meta[name="theme-color"]').content === expectedChrome, 'browser chrome follows the original blue theme');
  assert($('#detail-column').compareDocumentPosition($('#tree-panel')) & Node.DOCUMENT_POSITION_FOLLOWING, 'DOM order matches the visual properties-to-hierarchy order');
  const inspectorWidth = layoutWidths.inspector;
  state.resizing = {panel: 'inspector', startX: 400, startWidth: inspectorWidth};
  resizePanel(410);
  state.resizing = null;
  assert(layoutWidths.inspector >= inspectorWidth, 'dragging the central properties divider right expands the inspector');
  layoutWidths.inspector = inspectorWidth;
  applyLayoutWidths();
  if ($('#preview-column').clientWidth >= 360) {
    assert(getComputedStyle($('#locator-mode .mode-name')).display !== 'none', 'wide preview exposes readable mode names');
  }
  const tab = $('#tab-detail');
  tab.focus();
  tab.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true}));
  assert(document.activeElement === $('#tab-color') && $('#tab-color').getAttribute('aria-selected') === 'true', 'tab arrow navigation keeps focus and selection synchronized');
  activateTab('detail');

  for (const [trigger, dialog, close] of [
    ['open-settings', 'settings-dialog', 'close-settings'],
    ['import-capture', 'capture-library', 'close-capture-library'],
  ]) {
    $(`#${trigger}`).focus();
    await click(trigger);
    const modal = $(`#${dialog}`);
    const title = document.getElementById(modal.getAttribute('aria-labelledby'));
    assert(modal.open && title && modal.contains(title) && title.textContent.trim(), `${dialog} has an accessible title`);
    assert(modal.contains(document.activeElement), `${dialog} receives initial focus`);
    assert(document.documentElement.scrollWidth <= innerWidth, `${dialog} fits the viewport`);
    await click(close);
    assert(document.activeElement === $(`#${trigger}`), `${dialog} returns focus to its trigger`);
  }

  const before = captures();
  await action('home');
  assert(state.snapshotStale && $('#snapshot-notice').classList.contains('stale'), 'device operations persistently mark the snapshot stale');
  assert($('#snapshot-notice').getBoundingClientRect().height <= 30, 'stale warning cannot grow the layout vertically');
  assert($('#snapshot-status').title.includes('已过期'), 'full stale warning remains available without wrapping');
  assert(captures() === before, 'stale warning does not force an automatic recapture');
  selectElement(fixtureElement);
  assert(state.snapshotStale, 'selection does not dismiss the stale warning');
  await click('refresh-snapshot');
  assert(!state.snapshotStale && !$('#snapshot-notice').classList.contains('stale'), 'nearby refresh clears the warning after capture succeeds');

  const fetchBefore = window.fetch;
  try {
    window.fetch = async (url, options) => url === '/internal/action'
      ? {ok: false, status: 502, json: async () => ({detail: 'simulated action failure'})}
      : fetchBefore(url, options);
    await action('home');
    assert(!state.snapshotStale, 'failed device operations do not mark a snapshot stale');
  } finally { window.fetch = fetchBefore; }
  let releaseAction;
  try {
    window.fetch = (url, options) => url === '/internal/action'
      ? new Promise(resolve => { releaseAction = resolve; })
      : fetchBefore(url, options);
    const pending = action('home');
    await refreshScreen();
    releaseAction({ok: true, json: async () => ({message: '完成'})});
    await pending;
    assert(!state.snapshotStale, 'an earlier action response cannot mark a newer capture stale');
  } finally { window.fetch = fetchBefore; }

  const originalTree = state.tree;
  const originalExpanded = state.treeExpanded;
  const leaves = Array.from({length: 3000}, (_, index) => ({
    ...fixtureElement, xpath: `/quality/group/node[${index + 1}]`, children: [],
  }));
  const branch = {xpath: '/quality/group', text: '分组', children: leaves};
  state.tree = {xpath: '/quality', text: '根节点', children: [branch]};
  state.treeExpanded = new Set(['/quality']);
  const started = performance.now();
  renderHierarchy();
  const elapsed = performance.now() - started;
  assert($('#tree-list').querySelectorAll('.tree-row').length === 2, 'collapsed branches do not build thousands of hidden DOM rows');
  const toggle = [...$('#tree-list').querySelectorAll('.tree-row')].find(row => row.dataset.xpath === branch.xpath).querySelector('button.tree-toggle');
  assert(toggle.getAttribute('aria-expanded') === 'false', 'collapsed branch exposes its state');
  toggle.click();
  assert(toggle.getAttribute('aria-expanded') === 'true' && $('#tree-list').querySelectorAll('.tree-row').length === 3002, 'expanding lazily builds all child rows');
  const leaf = [...$('#tree-list').querySelectorAll('.tree-row')].find(row => row.dataset.xpath === leaves[0].xpath);
  assert(leaf.querySelector('.tree-toggle').tagName === 'SPAN' && leaf.querySelectorAll('button').length === 1, 'leaf has no misleading expansion control');
  toggle.click();
  assert(toggle.getAttribute('aria-expanded') === 'false', 'collapse updates the accessible state');
  renderHierarchy();
  selectElement(leaves.at(-1), 'search');
  const selectedRow = [...$('#tree-list').querySelectorAll('.tree-row.active')].find(row => row.dataset.xpath === leaves.at(-1).xpath);
  assert(selectedRow && selectedRow.getBoundingClientRect().height > 0, 'search reveals selections in unbuilt collapsed branches');
  assert([...$('#tree-list').querySelectorAll('button.tree-toggle')].every(button => button.getAttribute('aria-expanded') === 'true'), 'revealing a selection updates all ancestor expansion states');
  document.body.dataset.collapsedTreeRenderMs = elapsed.toFixed(2);
  state.tree = originalTree;
  state.treeExpanded = originalExpanded;
  clearSelection();
  renderHierarchy();
  await click('collapse-tree');
  await click('expand-tree');
  assert($('#tree-list').querySelectorAll('.tree-row').length > 2, 'expand-all still materializes the complete hierarchy');

  await action('home');
  await disconnect();
  assert(state.snapshotStale && !$('#snapshot-notice').classList.contains('hidden'), 'disconnect preserves snapshot freshness information');
  assert($('#snapshot-status').textContent.includes('离线快照') && $('#refresh-snapshot').classList.contains('hidden'), 'offline analysis never offers an unavailable device refresh');
  await click('import-capture');
  await click('choose-import-image');
  await click('choose-import-xml');
  await click('confirm-import');
  const importDeadline = Date.now() + 10000;
  while ($('#capture-library').open && Date.now() < importDeadline) await tick();
  assert(!$('#capture-library').open, 'import completes before checking the restored snapshot');
  assert(state.importedFiles && !state.connected && !state.snapshotStale, 'imported snapshots do not inherit stale state from the device');
  assert($('#snapshot-status').textContent.includes('离线导入'), 'imported data is visibly distinguished from device captures');
  await connect('demo-device');
  assert(!state.snapshotStale && !$('#refresh-snapshot').classList.contains('hidden'), 'reconnect restores fresh device analysis');
  assert(document.documentElement.scrollWidth <= innerWidth, 'all UI quality interactions preserve viewport fit');
  assert(fixtureErrors.length === 0, fixtureErrors.join('; '));
}
