/* Video localization: giao diện 4 bước */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const BAND_COLORS = ['#60a5fa', '#f472b6', '#fbbf24'];
const LANGS = { eng: 'Tiếng Anh', chi_sim: 'Trung giản thể', chi_tra: 'Trung phồn thể', kor: 'Hàn', jpn: 'Nhật', vie: 'Việt', tha: 'Thái', ind: 'Indonesia', fra: 'Pháp', spa: 'Tây Ban Nha', deu: 'Đức', rus: 'Nga' };


/* ---------------------------------------------------------------- địa chỉ máy xử lý */
const LOCAL = !!window.VL_EMBEDDED || ['127.0.0.1', 'localhost', '[::1]', ''].includes(location.hostname);
const lsGet = k => { try { return localStorage.getItem(k) || ''; } catch (e) { return ''; } };
const lsSet = (k, v) => { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch (e) { } };
const DEFAULT_SERVER = 'http://127.0.0.1:8765';
/* Mở bằng link dạng ?server=https://...&token=... (Colab in sẵn link này) thì tự lưu cấu hình rồi xoá khỏi thanh địa chỉ */
const QP = (() => {
  try {
    const q = new URLSearchParams(location.search), o = { server: (q.get('server') || '').trim().replace(/\/+$/, ''), token: (q.get('token') || '').trim() };
    if (o.server) lsSet('vl_server', o.server);
    if (o.token) lsSet('vl_token', o.token);
    if (o.server || o.token) history.replaceState(null, '', location.pathname);
    return o;
  } catch (e) { return { server: '', token: '' }; }
})();
const CFG = {
  base: LOCAL ? '' : (QP.server || lsGet('vl_server') || DEFAULT_SERVER),
  token: QP.token || lsGet('vl_token'),
};
const U = path => CFG.base + path + (CFG.token && path.startsWith('/api/') ? (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(CFG.token) : '');
const isLoopHost = h => ['127.0.0.1', 'localhost', '[::1]', '::1'].includes(h);
/* Safari và mọi trình duyệt trên iPhone/iPad (đều dùng WebKit) chặn trang https gọi http, kể cả 127.0.0.1 */
const STRICT_MIXED = /iPhone|iPad|iPod/.test(navigator.userAgent) || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1)
  || /^((?!chrome|chromium|android|crios|fxios|edg|opr).)*safari/i.test(navigator.userAgent);
function normBase(u) {
  u = (u || '').trim().replace(/\/+$/, '');
  if (u && !/^https?:\/\//i.test(u)) u = (isLoopHost(u.split(/[:/]/)[0]) || /^\d+\.\d+\.\d+\.\d+/.test(u) ? 'http://' : 'https://') + u;
  return u;
}
/* thử gọi /api/status, trả về { ok, status } hoặc { ok:false, why } giải thích bằng tiếng Việt */
async function probeServer(base, token) {
  let url;
  try { url = new URL((base || location.origin) + '/api/status'); } catch (e) { return { ok: false, why: 'Địa chỉ không hợp lệ. Ví dụ đúng: http://127.0.0.1:8765 hoặc https://xxxx.trycloudflare.com' }; }
  if (location.protocol === 'https:' && url.protocol === 'http:') {
    if (!isLoopHost(url.hostname)) return { ok: false, why: 'Trang https không được gọi tới địa chỉ http (trừ 127.0.0.1). Hãy dùng địa chỉ https, ví dụ Cloudflare Tunnel (xem README mục 9).' };
    if (STRICT_MIXED) return { ok: false, why: 'Safari và trình duyệt trên iPhone/iPad chặn trang https gọi http://127.0.0.1. Trên máy tính hãy mở thẳng http://127.0.0.1:8765, hoặc dùng Chrome/Edge; trên điện thoại hãy dùng địa chỉ https (Cloudflare Tunnel).' };
  }
  const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 8000);
  try {
    const r = await fetch(url, { headers: token ? { 'X-Access-Token': token } : {}, signal: ctl.signal, cache: 'no-store' });
    if (r.status === 401) return { ok: false, why: 'Máy xử lý yêu cầu mã truy cập: mã đang nhập bị sai hoặc để trống.' };
    if (!r.ok) return { ok: false, why: `Máy xử lý trả lỗi ${r.status}. Kiểm tra lại địa chỉ.` };
    let st = null; try { st = await r.json(); } catch (e) { }
    if (!st || !('ffmpeg' in st)) return { ok: false, why: 'Địa chỉ này trả lời nhưng không phải máy xử lý Video localization.' };
    return { ok: true, status: st };
  } catch (e) {
    if (e.name === 'AbortError') return { ok: false, why: 'Hết thời gian chờ: máy xử lý không trả lời sau 8 giây.' };
    return {
      ok: false, why: LOCAL ? 'Không gọi được máy xử lý. App có còn chạy không? Hãy mở lại start.bat / start.sh.'
        : `Không kết nối được. Kiểm tra: (1) app đang chạy (start.bat / start.sh); (2) địa chỉ đúng; (3) máy xử lý cho phép trang này: biến ALLOWED_ORIGINS phải có ${location.origin}; (4) nếu trình duyệt hỏi quyền truy cập thiết bị trong mạng cục bộ, hãy bấm Cho phép.`
    };
  } finally { clearTimeout(timer); }
}

const S = { status: null, p: null, step: 1, cur: 0, task: null, poll: null, saveT: null, pvT: null, pvN: 0, maxStep: 1 };

