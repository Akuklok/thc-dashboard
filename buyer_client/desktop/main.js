// TopTenBuyer.ai - always-on desktop shell around the live web app.
// Tray icon + global hotkey + always-on-top, and one-tap whole-screen capture.
const { app, BrowserWindow, Tray, Menu, globalShortcut, nativeImage, session,
        desktopCapturer, Notification } = require('electron');
const path = require('path');

const APP_URL = 'https://topten-buyer.onrender.com';
// embedded 32px icon so the tray icon ALWAYS shows (no file-path dependency in the packaged app)
const TRAY_ICON = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAADoUlEQVR4nMVXW2hUVxRd59xHnDtDE+MDJzGJJZgqilYCFXw0VQqlDYV+iP0oQmxTH/RDLC2mgj+N7Udb0A8RfFDSFj8UWkJKQkrLNPhGq0GLFDW0Pht8JGo1k+Q+zil7TxKasTMx80gW3Dtnzr7nrLXP3mffcwWGEK2uduIhfyOkroPGCwAKkFsMQuAylGhy+s193efOxalT0C20bEmJZarvhMBqaACabnmAEMyoNWKeL9f1n+z8W5DnfY7bKg3jFR2o/BAn6zAkVBB0hON2rXziuBuElBNGTiAu4iRuKYB6XvaJhuZo1JOAqrzFPK0ATQKqJCAsTBqEJTHJMMfzcKACuJ4H0zBgmaMXbtB1oVQikaWUKLDt3ApwPQ+VZRVoWL8Jxzt/wzc/fg/bSojwfB+vLXsZtStWMflPJ4+i7cSvMAwDIlFqshMw4A4iHHLwxZYGvL68BlppfN18BLAsttW9uQZ7tzdCUqEB8N5ba/HRrs+x5/C3mGKnL6hyLHKtNZYufBEtuw8wOa+G7/EvLXnxc4X45N3N0Eph3Y4PsebjDzgc2+o2ITp9JoctYwF+EGBG8TS072nCSwsW4+yli0l2H/Ofn4uKaCmu3ryOlo5f0Hosht+7LmPG1GIsnjsPvp+FACEEL+sPsXbU1L+NQ23No+yBUiibFeX23d4e9lZD407PPe4rj5aOJGZGAgwpeeL3G7fj1IVO2FZSZmsgEnK46XouhyvRToQo4jgsKOsktC2Lw/EURCJM7AnV1GGvZMKvhC39LpDIAhSi3kcPuR0OhSGF5L6IE+a++w96+Q2cs0KUDEMa6Lp5jetAZWkZphYWon9gAFXlczj2f/zVxUUrZwKkkKOWmCa/euMafj59DG+sWIVDn+3mXCibVYLY2VO49CcJMHMjQAiBf/oe49adbvQ8fMD/6aIk2/rVThb16tLlLPLo+TPY8uWnCIIAlpmeQhSuXPTM72KarMCy4QU+F5thULLRjplTMptFXe++zTthLPJxC6BtRtew98k28lgPhSbZngrmGIy810fU8kWnyqcPrmQz5VDC/dfOg0QGArSGWRSBVRQeJWJckID/KA6v93FKEWa68UIKPsFmemKj8RjaMalgph4tWDmrzxbpQ6C9lOfCZ0ykzKE9qYEr+Sf6H3ANwRUScHCM90WeBHBuH5SRuL1fK9VByTZh3JTYSnUQt6SvVC8w31FKx0BZm89w0NxSgLiIk7hH2Cbr8/xfku95l/xC+1QAAAAASUVORK5CYII=';

let win = null, tray = null, onTop = true, notified = false;

function trayImage() {
  const img = nativeImage.createFromDataURL(TRAY_ICON);
  return img.isEmpty() ? nativeImage.createEmpty() : img;
}

function createWindow() {
  win = new BrowserWindow({
    width: 480, height: 860, show: true, alwaysOnTop: onTop,
    title: 'TopTenBuyer.ai',
    icon: trayImage(),
    webPreferences: { contextIsolation: true }
  });
  win.loadURL(APP_URL);
  win.on('close', (e) => {
    if (!app.isQuitting) {
      e.preventDefault(); win.hide();
      if (!notified) {   // first time: tell them it's still running and how to get it back
        notified = true;
        try { new Notification({ title: 'TopTenBuyer.ai is still running',
          body: 'It lives in your taskbar tray. Press Ctrl+Shift+B to open it anytime.' }).show(); } catch (e) {}
      }
    }
  });
}

function toggle() {
  if (!win) return createWindow();
  if (win.isVisible() && win.isFocused()) win.hide();
  else { win.show(); win.focus(); }
}

function buildTray() {
  tray = new Tray(trayImage());
  tray.setToolTip('TopTenBuyer.ai - click to open (Ctrl+Shift+B)');
  const menu = Menu.buildFromTemplate([
    { label: 'Open TopTenBuyer.ai  (Ctrl+Shift+B)', click: () => { if (win) { win.show(); win.focus(); } else createWindow(); } },
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
    // allow our own app to capture the screen, and auto-pick the whole screen (no picker)
    try { session.defaultSession.setPermissionRequestHandler((wc, perm, cb) => cb(true)); } catch (e) {}
    try {
      session.defaultSession.setDisplayMediaRequestHandler((request, callback) => {
        desktopCapturer.getSources({ types: ['screen'] }).then((sources) => {
          callback(sources.length ? { video: sources[0] } : {});
        }).catch(() => callback({}));
      });
    } catch (e) { /* older electron falls back to the browser picker */ }
    createWindow();
    buildTray();
    globalShortcut.register('CommandOrControl+Shift+B', toggle);
    try { new Notification({ title: 'TopTenBuyer.ai is running',
      body: 'Find it in your taskbar tray (the ^ arrow), or press Ctrl+Shift+B anytime.' }).show(); } catch (e) {}
  });
  app.on('window-all-closed', () => { /* keep running in the tray */ });
  app.on('will-quit', () => globalShortcut.unregisterAll());
}
