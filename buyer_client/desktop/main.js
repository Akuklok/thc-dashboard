// TopTenBuyer.ai - always-on desktop shell around the live web app.
// Lives in the tray, summoned by a global hotkey, floats on top, and captures the
// whole screen in one tap for the assistant's "Screen" feature.
const { app, BrowserWindow, Tray, Menu, globalShortcut, nativeImage, session, desktopCapturer } = require('electron');
const path = require('path');

const APP_URL = 'https://topten-buyer.onrender.com';
let win = null, tray = null, onTop = true;

function createWindow() {
  win = new BrowserWindow({
    width: 480, height: 860, show: true, alwaysOnTop: onTop,
    title: 'TopTenBuyer.ai',
    icon: path.join(__dirname, 'build', 'icon.png'),
    webPreferences: { contextIsolation: true }
  });
  win.loadURL(APP_URL);
  win.on('close', (e) => { if (!app.isQuitting) { e.preventDefault(); win.hide(); } });
}

function toggle() {
  if (!win) return createWindow();
  if (win.isVisible() && win.isFocused()) { win.hide(); }
  else { win.show(); win.focus(); }
}

function buildTray() {
  let img = nativeImage.createFromPath(path.join(__dirname, 'build', 'icon.png'));
  if (!img.isEmpty()) img = img.resize({ width: 18, height: 18 });
  tray = new Tray(img.isEmpty() ? nativeImage.createEmpty() : img);
  tray.setToolTip('TopTenBuyer.ai');
  const menu = Menu.buildFromTemplate([
    { label: 'Show / Hide  (Ctrl+Shift+B)', click: toggle },
    { type: 'checkbox', label: 'Always on top', checked: onTop,
      click: (mi) => { onTop = mi.checked; if (win) win.setAlwaysOnTop(onTop); } },
    { type: 'checkbox', label: 'Start with computer', checked: app.getLoginItemSettings().openAtLogin,
      click: (mi) => app.setLoginItemSettings({ openAtLogin: mi.checked }) },
    { type: 'separator' },
    { label: 'Quit', click: () => { app.isQuitting = true; app.quit(); } }
  ]);
  tray.setContextMenu(menu);
  tray.on('click', toggle);
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => { if (win) { win.show(); win.focus(); } });
  app.whenReady().then(() => {
    // one-tap whole-screen capture for the assistant's "Screen" button (no picker)
    try {
      session.defaultSession.setDisplayMediaRequestHandler((request, callback) => {
        desktopCapturer.getSources({ types: ['screen'] }).then((sources) => {
          callback(sources.length ? { video: sources[0] } : {});
        }).catch(() => callback({}));
      });
    } catch (e) { /* older electron: falls back to the browser picker */ }
    createWindow();
    buildTray();
    globalShortcut.register('CommandOrControl+Shift+B', toggle);
  });
  app.on('window-all-closed', () => { /* keep running in the tray */ });
  app.on('will-quit', () => globalShortcut.unregisterAll());
}
