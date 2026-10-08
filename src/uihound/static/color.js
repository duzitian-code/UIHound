(function () {
  'use strict';

  const MAGNIFIER_PIXELS = 11;
  const HISTORY_LIMIT = 100;

  function clampByte(value) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.min(255, Math.max(0, Math.round(number))) : 0;
  }

  function rgbToColor(red, green, blue) {
    const rgb = [clampByte(red), clampByte(green), clampByte(blue)];
    const [r, g, b] = rgb.map(value => value / 255);
    const maximum = Math.max(r, g, b);
    const minimum = Math.min(r, g, b);
    const delta = maximum - minimum;
    let hue = 0;

    if (delta !== 0) {
      if (maximum === r) hue = 60 * (((g - b) / delta) % 6);
      else if (maximum === g) hue = 60 * ((b - r) / delta + 2);
      else hue = 60 * ((r - g) / delta + 4);
      if (hue < 0) hue += 360;
    }

    const saturation = maximum === 0 ? 0 : delta / maximum;
    const roundedHue = Math.round(hue) % 360;
    const hsb = [roundedHue, Math.round(saturation * 100), Math.round(maximum * 100)];
    // OpenCV stores hue in half-degrees. RGB8 conversion can differ by one due to rounding.
    const opencv = [
      Math.min(179, Math.round(hue / 2)),
      Math.round(saturation * 255),
      Math.round(maximum * 255),
    ];
    const hex = `#${rgb.map(value => value.toString(16).padStart(2, '0')).join('')}`.toUpperCase();
    return { hex, rgb, hsb, opencv };
  }

  function appendTextElement(parent, tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = text;
    parent.append(element);
    return element;
  }

  class ColorInspector {
    constructor({ canvas, magnifier, detail, history, notify } = {}) {
      this.canvas = canvas || null;
      this.magnifier = magnifier || null;
      this.detail = detail || null;
      this.historyContainer = history || null;
      this.notify = typeof notify === 'function' ? notify : () => {};
      this.current = null;
      this.history = [];
      this._renderEmpty();
    }

    sample(x, y) {
      const width = this.canvas?.width || 0;
      const height = this.canvas?.height || 0;
      if (!width || !height) {
        this.notify('当前没有可取色的截图');
        return null;
      }

      const pixelX = Math.min(width - 1, Math.max(0, Math.round(Number(x) || 0)));
      const pixelY = Math.min(height - 1, Math.max(0, Math.round(Number(y) || 0)));
      let data;
      try {
        data = this.canvas.getContext('2d', { willReadFrequently: true })
          .getImageData(pixelX, pixelY, 1, 1).data;
      } catch (error) {
        this.notify(`读取像素失败：${error.message}`);
        return null;
      }

      const color = rgbToColor(data[0], data[1], data[2]);
      this.current = {
        x: pixelX,
        y: pixelY,
        ratio: [pixelX / width, pixelY / height],
        normalized: [pixelX / width, pixelY / height],
        ...color,
      };
      this._drawMagnifier();
      this._renderDetail();
      return this.current;
    }

    confirm() {
      if (!this.current) {
        this.notify('请先选择一个像素');
        return null;
      }

      const saved = {
        ...this.current,
        ratio: [...this.current.ratio],
        normalized: [...this.current.normalized],
        rgb: [...this.current.rgb],
        hsb: [...this.current.hsb],
        opencv: [...this.current.opencv],
      };
      this.history.unshift(saved);
      if (this.history.length > HISTORY_LIMIT) {
        this.history.pop();
        this.historyContainer?.lastElementChild?.remove();
      }
      if (this.historyContainer) this.historyContainer.prepend(this._createRecord(saved));
      return saved;
    }

    nudge(dx, dy) {
      if (!this.current) return null;
      return this.sample(this.current.x + Number(dx || 0), this.current.y + Number(dy || 0));
    }

    clear() {
      this.history = [];
      if (this.historyContainer) this.historyContainer.replaceChildren();
    }

    reset() {
      this.current = null;
      this.clear();
      this._renderEmpty();
    }

    _renderEmpty() {
      if (this.detail) this.detail.textContent = '移动指针到截图上预览像素';
      const context = this.magnifier?.getContext('2d');
      if (context) {
        context.clearRect(0, 0, this.magnifier.width, this.magnifier.height);
        context.fillStyle = '#0a1522';
        context.fillRect(0, 0, this.magnifier.width, this.magnifier.height);
      }
    }

    _drawMagnifier() {
      if (!this.current || !this.magnifier) return;
      const target = this.magnifier;
      const context = target.getContext('2d');
      const radius = Math.floor(MAGNIFIER_PIXELS / 2);
      const cellWidth = target.width / MAGNIFIER_PIXELS;
      const cellHeight = target.height / MAGNIFIER_PIXELS;
      const left = this.current.x - radius;
      const top = this.current.y - radius;
      const sourceX = Math.max(0, left);
      const sourceY = Math.max(0, top);
      const sourceRight = Math.min(this.canvas.width, this.current.x + radius + 1);
      const sourceBottom = Math.min(this.canvas.height, this.current.y + radius + 1);
      const sourceWidth = sourceRight - sourceX;
      const sourceHeight = sourceBottom - sourceY;

      context.clearRect(0, 0, target.width, target.height);
      context.fillStyle = '#0a1522';
      context.fillRect(0, 0, target.width, target.height);
      context.imageSmoothingEnabled = false;
      context.drawImage(
        this.canvas,
        sourceX, sourceY, sourceWidth, sourceHeight,
        (sourceX - left) * cellWidth, (sourceY - top) * cellHeight,
        sourceWidth * cellWidth, sourceHeight * cellHeight,
      );

      const centerX = radius * cellWidth;
      const centerY = radius * cellHeight;
      context.lineWidth = 1;
      context.strokeStyle = 'rgba(0, 0, 0, .85)';
      context.strokeRect(centerX - 1, centerY - 1, cellWidth + 2, cellHeight + 2);
      context.strokeStyle = '#ffffff';
      context.strokeRect(centerX + 0.5, centerY + 0.5, cellWidth - 1, cellHeight - 1);
      context.beginPath();
      context.moveTo(centerX + cellWidth / 2, 0);
      context.lineTo(centerX + cellWidth / 2, centerY);
      context.moveTo(centerX + cellWidth / 2, centerY + cellHeight);
      context.lineTo(centerX + cellWidth / 2, target.height);
      context.moveTo(0, centerY + cellHeight / 2);
      context.lineTo(centerX, centerY + cellHeight / 2);
      context.moveTo(centerX + cellWidth, centerY + cellHeight / 2);
      context.lineTo(target.width, centerY + cellHeight / 2);
      context.stroke();
    }

    _renderDetail() {
      if (!this.detail || !this.current) return;
      const value = this.current;
      this.detail.replaceChildren();

      const swatchRow = document.createElement('div');
      swatchRow.className = 'color-property';
      const swatch = document.createElement('span');
      swatch.className = 'color-swatch';
      swatch.style.backgroundColor = value.hex;
      swatchRow.append(swatch);
      appendTextElement(swatchRow, 'strong', '', value.hex);
      this.detail.append(swatchRow);

      this._appendProperty(this.detail, '像素', `x ${value.x}, y ${value.y}`);
      this._appendProperty(
        this.detail,
        '归一化',
        `${value.ratio[0].toFixed(6)}, ${value.ratio[1].toFixed(6)}`,
      );
      this._appendProperty(this.detail, 'HEX', value.hex);
      this._appendProperty(this.detail, 'RGB', `rgb(${value.rgb.join(', ')})`);
      this._appendProperty(
        this.detail,
        'HSB / HSV',
        `h ${value.hsb[0]}°, s ${value.hsb[1]}%, b ${value.hsb[2]}%`,
      );
      this._appendProperty(
        this.detail,
        'OpenCV HSV',
        `H ${value.opencv[0]}, S ${value.opencv[1]}, V ${value.opencv[2]}`,
      );
    }

    _appendProperty(parent, label, value) {
      const row = document.createElement('div');
      row.className = 'color-property';
      appendTextElement(row, 'span', 'property-name', label);
      appendTextElement(row, 'span', 'property-value', value);
      row.append(this._copyButton(value, `复制 ${label}`));
      parent.append(row);
    }

    _createRecord(value) {
      const record = document.createElement('div');
      record.className = 'color-record';
      const swatch = document.createElement('span');
      swatch.className = 'color-swatch';
      swatch.style.backgroundColor = value.hex;
      record.append(swatch);
      const representations = [
        ['坐标', `(${value.x}, ${value.y})`],
        ['HEX', value.hex],
        ['RGB', `rgb(${value.rgb.join(', ')})`],
        ['HSB', `h ${value.hsb[0]}°, s ${value.hsb[1]}%, b ${value.hsb[2]}%`],
      ];
      for (const [label, text] of representations) {
        const group = document.createElement('span');
        group.className = 'color-record-value';
        group.textContent = text;
        group.append(this._copyButton(text, `复制 ${label}`));
        record.append(group);
      }
      return record;
    }

    _copyButton(value, label) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'copy-property';
      button.setAttribute('aria-label', label);
      button.title = label;
      button.textContent = '⧉';
      button.addEventListener('click', async () => {
        if (!navigator.clipboard?.writeText) {
          this.notify('当前浏览器不支持复制到剪贴板');
          return;
        }
        try {
          await navigator.clipboard.writeText(value);
          this.notify('已复制到剪贴板');
        } catch (error) {
          this.notify(`复制失败：${error.message}`);
        }
      });
      return button;
    }
  }

  window.rgbToColor = rgbToColor;
  window.ColorInspector = ColorInspector;
}());