/* ---------------------------------------------------------------- tiện ích */
async function api(path, opt = {}) {
  const o = { ...opt };
  if (o.json !== undefined) { o.method = o.method || 'POST'; o.headers = { 'content-type': 'application/json' }; o.body = JSON.stringify(o.json); delete o.json; }
  o.headers = { ...(o.headers || {}), ...(CFG.token ? { 'X-Access-Token': CFG.token } : {}) };
  const r = await fetch(CFG.base + path, o);
  if (r.status === 401) { showConnect('Máy xử lý yêu cầu mã truy cập: mã đang nhập bị sai hoặc để trống.'); throw new Error('Sai hoặc thiếu mã truy cập.'); }
  if (!r.ok) {
    let m = r.statusText;
    try { m = (await r.json()).error || m; } catch (e) { }
    throw new Error(m);
  }
  const ct = r.headers.get('content-type') || '';
  return ct.includes('json') ? r.json() : r;
}
function toast(msg, err = false) {
  const t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : '');
  t.textContent = msg;
  $('#toasts').appendChild(t);
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 400); }, err ? 6500 : 3200);
}
const fmt = (s) => { s = Math.max(0, s || 0); const m = Math.floor(s / 60); return m + ':' + (s - m * 60).toFixed(1).padStart(4, '0'); };
const parseT = (v) => { v = String(v).trim().replace(',', '.'); if (v.includes(':')) { const [m, s] = v.split(':'); return (+m || 0) * 60 + (+s || 0); } return +v || 0; };
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const dur = (s) => { const m = Math.floor(s / 60); return m + ':' + String(Math.round(s % 60)).padStart(2, '0'); };
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function deepGet(o, path) { return path.split('.').reduce((a, k) => a?.[k], o); }
function deepSet(o, path, v) { const ks = path.split('.'); const l = ks.pop(); const t = ks.reduce((a, k) => (a[k] ??= {}), o); t[l] = v; }

function save(now = false) {
  if (!S.p) return;
  clearTimeout(S.saveT);
  const go = () => api(`/api/p/${S.p.id}/save`, { json: { name: S.p.name, bands: S.p.bands, primary: S.p.primary, ocr: S.p.ocr, cues: S.p.cues, settings: S.p.settings, translate: S.p.translate } }).catch(e => toast('Lưu lỗi: ' + e.message, true));
  if (now) return go();
  S.saveT = setTimeout(go, 500);
}

/* ---------------------------------------------------------------- điều hướng */
function setStep(n, force = false) {
  if (n > 1 && !S.p) return;
  if (!force && n === S.step) return;
  const cur = $('#s' + S.step), nxt = $('#s' + n);
  S.step = n;
  S.maxStep = Math.max(S.maxStep, n);
  cur.classList.add('out');
  setTimeout(() => {
    $$('.step').forEach(s => { s.hidden = true; s.classList.remove('out'); });
    nxt.hidden = false;
    nxt.style.animation = 'none'; void nxt.offsetWidth; nxt.style.animation = '';
    onEnter(n);
  }, force ? 0 : 230);
  updateStepper();
}
function updateStepper() {
  const btns = $$('#stepper button');
  btns.forEach(b => {
    const n = +b.dataset.step;
    b.classList.toggle('on', n === S.step);
    b.classList.toggle('done', !!S.p && n < S.step);
    b.disabled = n > 1 && !S.p;
  });
  const on = btns.find(b => +b.dataset.step === S.step), pill = $('#pill');
  if (on) { pill.style.left = on.offsetLeft + 'px'; pill.style.width = on.offsetWidth + 'px'; }
}
function onEnter(n) {
  if (n === 1) loadProjects();
  if (n === 2) enter2();
  if (n === 3) enter3();
  if (n === 4) enter4();
}
$('#stepper').addEventListener('click', e => { const b = e.target.closest('button'); if (b && !b.disabled) setStep(+b.dataset.step); });
window.addEventListener('resize', updateStepper);

/* ---------------------------------------------------------------- tác vụ nền */
function runTask(root, onDone, btn) {
  const bar = $('.bar i', root), pm = $('.pm', root), pp = $('.pp', root);
  root.hidden = false;
  btn?.classList.add('busy'); btn && (btn.disabled = true);
  $('[data-cancel]', root).onclick = () => api(`/api/p/${S.p.id}/task/cancel`, { method: 'POST' });
  clearInterval(S.poll);
  const tick = async () => {
    let t;
    try { t = await api(`/api/p/${S.p.id}/task`); } catch (e) { return; }
    bar.style.width = (t.progress * 100).toFixed(1) + '%';
    pm.textContent = t.message || '...';
    pp.textContent = Math.round(t.progress * 100) + '%';
    if (t.state !== 'running') {
      clearInterval(S.poll);
      btn?.classList.remove('busy'); btn && (btn.disabled = false);
      setTimeout(() => { root.hidden = true; }, t.state === 'done' ? 900 : 0);
      if (t.state === 'error') toast(t.error || 'Có lỗi xảy ra', true);
      else if (t.state === 'cancelled') toast('Đã huỷ');
      else onDone && onDone(t);
    }
  };
  S.poll = setInterval(tick, 700); tick();
}

