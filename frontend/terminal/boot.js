/* Boot guard: if the runtime script does not start (server restarting, token rotated), show a reconnect notice and retry instead of a blank page. */
(() => {
  'use strict';
  const start = Date.now();
  const check = () => {
    if (window.__terminalReady) return;
    const body = document.getElementById('body');
    if (body && !body.childElementCount) {
      const s = Math.round((Date.now() - start) / 1000);
      body.innerHTML = `<div class="empty"><b>engine restarting</b> · reconnecting… ${s}s · the page reloads itself when the server is back</div>`;
    }
    fetch('/api/health', {cache: 'no-store'}).then(r => { if (r.ok) location.reload(); }).catch(() => {});
    setTimeout(check, 3000);
  };
  setTimeout(check, 2500);
})();
