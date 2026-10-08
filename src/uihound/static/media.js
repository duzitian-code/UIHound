class MediaLibrary {
  constructor({ kind, root, count, empty, directory, notify }) {
    Object.assign(this, { kind, root, count, empty, directory, notify });
    this.files = new Map();
    this.serial = 0;
  }

  async request(operation, data) {
    const response = await fetch(`/internal/media/${operation}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: this.kind, ...data }),
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.detail || '文件操作失败');
    return result;
  }

  async refresh() {
    const serial = ++this.serial;
    const response = await fetch(`/internal/media/list?kind=${this.kind}&directory=${encodeURIComponent(this.directory())}`);
    const result = await response.json();
    if (!response.ok) throw Error(result.detail || '读取文件列表失败');
    if (serial !== this.serial) return;
    this.files = new Map(result.files.map(file => [file.path, file]));
    this.render();
  }

  add(file) {
    ++this.serial;
    this.files.delete(file.path);
    this.files = new Map([[file.path, file], ...this.files]);
    this.render();
  }

  render() {
    this.root.querySelectorAll('video').forEach(video => {
      video.pause();
      video.removeAttribute('src');
      video.load();
    });
    this.root.replaceChildren();
    for (const file of this.files.values()) {
      const card = document.createElement('article');
      card.className = 'snapshot-preview';
      card.dataset.path = file.path;
      const preview = document.createElement(this.kind === 'snapshot' ? 'img' : 'video');
      preview.src = file.image ? `data:image/png;base64,${file.image}` : file.url;
      if (this.kind === 'recording') {
        preview.controls = true;
        preview.preload = 'metadata';
      } else {
        preview.alt = file.name || '截图预览';
        preview.loading = 'lazy';
      }
      const name = document.createElement('div');
      name.className = 'snapshot-name';
      name.textContent = file.name || file.path.split(/[\\/]/).pop();
      name.title = file.path;
      const buttons = document.createElement('div');
      buttons.className = 'media-actions';
      for (const [label, handler] of [
        ['复制文件', async () => {
          if (this.kind === 'snapshot') {
            const response = await fetch('/internal/snapshots/copy-file', {
              method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: file.path }),
            });
            const result = await response.json();
            if (!response.ok) throw Error(result.detail || '复制失败');
            this.notify(result.message);
          } else this.notify((await this.request('copy-file', { path: file.path })).message);
        }],
        ['删除', () => this.remove([file.path])],
      ]) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'button button-muted';
        button.textContent = label;
        button.addEventListener('click', async () => {
          button.disabled = true;
          try { await handler(); } catch (error) { this.notify(error.message, true); }
          finally { button.disabled = false; }
        });
        buttons.append(button);
      }
      card.append(preview, buttons, name);
      this.root.append(card);
    }
    this.count.textContent = this.files.size;
    this.empty.classList.toggle('hidden', this.files.size > 0);
    this.root.parentElement.classList.remove('hidden');
  }

  async remove(paths = [...this.files.keys()]) {
    if (this.deleting) return this.notify('正在删除，请稍候');
    if (!paths.length) return this.notify('当前没有可删除的文件');
    if (!window.confirm(`将永久删除这 ${paths.length} 个磁盘文件，确定继续？`)) return;
    this.deleting = true;
    try {
      const result = await this.request('delete', { paths });
      ++this.serial;
      for (const path of result.deleted) this.files.delete(path);
      this.render();
      if (result.errors?.length) this.notify(`部分删除失败：${result.errors.map(error => error.message).join('；')}`, true);
      else this.notify(`已删除 ${result.deleted.length} 个文件`);
    } finally { this.deleting = false; }
  }
}
window.MediaLibrary = MediaLibrary;
