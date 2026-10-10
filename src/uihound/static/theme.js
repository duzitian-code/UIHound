(function () {
  'use strict';
  const theme = {
    error: null,
    set(value, persist = true) {
      if (!['light', 'dark'].includes(value)) throw new Error('Invalid theme');
      document.documentElement.dataset.theme = value;
      document.querySelector('meta[name="theme-color"]').content = value === 'light' ? '#f5f8fc' : '#0f2b48';
      if (persist) {
        try {
          localStorage.setItem('uihound-theme', value);
        } catch (error) {
          this.error = `主题已切换，但无法保存偏好：${error.message}`;
        }
      }
    },
  };
  let saved = 'light';
  try {
    saved = localStorage.getItem('uihound-theme') || saved;
  } catch (error) {
    theme.error = `无法读取主题偏好：${error.message}`;
  }
  theme.set(saved === 'dark' ? 'dark' : 'light', false);
  window.UiHoundTheme = theme;
}());
