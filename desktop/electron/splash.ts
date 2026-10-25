/** Inline splash/error page. It uses the same preload bridge as the app window. */
export function splashHtml(): string {
  return `<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<style>
  :root { color-scheme: light dark; --fg:#1d1d1f; --muted:#6e6e73; --bg:#f5f5f7; --accent:#3b5bdb; }
  @media (prefers-color-scheme: dark) { :root { --fg:#f5f5f7; --muted:#a1a1a6; --bg:#1c1c1e; --accent:#7c93f5; } }
  html,body { margin:0; height:100%; background:var(--bg); color:var(--fg);
    font:13px -apple-system, BlinkMacSystemFont, system-ui, sans-serif; -webkit-user-select:none; }
  body { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:14px;
    -webkit-app-region:drag; text-align:center; padding:0 28px; }
  h1 { font-size:20px; font-weight:600; margin:0; letter-spacing:-0.01em; }
  #msg { color:var(--muted); min-height:18px; max-width:340px; line-height:1.4; }
  .spin { width:18px; height:18px; border:2px solid var(--muted); border-top-color:var(--accent);
    border-radius:50%; animation:s 0.9s linear infinite; }
  @keyframes s { to { transform:rotate(360deg); } }
  #actions { display:none; gap:8px; -webkit-app-region:no-drag; }
  button { font:inherit; padding:5px 12px; border-radius:7px; border:1px solid var(--muted);
    background:transparent; color:var(--fg); cursor:pointer; }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
  body.failed .spin { display:none; } body.failed #actions { display:flex; }
</style></head>
<body>
  <h1>Reachout</h1>
  <div class="spin" role="progressbar" aria-label="Starting"></div>
  <div id="msg" role="status" aria-live="polite">Starting…</div>
  <div id="actions">
    <button class="primary" id="retry">Try again</button>
    <button id="logs">View logs</button>
    <button id="quit">Quit</button>
  </div>
<script>
  const b = window.reachout, msg = document.getElementById('msg');
  const show = (s) => { msg.textContent = s.message; document.body.classList.toggle('failed', s.state === 'failed'); };
  b.getBackendStatus().then(show);
  b.onBackendStatus(show);
  document.getElementById('retry').onclick = () => { document.body.classList.remove('failed'); msg.textContent = 'Starting…'; b.retryBackend(); };
  document.getElementById('logs').onclick = () => b.openLogs();
  document.getElementById('quit').onclick = () => b.quit();
</script></body></html>`
}