/* ---------------------------------------------------------------- khởi động */
async function init() {
  const pr = await probeServer(CFG.base, CFG.token);
  if (!pr.ok) { showConnect(pr.why); return; }
  S.status = pr.status;
  $('#srvBtn').textContent = LOCAL ? 'Máy xử lý: trang này' : 'Máy xử lý: ' + CFG.base.replace(/^https?:\/\//, '');
  const st = S.status;
  $('#chips').innerHTML = [['ffmpeg', st.ffmpeg], ['libass', st.libass], ['Tesseract', st.tesseract]].map(([n, ok]) => `<span class="chip ${ok ? '' : 'bad'}" title="${ok ? 'Đã sẵn sàng' : 'Chưa cài, xem README'}">${n}</span>`).join('');
  if (!st.ffmpeg) toast('Chưa tìm thấy ffmpeg. Hãy cài ffmpeg rồi mở lại (xem README).', true);
  if (st.ffmpeg && !st.libass) toast('ffmpeg của bạn thiếu libass nên không chèn được phụ đề. Hãy dùng bản ffmpeg đầy đủ (xem README).', true);
  if (!st.tesseract) toast('Chưa tìm thấy Tesseract OCR. Cần để quét sub (xem README).', true);
  updateStepper();
  setStep(1, true);
  setTimeout(updateStepper, 80);
}

/* ---------------------------------------------------------------- bước 1 */
const mb = n => n >= 1073741824 ? (n / 1073741824).toFixed(1) + ' GB' : Math.max(1, Math.round(n / 1048576)) + ' MB';
let mediaPoll = null;
async function loadMedia(sync = false) {
  const box = $('#mediaBox');
  if (!S.status?.media) { box.hidden = true; return; }
  box.hidden = false;
  if (sync === true) await api('/api/media/sync', { method: 'POST' }).catch(() => { });
  const r = await api('/api/media').catch(() => ({ files: [], sync: {} }));
  const sy = r.sync || {};
  $('#mediaList').innerHTML = r.files.length ? r.files.map((m, i) => `<div class="proj" data-media="${esc(m.name)}" style="--i:${i}"><b>${esc(m.name)}</b><small>${mb(m.size)}</small></div>`).join('')
    : '<div class="empty">Chưa có video nào.' + (r.can_sync ? '<br>Hãy tải video vào thư mục Drive <b>video-localization/videos</b> rồi bấm "Làm mới".' : '') + '</div>';
  const st = $('#mediaSync');
  st.hidden = !sy.running && !sy.error;
  st.textContent = sy.error ? 'Lỗi chép video: ' + sy.error : `Đang chép từ Drive: ${sy.file || '...'} (${mb(sy.done || 0)} / ${mb(sy.total || 0)})`;
  $('#mediaRefresh').hidden = !r.can_sync;
  clearTimeout(mediaPoll);
  if (sy.running) mediaPoll = setTimeout(() => loadMedia(), 2000);
}
$('#mediaRefresh').onclick = () => loadMedia(true);
$('#mediaList').addEventListener('click', async e => {
  const c = e.target.closest('[data-media]'); if (!c || c.classList.contains('busy')) return;
  c.classList.add('busy');
  try { S.p = await api('/api/projects', { json: { media: c.dataset.media } }); S.maxStep = 2; toast('Đã tạo dự án'); setStep(2); }
  catch (err) { toast(err.message, true); } finally { c.classList.remove('busy'); }
});
async function loadProjects() {
  loadMedia();
  const list = await api('/api/projects').catch(() => []);
  const box = $('#projects');
  if (!list.length) { box.innerHTML = '<div class="empty">Chưa có dự án nào.<br>Hãy chọn video ở bên cạnh để bắt đầu.</div>'; return; }
  box.innerHTML = list.map((p, i) => `
    <div class="proj" data-id="${p.id}" style="--i:${i}">
      <b>${esc(p.name)}${p.has_final ? '<span class="badge">đã xuất</span>' : ''}</b>
      <button class="del" title="Xoá dự án" data-del="${p.id}">×</button>
      <small>${p.info.width}x${p.info.height} · ${dur(p.info.duration)} · ${p.cues} câu · ${p.translated} đã dịch</small>
    </div>`).join('');
}
$('#projects').addEventListener('click', async e => {
  const d = e.target.closest('[data-del]');
  if (d) {
    e.stopPropagation();
    if (!confirm('Xoá dự án này và các file đã xuất?')) return;
    await api('/api/p/' + d.dataset.del, { method: 'DELETE' });
    toast('Đã xoá dự án'); loadProjects(); return;
  }
  const c = e.target.closest('.proj');
  if (c) openProject(c.dataset.id);
});
async function openProject(id) {
  S.p = await api('/api/p/' + id);
  S.maxStep = S.p.cues.length ? 3 : 2;
  S.cur = 0;
  /* nếu đang có tác vụ chạy nền (vd. điện thoại khoá màn hình lúc đang quét/xuất) thì nối lại thanh tiến độ */
  const t = await api(`/api/p/${id}/task`).catch(() => null);
  const R = { ocr: [2, '#ocrProg', onOcrDone, '#ocrBtn'], translate: [3, '#trProg', onTrDone, '#trBtn'], render: [4, '#rdProg', onRenderDone, '#renderBtn'] }[t?.kind];
  if (t?.state === 'running' && R) {
    S.maxStep = 4; setStep(R[0]);
    setTimeout(() => runTask($(R[1]), R[2], $(R[3])), 300);
    return;
  }
  setStep(S.p.cues.length ? 3 : 2);
}
const drop = $('#drop');
['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', e => { const f = e.dataTransfer.files[0]; if (f) upload(f); });
$('#file').addEventListener('change', e => { if (e.target.files[0]) upload(e.target.files[0]); });
function upload(file) {
  const xhr = new XMLHttpRequest(), fd = new FormData();
  fd.append('file', file);
  $('#upbar').hidden = false;
  xhr.upload.onprogress = e => { if (e.lengthComputable) { const p = e.loaded / e.total; $('#upfill').style.width = (p * 100) + '%'; $('#uptxt').textContent = `Đang tải lên ${Math.round(p * 100)}% (${(e.loaded / 1048576).toFixed(0)} / ${(e.total / 1048576).toFixed(0)} MB)`; } };
  xhr.onload = () => {
    $('#upbar').hidden = true;
    let j = {}; try { j = JSON.parse(xhr.responseText); } catch (e) { }
    if (xhr.status !== 200) return toast(j.error || 'Tải lên lỗi', true);
    S.p = j; S.maxStep = 2; toast('Đã tạo dự án'); setStep(2);
  };
  xhr.onerror = () => { $('#upbar').hidden = true; toast('Tải lên lỗi', true); };
  xhr.open('POST', U('/api/projects')); xhr.send(fd);
}
$('#pathBtn').onclick = async () => {
  const path = $('#pathIn').value.trim(); if (!path) return toast('Hãy dán đường dẫn video', true);
  const b = $('#pathBtn'); b.disabled = true;
  try { S.p = await api('/api/projects', { json: { path } }); S.maxStep = 2; toast('Đã tạo dự án'); setStep(2); }
  catch (e) { toast(e.message, true); } finally { b.disabled = false; }
};

/* ---------------------------------------------------------------- bước 2 */
function frameUrl(t, w = 1280) { return U(`/api/p/${S.p.id}/frame?t=${t.toFixed(2)}&w=${w}&_=${Date.now() % 100000}`); }
function loadImg(img, skel, url) {
  skel?.classList.remove('off'); img.classList.remove('ready');
  const tmp = new Image();
  tmp.onload = () => { img.src = tmp.src; img.classList.add('ready'); skel?.classList.add('off'); };
  tmp.onerror = () => { skel?.classList.add('off'); };
  tmp.src = url;
}
function enter2() {
  const p = S.p, o = p.ocr;
  const sl = $('#tSlider'); sl.max = p.info.duration.toFixed(1);
  if (!S.t2) S.t2 = Math.min(p.info.duration * 0.3, p.info.duration);
  sl.value = S.t2; $('#tOut').textContent = fmt(S.t2);
  $('#o_fps').value = o.fps; $('#o_t0').value = o.t0 || ''; $('#o_t1').value = o.t1 || ''; $('#o_thr').value = o.thr; $('#o_seg').value = o.seg_min ?? 72;
  renderBands(); showFrame2();
}
const showFrame2 = debounce(() => loadImg($('#frame'), $('#skel'), frameUrl(S.t2)), 120);
$('#tSlider').addEventListener('input', e => { S.t2 = +e.target.value; $('#tOut').textContent = fmt(S.t2); showFrame2(); });
$$('[data-seek]').forEach(b => b.onclick = () => { S.t2 = Math.max(0, Math.min(S.p.info.duration, S.t2 + +b.dataset.seek)); $('#tSlider').value = S.t2; $('#tOut').textContent = fmt(S.t2); showFrame2(); });
['fps', 't0', 't1', 'thr', 'seg'].forEach(k => $('#o_' + k).addEventListener('input', e => {
  const v = e.target.value === '' ? null : +e.target.value;
  S.p.ocr[k === 'seg' ? 'seg_min' : k] = v; save();
}));

function renderBands() {
  const p = S.p, box = $('#bands');
  box.innerHTML = p.bands.map((b, i) => `
    <div class="bandcard" style="--c:${BAND_COLORS[i]};animation-delay:${i * 70}ms" data-i="${i}">
      <div class="r1">
        <label>Tên dòng<input type="text" data-k="name" value="${esc(b.name)}"></label>
        <label>Ngôn ngữ<select data-k="lang">${S.status.langs.map(l => `<option value="${l}" ${l === b.lang ? 'selected' : ''}>${LANGS[l] || l}</option>`).join('')}</select></label>
        <button class="xbtn" data-del title="Xoá dòng">×</button>
      </div>
      <div class="r2">
        <label>Bắt đầu: <b>${(b.y0 * 100).toFixed(1)}%</b><input type="range" min="40" max="100" step="0.2" data-k="y0" value="${b.y0 * 100}"></label>
        <label>Kết thúc: <b>${(b.y1 * 100).toFixed(1)}%</b><input type="range" min="40" max="100" step="0.2" data-k="y1" value="${b.y1 * 100}"></label>
      </div>
      <div class="r3"><label><input type="radio" name="primary" data-primary ${p.primary === i ? 'checked' : ''}> Dùng dòng này để dịch</label><span>${p.primary === i ? 'dòng chính' : 'dòng đối chiếu'}</span></div>
    </div>`).join('');
  $('#addBand').hidden = p.bands.length >= 3;
  drawBands();
}
function drawBands() {
  $('#bandLayer').innerHTML = S.p.bands.map((b, i) => `<div class="band" style="--c:${BAND_COLORS[i]};top:${b.y0 * 100}%;height:${(b.y1 - b.y0) * 100}%"><em>${esc(b.name)}</em></div>`).join('');
}
$('#bands').addEventListener('input', e => {
  const card = e.target.closest('.bandcard'); if (!card) return;
  const b = S.p.bands[+card.dataset.i], k = e.target.dataset.k;
  if (e.target.dataset.primary !== undefined) { S.p.primary = +card.dataset.i; renderBands(); save(); return; }
  if (!k) return;
  if (k === 'y0' || k === 'y1') {
    b[k] = +e.target.value / 100;
    if (b.y1 - b.y0 < 0.01) { if (k === 'y0') b.y0 = b.y1 - 0.01; else b.y1 = b.y0 + 0.01; }
    e.target.parentElement.querySelector('b').textContent = (b[k] * 100).toFixed(1) + '%';
  } else b[k] = e.target.value;
  drawBands(); save();
});
$('#bands').addEventListener('click', e => {
  if (!e.target.closest('[data-del]')) return;
  const i = +e.target.closest('.bandcard').dataset.i;
  if (S.p.bands.length <= 1) return toast('Cần ít nhất một dòng', true);
  S.p.bands.splice(i, 1); S.p.primary = Math.min(S.p.primary, S.p.bands.length - 1); renderBands(); save();
});
$('#addBand').onclick = () => {
  const last = S.p.bands[S.p.bands.length - 1];
  S.p.bands.push({ name: 'Dòng ' + (S.p.bands.length + 1), y0: Math.min(0.9, last.y1), y1: Math.min(1, last.y1 + 0.06), lang: 'eng' });
  renderBands(); save();
};
$('#detectBtn').onclick = async () => {
  const b = $('#detectBtn'); b.disabled = true; b.firstChild.textContent = '⏳';
  try {
    const { lines } = await api(`/api/p/${S.p.id}/detect`, { method: 'POST' });
    if (!lines.length) toast('Chưa dò thấy vùng sub. Hãy kéo các dải thủ công.', true);
    else {
      const cand = lines.filter(l => l.y0 > 0.7).length ? lines.filter(l => l.y0 > 0.7) : lines;
      S.p.bands = cand.slice(0, 3).map((l, i) => ({ name: 'Dòng ' + (i + 1), y0: l.y0, y1: l.y1, lang: S.p.bands[i]?.lang || 'eng' }));
      S.p.primary = S.p.bands.length - 1;
      renderBands(); save(); toast(`Đã dò thấy ${S.p.bands.length} dòng sub. Hãy kiểm tra ngôn ngữ từng dòng.`);
    }
  } catch (e) { toast(e.message, true); }
  b.disabled = false; b.firstChild.textContent = '✦';
};
$('#ocrBtn').onclick = async () => {
  const b = $('#ocrBtn');
  try {
    await save(true);
    await api(`/api/p/${S.p.id}/ocr`, { json: { bands: S.p.bands, primary: S.p.primary, ocr: S.p.ocr } });
  } catch (e) { return toast(e.message, true); }
  runTask($('#ocrProg'), onOcrDone, b);
};
async function onOcrDone(t) {
  S.p = await api('/api/p/' + S.p.id);
  toast(`Đã quét được ${t.result?.cues ?? S.p.cues.length} câu`);
  S.maxStep = 3; setStep(3);
}

/* ---------------------------------------------------------------- bước 3 */
function enter3() { renderCues(); updateStat(); }
function updateStat() {
  const n = S.p.cues.filter(c => (c.vi || '').trim()).length, el = $('#statDone');
  if (el.textContent !== String(n)) { el.textContent = n; el.classList.remove('bump'); void el.offsetWidth; el.classList.add('bump'); }
  $('#statAll').textContent = S.p.cues.length;
}
function renumber() { S.p.cues.forEach((c, i) => c.id = i + 1); }
function renderCues(flashIdx = -1) {
  const box = $('#cues');
  if (!S.p.cues.length) { box.innerHTML = '<div class="card empty">Chưa có câu nào. Quay lại bước 2 để quét OCR hoặc bấm "Thêm câu".</div>'; return; }
  box.innerHTML = S.p.cues.map((c, i) => `
    <div class="cue ${i === S.cur ? 'sel' : ''} ${i === flashIdx ? 'flash' : ''}" data-i="${i}" style="--i:${i}">
      <div class="n">${c.id}</div>
      <div class="times"><input type="text" data-k="start" value="${fmt(c.start)}"><input type="text" data-k="end" value="${fmt(c.end)}">
        ${c.conf != null ? `<span class="conf ${c.conf < 80 ? 'low' : ''}">tin cậy ${c.conf}%</span>` : ''}</div>
      <div><textarea class="src" data-k="src" placeholder="Văn bản gốc">${esc(c.src)}</textarea>${c.ref ? `<small>Đối chiếu: ${esc(c.ref)}</small>` : ''}</div>
      <div><textarea class="vi" data-k="vi" placeholder="Bản dịch tiếng Việt">${esc(c.vi)}</textarea></div>
      <div class="acts"><button data-act="merge" title="Gộp với câu dưới">⤓</button><button data-act="del" title="Xoá câu">🗑</button></div>
    </div>`).join('');
  if (S.p.cues[S.cur]) showFrame3();
}
const showFrame3 = debounce(() => {
  const c = S.p.cues[S.cur]; if (!c) return;
  $('#cueHint').textContent = `Câu #${c.id} · ${fmt(c.start)} đến ${fmt(c.end)}`;
  loadImg($('#frame3'), $('#skel3'), frameUrl((c.start + c.end) / 2, 640));
}, 150);
$('#cues').addEventListener('input', e => {
  const row = e.target.closest('.cue'); if (!row) return;
  const c = S.p.cues[+row.dataset.i], k = e.target.dataset.k;
  if (k === 'start' || k === 'end') c[k] = parseT(e.target.value); else c[k] = e.target.value;
  if (k === 'vi') updateStat();
  save();
});
$('#cues').addEventListener('change', e => { if (e.target.dataset.k === 'start' || e.target.dataset.k === 'end') e.target.value = fmt(S.p.cues[+e.target.closest('.cue').dataset.i][e.target.dataset.k]); });
$('#cues').addEventListener('click', e => {
  const row = e.target.closest('.cue'); if (!row) return;
  const i = +row.dataset.i, act = e.target.closest('[data-act]')?.dataset.act;
  if (act === 'del') {
    row.classList.add('leave');
    setTimeout(() => { S.p.cues.splice(i, 1); renumber(); S.cur = Math.min(S.cur, S.p.cues.length - 1); renderCues(); updateStat(); save(); }, 280);
    return;
  }
  if (act === 'merge') {
    const a = S.p.cues[i], b = S.p.cues[i + 1]; if (!b) return toast('Đây là câu cuối', true);
    a.end = b.end; a.src = (a.src + ' ' + b.src).trim(); a.vi = (a.vi + ' ' + b.vi).trim(); a.ref = [a.ref, b.ref].filter(Boolean).join(' ');
    S.p.cues.splice(i + 1, 1); renumber(); renderCues(i); updateStat(); save(); return;
  }
  if (S.cur !== i) { $$('.cue.sel').forEach(x => x.classList.remove('sel')); row.classList.add('sel'); S.cur = i; showFrame3(); }
});
$('#addCue').onclick = () => {
  const last = S.p.cues[S.p.cues.length - 1], t = last ? last.end + 0.2 : 0;
  S.p.cues.push({ id: 0, start: t, end: t + 2, src: '', ref: '', vi: '' });
  renumber(); S.cur = S.p.cues.length - 1; renderCues(S.cur); updateStat(); save();
  $('#cues').lastElementChild?.scrollIntoView({ behavior: 'smooth', block: 'center' });
};
$('#expVi').onclick = async e => { e.preventDefault(); await save(true); location.href = U(`/api/p/${S.p.id}/srt/vi`); };
$('#expSrc').onclick = async e => { e.preventDefault(); await save(true); location.href = U(`/api/p/${S.p.id}/srt/src`); };

/* dịch bằng Claude */
$('#trBtn').onclick = () => {
  const t = S.p.translate || {};
  if (!lsGet('vl_key') && lsGet('dx_key')) { lsSet('vl_key', lsGet('dx_key')); lsSet('dx_key', ''); }
  $('#trKey').value = lsGet('vl_key'); $('#trRemember').checked = !!lsGet('vl_key');
  $('#trModel').value = t.model || S.status.default_model; $('#trRules').value = t.rules || S.status.default_rules; $('#trGloss').value = t.glossary || '';
  $('#dlgTr').showModal();
};
$('#dlgTr').addEventListener('close', async () => {
  if ($('#dlgTr').returnValue !== 'go') return;
  const key = $('#trKey').value.trim();
  if (!key) return toast('Chưa nhập API key', true);
  lsSet('vl_key', $('#trRemember').checked ? key : '');
  S.p.translate = { model: $('#trModel').value.trim(), rules: $('#trRules').value, glossary: $('#trGloss').value };
  try {
    await api(`/api/p/${S.p.id}/translate`, { json: { api_key: key, ...S.p.translate, cues: S.p.cues, only_empty: $('#trOnlyEmpty').checked } });
  } catch (e) { return toast(e.message, true); }
  runTask($('#trProg'), onTrDone, $('#trBtn'));
});
async function onTrDone(t) {
  S.p = await api('/api/p/' + S.p.id); renderCues(); updateStat();
  toast(t.result?.missing ? `Xong, còn ${t.result.missing} câu chưa dịch được` : 'Đã dịch xong toàn bộ');
}
/* nhập SRT */
$('#impBtn').onclick = () => { $('#impText').value = ''; $('#impFile').value = ''; $('#dlgImp').showModal(); };
$('#impFile').onchange = async e => { const f = e.target.files[0]; if (f) $('#impText').value = await f.text(); };
$('#dlgImp').addEventListener('close', async () => {
  if ($('#dlgImp').returnValue !== 'go') return;
  try {
    const r = await api(`/api/p/${S.p.id}/import_srt`, { json: { text: $('#impText').value, cues: S.p.cues } });
    S.p.cues = r.cues; renderCues(); updateStat(); toast(`Đã nhập ${r.matched} câu (khớp ${r.how})`);
  } catch (e) { toast(e.message, true); }
});

/* ---------------------------------------------------------------- bước 4 */
const SCHEMA = [
  ['Xoá sub cũ', true, [
    ['old.y0', 'range', 'Sub cũ bắt đầu từ (% chiều cao)', { min: 50, max: 98, step: .2, mul: 100 }],
    ['old.fade', 'range', 'Độ mờ dần phía trên (%)', { min: 0, max: 15, step: .2, mul: 100 }],
    ['old.blur', 'range', 'Độ nhoè', { min: .3, max: 3, step: .1 }],
  ]],
  ['Logo', false, [
    ['logo.enabled', 'toggle', 'Hiện logo'],
    ['logo.file', 'logo', 'Ảnh logo'],
    ['logo.pos', 'select', 'Vị trí', { opts: { tl: 'Trên trái', tr: 'Trên phải', bl: 'Dưới trái', br: 'Dưới phải' } }],
    ['logo.width', 'range', 'Kích thước (% chiều rộng)', { min: 3, max: 25, step: .2, mul: 100 }],
    ['logo.opacity', 'range', 'Độ đậm (%)', { min: 10, max: 100, step: 1, mul: 100 }],
  ]],
  ['Chữ watermark ở góc', false, [
    ['corner.enabled', 'toggle', 'Hiện chữ ở góc'],
    ['corner.text', 'text', 'Nội dung'],
    ['corner.pos', 'select', 'Vị trí', { opts: { tl: 'Trên trái', tr: 'Trên phải', bl: 'Dưới trái', br: 'Dưới phải' } }],
    ['corner.size', 'range', 'Cỡ chữ (% chiều cao)', { min: 1.5, max: 12, step: .1, mul: 100 }],
    ['corner.opacity', 'range', 'Độ đậm (%)', { min: 5, max: 100, step: 1, mul: 100 }],
    ['corner.serif', 'toggle', 'Font có chân'],
  ]],
  ['Chữ watermark bay vòng', false, [
    ['fly.enabled', 'toggle', 'Hiện chữ bay'],
    ['fly.text', 'text', 'Nội dung'],
    ['fly.size', 'range', 'Cỡ chữ (% chiều cao)', { min: 1.5, max: 12, step: .1, mul: 100 }],
    ['fly.opacity', 'range', 'Độ đậm (%)', { min: 5, max: 100, step: 1, mul: 100 }],
    ['fly.speed', 'range', 'Tốc độ bay', { min: .3, max: 3, step: .1 }],
    ['fly.serif', 'toggle', 'Font có chân'],
  ]],
  ['Phụ đề tiếng Việt', false, [
    ['sub.font', 'select', 'Font', { fonts: true }],
    ['sub.size', 'range', 'Cỡ chữ (% chiều cao)', { min: 2.5, max: 9, step: .1, mul: 100 }],
    ['sub.outline', 'range', 'Độ dày viền', { min: 0, max: 8, step: .2 }],
    ['sub.margin_v', 'range', 'Cách đáy (% chiều cao)', { min: 1, max: 20, step: .2, mul: 100 }],
    ['sub.color', 'color', 'Màu chữ'],
    ['sub.outline_color', 'color', 'Màu viền'],
  ]],
  ['Chất lượng xuất', false, [
    ['export.quality', 'select', 'Chất lượng', { opts: { fast: 'Nhanh (file nhẹ hơn)', balanced: 'Cân bằng', best: 'Đẹp nhất (chậm)' } }],
    ['export.resolution', 'select', 'Độ phân giải', { opts: { source: 'Giữ nguyên', '1080': 'Ép về 1080p', '720': 'Ép về 720p' } }],
  ]],
];
function cfg() {
  const d = JSON.parse(JSON.stringify(S.status.defaults));
  for (const [k, v] of Object.entries(S.p.settings || {})) Object.assign(d[k] ||= {}, v);
  return d;
}
function buildSettings() {
  const c = cfg();
  $('#settingsCard').innerHTML = '<h2>Hiệu ứng và phụ đề</h2>' + SCHEMA.map(([title, open, fields], gi) => `
    <details class="grp" ${open ? 'open' : ''}><summary>${title}</summary><div class="grpbody">${fields.map(([path, type, label, o = {}]) => {
    const v = deepGet(c, path);
    if (type === 'range') { const mul = o.mul || 1; return `<div class="fld"><div class="lab"><span>${label}</span><output>${(v * mul).toFixed(1)}</output></div><input type="range" data-path="${path}" data-mul="${mul}" min="${o.min}" max="${o.max}" step="${o.step}" value="${v * mul}"></div>`; }
    if (type === 'toggle') return `<div class="fld inline"><span class="lab">${label}</span><label class="toggle"><input type="checkbox" data-path="${path}" ${v ? 'checked' : ''}><span></span></label></div>`;
    if (type === 'text') return `<label>${label}<input type="text" data-path="${path}" value="${esc(v)}"></label>`;
    if (type === 'color') return `<div class="fld inline"><span class="lab">${label}</span><input type="color" data-path="${path}" value="${v}"></div>`;
    if (type === 'select') { const opts = o.fonts ? Object.fromEntries(S.status.fonts.map(f => [f, f])) : o.opts; return `<label>${label}<select data-path="${path}">${Object.entries(opts).map(([k, n]) => `<option value="${k}" ${String(v) === k ? 'selected' : ''}>${n}</option>`).join('')}</select></label>`; }
    if (type === 'logo') return `<label>${label}<input type="file" id="logoFile" accept="image/*"></label><div class="hint">Đang dùng: <b id="logoName">${esc(v || 'không có')}</b></div>`;
    return '';
  }).join('')}</div></details>`).join('');
}
function enter4() {
  buildSettings();
  const h = $('#exportHint');
  if (S.status.export_dir) { h.hidden = false; h.textContent = 'Video xuất xong cũng được chép tự động vào: ' + S.status.export_dir; } else h.hidden = true;
  const sel = $('#pvCue');
  sel.innerHTML = S.p.cues.map((c, i) => `<option value="${i}">Câu #${c.id} · ${fmt(c.start)}</option>`).join('') || '<option value="-1">Không có câu</option>';
  sel.value = Math.min(S.cur, S.p.cues.length - 1);
  preview();
  api(`/api/p/${S.p.id}/check`).then(r => { showResult(r.checks, false); }).catch(() => { });
}
$('#pvCue').onchange = e => { S.cur = +e.target.value; preview(); };
$('#settingsCard').addEventListener('input', e => {
  const el = e.target, path = el.dataset.path; if (!path) return;
  let v;
  if (el.type === 'checkbox') v = el.checked;
  else if (el.type === 'range') { const mul = +el.dataset.mul; v = +el.value / mul; el.parentElement.querySelector('output').textContent = (+el.value).toFixed(1); }
  else v = el.value;
  S.p.settings ||= {}; deepSet(S.p.settings, path, v);
  save(); previewSoon();
});
$('#settingsCard').addEventListener('change', async e => {
  if (e.target.id !== 'logoFile' || !e.target.files[0]) return;
  const fd = new FormData(); fd.append('file', e.target.files[0]);
  try {
    const r = await api(`/api/p/${S.p.id}/asset`, { method: 'POST', body: fd });
    S.p.settings ||= {}; deepSet(S.p.settings, 'logo.file', r.file); $('#logoName').textContent = r.file; save(); preview(); toast('Đã đổi logo');
  } catch (err) { toast(err.message, true); }
});
const previewSoon = debounce(() => preview(), 700);
async function preview() {
  if (!S.p.cues.length) return;
  const c = S.p.cues[Math.max(0, +$('#pvCue').value)] || S.p.cues[0];
  const t = (c.start + c.end) / 2, n = ++S.pvN;
  $('#pvBusy').classList.add('show');
  try {
    const r = await api(`/api/p/${S.p.id}/preview`, { json: { t, settings: S.p.settings, cues: S.p.cues } });
    const blob = await r.blob(); if (n !== S.pvN) return;
    const img = $('#frame4'); if (img.src.startsWith('blob:')) URL.revokeObjectURL(img.src); img.src = URL.createObjectURL(blob); img.classList.add('ready'); $('#skel4').classList.add('off');
  } catch (e) { toast(e.message, true); }
  if (n === S.pvN) $('#pvBusy').classList.remove('show');
}
$('#renderBtn').onclick = async () => {
  const b = $('#renderBtn');
  if (!S.p.cues.some(c => (c.vi || '').trim())) return toast('Chưa có bản dịch. Hãy dịch hoặc nhập SRT ở bước 3.', true);
  try { await api(`/api/p/${S.p.id}/render`, { json: { cues: S.p.cues, settings: S.p.settings } }); }
  catch (e) { return toast(e.message, true); }
  $('#resultCard').hidden = true;
  runTask($('#rdProg'), onRenderDone, b);
};
async function onRenderDone(t) {
  toast('Xuất video xong!'); burst();
  const r = await api(`/api/p/${S.p.id}/check`).catch(() => ({ checks: [] }));
  showResult(r.checks, true);
  const h = $('#exportHint');
  if (t?.result?.export) { h.hidden = false; h.textContent = 'Đã lưu bản xuất vào: ' + t.result.export + '. Với video lớn, tải từ app Google Drive nhanh và ổn định hơn tải qua trang web.'; }
}
function showResult(checks, scroll) {
  const id = S.p.id, card = $('#resultCard');
  const v = $('#player'); v.src = U(`/api/p/${id}/final?_=${Date.now()}`);
  $('#dlMp4').href = U(`/api/p/${id}/final?download=1`);
  $('#dlVi').href = U(`/api/p/${id}/srt/vi`); $('#dlSrc').href = U(`/api/p/${id}/srt/src`);
  $('#checks').innerHTML = checks.map((c, i) => `<li class="${c.ok ? '' : 'bad'}" style="--i:${i}"><i>${c.ok ? '✓' : '!'}</i><span>${esc(c.name)}</span><small>${esc(c.value)}</small></li>`).join('');
  card.hidden = false;
  if (scroll) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

/* ---------------------------------------------------------------- pháo giấy */
function burst() {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const cv = $('#confetti'), ctx = cv.getContext('2d');
  cv.width = innerWidth; cv.height = innerHeight;
  const cols = ['#4ade80', '#22d3ee', '#fbbf24', '#f472b6', '#a3e635', '#fff'];
  const ps = Array.from({ length: 160 }, () => ({ x: innerWidth / 2 + (Math.random() - .5) * 200, y: innerHeight * .55, vx: (Math.random() - .5) * 16, vy: -Math.random() * 18 - 6, s: 5 + Math.random() * 7, r: Math.random() * 6, vr: (Math.random() - .5) * .4, c: cols[Math.random() * cols.length | 0] }));
  let f = 0;
  (function loop() {
    ctx.clearRect(0, 0, cv.width, cv.height);
    ps.forEach(p => { p.vy += .45; p.x += p.vx; p.y += p.vy; p.r += p.vr; ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.r); ctx.fillStyle = p.c; ctx.globalAlpha = Math.max(0, 1 - f / 110); ctx.fillRect(-p.s / 2, -p.s / 3, p.s, p.s / 1.6); ctx.restore(); });
    if (++f < 120) requestAnimationFrame(loop); else ctx.clearRect(0, 0, cv.width, cv.height);
  })();
}



/* ---------------------------------------------------------------- hộp thoại kết nối */
function connTip() {
  if (LOCAL) return 'Bạn đang mở giao diện ngay trên máy xử lý nên không cần nhập địa chỉ.';
  return `Chạy app trên máy tính (start.bat / start.sh) rồi để địa chỉ ${DEFAULT_SERVER}. Dùng trên điện thoại: chạy cloudflared tunnel trên máy tính rồi dán địa chỉ https://...trycloudflare.com và mã truy cập (README mục 9).`;
}
function showConnect(why) {
  const d = $('#dlgConn');
  $('#connUrl').value = LOCAL ? location.origin : (CFG.base || DEFAULT_SERVER);
  $('#connUrl').disabled = LOCAL;
  $('#connToken').value = CFG.token;
  const f = $('#connFail');
  f.hidden = !why; f.textContent = why || ''; f.classList.add('bad'); f.classList.remove('good');
  $('#connTip').textContent = connTip();
  if (!d.open) d.showModal();
}
$('#srvBtn').onclick = () => showConnect('');
$('#connTest').onclick = async () => {
  const b = $('#connTest'), f = $('#connFail');
  b.disabled = true; b.textContent = 'Đang thử...';
  const r = await probeServer(LOCAL ? '' : normBase($('#connUrl').value), $('#connToken').value.trim());
  b.disabled = false; b.textContent = 'Thử kết nối';
  f.hidden = false;
  f.classList.toggle('bad', !r.ok); f.classList.toggle('good', r.ok);
  f.textContent = r.ok ? `Kết nối được. ffmpeg ${r.status.ffmpeg ? 'có' : 'CHƯA có'}, Tesseract ${r.status.tesseract ? 'có' : 'CHƯA có'}.` : r.why;
};
$('#connGo').onclick = () => {
  if (!LOCAL) lsSet('vl_server', normBase($('#connUrl').value) || DEFAULT_SERVER);
  lsSet('vl_token', $('#connToken').value.trim());
  location.reload();
};

init();
