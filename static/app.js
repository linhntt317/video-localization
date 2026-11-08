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
  const go = () => api(`/api/p/${S.p.id}/save`, { json: { name: S.p.name, mode: S.p.mode, bands: S.p.bands, primary: S.p.primary, ocr: S.p.ocr, cues: S.p.cues, settings: S.p.settings, translate: S.p.translate, dub: S.p.dub } }).catch(e => toast('Lưu lỗi: ' + e.message, true));
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
    b.hidden = isBrand() && (n === 2 || n === 3);  // chế độ chỉ logo/watermark: ẩn bước quét và phụ đề
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
$('#stepper').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b || b.disabled) return;
  if (TL.on) { TL.on = false; $('#tools').hidden = true; $('#toolsBtn').textContent = 'Công cụ'; setStep(+b.dataset.step, true); } else setStep(+b.dataset.step);
});
window.addEventListener('resize', updateStepper);

/* ---------------------------------------------------------------- tác vụ nền */
function runTask(root, onDone, btn, base) {
  base = base || `/api/p/${S.p.id}`;
  const bar = $('.bar i', root), pm = $('.pm', root), pp = $('.pp', root);
  root.hidden = false;
  btn?.classList.add('busy'); btn && (btn.disabled = true);
  $('[data-cancel]', root).onclick = () => api(`${base}/task/cancel`, { method: 'POST' });
  clearInterval(root._poll);
  const tick = async () => {
    let t;
    try { t = await api(`${base}/task`); } catch (e) { return; }
    bar.style.width = (t.progress * 100).toFixed(1) + '%';
    pm.textContent = t.message || '...';
    pp.textContent = Math.round(t.progress * 100) + '%';
    if (t.state !== 'running') {
      clearInterval(root._poll);
      btn?.classList.remove('busy'); btn && (btn.disabled = false);
      setTimeout(() => { root.hidden = true; }, t.state === 'done' ? 900 : 0);
      if (t.state === 'error') toast(t.error || 'Có lỗi xảy ra', true);
      else if (t.state === 'cancelled') toast('Đã huỷ');
      else onDone && onDone(t);
    }
  };
  root._poll = setInterval(tick, 700); tick();
}

/* ---------------------------------------------------------------- khởi động */
async function init() {
  const pr = await probeServer(CFG.base, CFG.token);
  if (!pr.ok) { showConnect(pr.why); return; }
  S.status = pr.status;
  $('#srvBtn').textContent = LOCAL ? 'Máy xử lý: trang này' : 'Máy xử lý: ' + CFG.base.replace(/^https?:\/\//, '');
  const st = S.status;
  $('#chips').innerHTML = [['ffmpeg', st.ffmpeg], ['libass', st.libass], ['Tesseract', st.tesseract], ...(st.rapidocr ? [['RapidOCR', true]] : [])].map(([n, ok]) => `<span class="chip ${ok ? '' : 'bad'}" title="${ok ? 'Đã sẵn sàng' : 'Chưa cài, xem README'}">${n}</span>`).join('');
  if (!st.ffmpeg) toast('Chưa tìm thấy ffmpeg. Hãy cài ffmpeg rồi mở lại (xem README).', true);
  if (st.ffmpeg && !st.libass) toast('ffmpeg của bạn thiếu libass nên không chèn được phụ đề. Hãy dùng bản ffmpeg đầy đủ (xem README).', true);
  if (!st.tesseract && !st.rapidocr) toast('Chưa có bộ đọc chữ (Tesseract hoặc RapidOCR). Cần để quét sub (xem README).', true);
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
  try { S.p = await api('/api/projects', { json: { media: c.dataset.media } }); await created(); }
  catch (err) { toast(err.message, true); } finally { c.classList.remove('busy'); }
});
/* sau khi tạo dự án: nếu tick "Chỉ gắn logo/watermark" thì bỏ qua bước quét/dịch, vào thẳng bước 4 */
async function created() {
  if ($('#brandOnly').checked) {
    S.p.mode = 'brand';
    S.p.settings = { old: { enabled: false }, sub: { enabled: false } };
    try { await save(true); } catch (e) { }
    S.maxStep = 4; toast('Đã tạo dự án (chỉ gắn logo/watermark)'); setStep(4);
  } else { S.maxStep = 2; toast('Đã tạo dự án'); setStep(2); }
}
const isBrand = () => S.p?.mode === 'brand';
async function loadProjects() {
  loadMedia();
  const list = await api('/api/projects').catch(() => []);
  const box = $('#projects');
  if (!list.length) { box.innerHTML = '<div class="empty">Chưa có dự án nào.<br>Hãy chọn video ở bên cạnh để bắt đầu.</div>'; return; }
  box.innerHTML = list.map((p, i) => `
    <div class="proj" data-id="${p.id}" style="--i:${i}">
      <b>${esc(p.name)}${p.mode === 'brand' ? '<span class="badge">logo/watermark</span>' : ''}${p.has_final ? '<span class="badge">đã xuất</span>' : ''}</b>
      <button class="del" title="Xoá dự án" data-del="${p.id}">×</button>
      <small>${p.info.width}x${p.info.height} · ${dur(p.info.duration)}${p.mode === 'brand' ? '' : ` · ${p.cues} câu · ${p.translated} đã dịch`}</small>
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
  if (S.p._video_error) toast(S.p._video_error, true);
  S.maxStep = isBrand() ? 4 : S.p.cues.length ? 3 : 2;
  S.cur = 0;
  /* nếu đang có tác vụ chạy nền (vd. điện thoại khoá màn hình lúc đang quét/xuất) thì nối lại thanh tiến độ */
  const t = await api(`/api/p/${id}/task`).catch(() => null);
  const R = { ocr: [2, '#ocrProg', onOcrDone, '#ocrBtn'], translate: [3, '#trProg', onTrDone, '#trBtn'], render: [4, '#rdProg', onRenderDone, '#renderBtn'], rescan: [3, '#rsProg', onRescanDone, '#rsBtn'], asr: [3, '#asrProg', onAsrDone, '#asrBtn'], tts: [3, '#dubProg', onDubDone, '#dubAll'], shorten: [3, '#dubProg', onDubDone, '#dubShort'] }[t?.kind];
  if (t?.state === 'running' && R) {
    S.maxStep = 4; setStep(R[0]);
    setTimeout(() => runTask($(R[1]), R[2], $(R[3])), 300);
    return;
  }
  setStep(isBrand() ? 4 : S.p.cues.length ? 3 : 2);
}
/* tải video về máy xử lý từ link: hợp với video lớn vì không phải đi qua trình duyệt điện thoại */
let urlPoll = null;
$('#urlBtn').onclick = async () => {
  const url = $('#urlIn').value.trim(); if (!url) return toast('Hãy dán link video', true);
  try { await api('/api/import_url', { json: { url } }); } catch (e) { return toast(e.message, true); }
  const root = $('#urlProg'), bar = $('.bar i', root), pm = $('.pm', root), pp = $('.pp', root);
  root.hidden = false; $('#urlBtn').disabled = true;
  $('#urlCancel').onclick = () => api('/api/import_url/cancel', { method: 'POST' });
  clearInterval(urlPoll);
  const tick = async () => {
    let t; try { t = await api('/api/import_url'); } catch (e) { return; }
    const f = t.total ? t.done / t.total : 0;
    bar.style.width = (f * 100).toFixed(1) + '%';
    pm.textContent = t.message || '...';
    pp.textContent = t.total ? `${mb(t.done)} / ${mb(t.total)}` : (t.done ? mb(t.done) : '');
    if (t.state === 'running') return;
    clearInterval(urlPoll); $('#urlBtn').disabled = false; root.hidden = true;
    if (t.state === 'error') return toast(t.error || 'Tải lỗi', true);
    if (t.state === 'done' && t.pid) { S.p = await api('/api/p/' + t.pid); $('#urlIn').value = ''; await created(); }
  };
  urlPoll = setInterval(tick, 1000); tick();
};
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
    S.p = j; created();
  };
  xhr.onerror = () => { $('#upbar').hidden = true; toast('Tải lên lỗi', true); };
  xhr.open('POST', U('/api/projects')); xhr.send(fd);
}
$('#pathBtn').onclick = async () => {
  const path = $('#pathIn').value.trim(); if (!path) return toast('Hãy dán đường dẫn video', true);
  const b = $('#pathBtn'); b.disabled = true;
  try { S.p = await api('/api/projects', { json: { path } }); await created(); }
  catch (e) { toast(e.message, true); } finally { b.disabled = false; }
};

/* ---------------------------------------------------------------- bước 2 */
function frameUrl(t, w = 1280) { return U(`/api/p/${S.p.id}/frame?t=${t.toFixed(2)}&w=${w}&_=${Date.now() % 100000}`); }
function loadImg(img, skel, url, tries = 0) {
  const view = img.parentElement, n = img._n = (img._n || 0) + 1;
  $('.vmsg', view)?.remove();
  skel?.classList.remove('off'); img.classList.remove('ready');
  const tmp = new Image();
  tmp.onload = () => { if (img._n !== n) return; img.src = tmp.src; img.classList.add('ready'); skel?.classList.add('off'); };
  tmp.onerror = async () => {
    if (img._n !== n) return;
    skel?.classList.add('off');
    let msg = 'Không tải được khung hình (mất kết nối với máy xử lý?).', wait = false;
    try { const j = await (await fetch(url)).json(); if (j?.error) { msg = j.error; wait = /đang được chép|Đã bắt đầu chép/.test(msg); } } catch (e) { }
    if (img._n !== n) return;
    const d = document.createElement('div'); d.className = 'vmsg';
    d.innerHTML = `<span>${esc(msg)}</span><button class="btn mini" type="button">Thử lại</button>`;
    $('button', d).onclick = () => loadImg(img, skel, url, 0);
    view.appendChild(d);
    if (wait && tries < 120) setTimeout(() => { if (img._n === n) loadImg(img, skel, url, tries + 1); }, 5000);
  };
  tmp.src = url;
}
function enter2() {
  const p = S.p, o = p.ocr;
  const sl = $('#tSlider'); sl.max = p.info.duration.toFixed(1);
  if (!S.t2) S.t2 = Math.min(p.info.duration * 0.3, p.info.duration);
  sl.value = S.t2; $('#tOut').textContent = fmt(S.t2);
  $('#o_fps').value = o.fps; $('#o_t0').value = o.t0 || ''; $('#o_t1').value = o.t1 || ''; $('#o_thr').value = o.thr; $('#o_seg').value = o.seg_min ?? 55; $('#o_ref').checked = o.ref !== false;
  $('#o_engine').value = o.engine || 'tesseract'; $('#o_refine').checked = o.refine !== false; initOcrAsr();
  renderBands(); showFrame2();
}
const showFrame2 = debounce(() => loadImg($('#frame'), $('#skel'), frameUrl(S.t2)), 120);
$('#tSlider').addEventListener('input', e => { S.t2 = +e.target.value; $('#tOut').textContent = fmt(S.t2); showFrame2(); });
$$('[data-seek]').forEach(b => b.onclick = () => { S.t2 = Math.max(0, Math.min(S.p.info.duration, S.t2 + +b.dataset.seek)); $('#tSlider').value = S.t2; $('#tOut').textContent = fmt(S.t2); showFrame2(); });
function initOcrAsr() {
  const st = S.status.asr || {}, w = S.p.ocr.asr || {}, sel = $('#o_asr_model');
  if (!sel.options.length) sel.innerHTML = (st.models || []).map(m => `<option value="${m}">${m}${m === st.default_model ? ' (đề nghị)' : ''}</option>`).join('');
  sel.value = w.model || st.default_model || 'small';
  $('#o_asr_lang').value = w.lang || '';
  $('#o_asr').checked = !!w.on && !!st.available; $('#o_asr').disabled = !st.available;
  $('#o_asr_opts').hidden = !$('#o_asr').checked;
  $('#o_asr_hint').textContent = !st.available ? 'Máy xử lý chưa cài Whisper (Colab: chạy lại ô cài đặt mới nhất; máy tính: pip install -r requirements-asr.txt).'
    : `Whisper nghe video; chỗ nào có người nói mà chưa có câu thì OCR quét lại THẬT KỸ đúng chỗ đó (nhiều mức nhạy, tốc độ khung cao, thử cả hai dòng) và chỉ thêm câu khi OCR thấy chữ thật. Đoạn OCR vẫn không thấy chữ sẽ để ở hàng đỏ ở bước 3 cho bạn quyết. Máy này dùng ${st.device === 'cuda' ? 'GPU (nhanh)' : 'CPU (chậm, nên chọn tiny/base/small)'}; lần đầu phải tải mô hình.`;
}
function saveOcrAsr() {
  S.p.ocr.asr = { on: $('#o_asr').checked, model: $('#o_asr_model').value, lang: $('#o_asr_lang').value };
  $('#o_asr_opts').hidden = !$('#o_asr').checked; save();
}
['o_asr', 'o_asr_model', 'o_asr_lang'].forEach(id => $('#' + id).addEventListener('change', saveOcrAsr));
$('#o_ref').addEventListener('change', e => { S.p.ocr.ref = e.target.checked; save(); });
$('#o_refine').addEventListener('change', e => { S.p.ocr.refine = e.target.checked; save(); });
$('#o_engine').addEventListener('change', e => { S.p.ocr.engine = e.target.value; save(); });
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
      S.p.bands = cand.slice(0, 3).map((l, i) => ({ name: 'Dòng ' + (i + 1), y0: l.y0, y1: l.y1, lang: l.lang || S.p.bands[i]?.lang || 'eng' }));
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
  const r = t.result || {};
  toast(`Đã quét được ${r.cues ?? S.p.cues.length} câu`);
  if (r.segments != null) {
    toast(`Whisper nghe được ${r.segments} đoạn. OCR quét kỹ xác nhận thêm ${r.rescanned || 0} câu; ${r.missing ? `còn ${r.missing} đoạn Whisper nghe có tiếng nhưng OCR không thấy chữ (hàng đỏ ở bước 3, bạn xem rồi quyết)` : 'không còn đoạn nào bị sót'}.`, !!r.missing);
  }
  if (r.asr_error) toast('Đối chiếu Whisper không chạy được: ' + r.asr_error + ' (kết quả OCR vẫn được giữ).', true);
  S.maxStep = 3; setStep(3);
}

/* ---------------------------------------------------------------- bước 3 */
function enter3() { S.dubOn = S.dubOn ?? S.p.cues.some(c => c.dub); $('#dubPanel').hidden = !S.dubOn; renderCues(); updateStat(); initFinder(); if (S.dubOn) initDub(); }
function updateStat() {
  const n = S.p.cues.filter(c => (c.vi || '').trim()).length, el = $('#statDone');
  if (el.textContent !== String(n)) { el.textContent = n; el.classList.remove('bump'); void el.offsetWidth; el.classList.add('bump'); }
  $('#statAll').textContent = S.p.cues.length;
}
function renumber() { S.p.cues.forEach((c, i) => c.id = i + 1); }
function renderCues(flashIdx = -1) {
  const box = $('#cues');
  drawTimeline();
  if (!S.p.cues.length) { box.innerHTML = '<div class="card empty">Chưa có câu nào. Quay lại bước 2 để quét OCR hoặc bấm "Thêm câu".</div>'; return; }
  box.innerHTML = S.p.cues.map((c, i) => `
    <div class="cue ${i === S.cur ? 'sel' : ''} ${i === flashIdx ? 'flash' : ''}" data-i="${i}" style="--i:${i}">
      <div class="n">${c.id}</div>
      <div class="times"><input type="text" data-k="start" value="${fmt(c.start)}"><input type="text" data-k="end" value="${fmt(c.end)}">
        ${c.conf != null ? `<span class="conf ${c.conf < 80 ? 'low' : ''}">tin cậy ${c.conf}%</span>` : ''}${c.asr ? '<span class="conf asr">từ Whisper</span>' : ''}${c.deep ? `<span class="conf asr">quét kỹ${c.from_band ? ' · ' + esc(c.from_band) : ''}</span>` : ''}</div>
      <div><textarea class="src" data-k="src" placeholder="Văn bản gốc">${esc(c.src)}</textarea>${c.ref ? `<small>Đối chiếu: ${esc(c.ref)}</small>` : ''}</div>
      <div><textarea class="vi" data-k="vi" placeholder="Bản dịch tiếng Việt">${esc(c.vi)}</textarea></div>
      <div class="acts"><button data-act="merge" title="Gộp với câu dưới">⤓</button><button data-act="del" title="Xoá câu">🗑</button></div>
      ${S.dubOn ? `<div class="dubrow">${dubRowInner(c, i)}</div>` : ''}
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
  if (k === 'start' || k === 'end') drawTimeline();
  if (S.dubOn && (k === 'vi' || k === 'start' || k === 'end')) refreshDubRow(+row.dataset.i);
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
$('#expBtn').onclick = e => { e.stopPropagation(); $('#expBtn').parentElement.classList.toggle('open'); };
document.addEventListener('click', e => { if (!e.target.closest('.menu')) $$('.menu.open').forEach(m => m.classList.remove('open')); });
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

/* ---- tìm và bổ sung câu bị sót ---- */
const F = { t: 0, a: null, b: null };
const showFrame5 = debounce(() => loadImg($('#frame5'), $('#skel5'), frameUrl(F.t, 960)), 150);
function initFinder() {
  const d = S.p.info.duration, sl = $('#fSlider');
  sl.max = d.toFixed(1);
  if (F.pid !== S.p.id) { F.pid = S.p.id; F.t = Math.min(d * 0.3, d); F.a = F.b = null; }
  sl.value = F.t; updateFinder(); showFrame5();
}
function updateFinder() {
  $('#fOut').textContent = fmt(F.t);
  $('#tlHead').style.left = (F.t / S.p.info.duration * 100) + '%';
  const r = $('#tlRange'), d = S.p.info.duration, ok = F.a != null && F.b != null && F.b > F.a;
  r.style.display = F.a != null || F.b != null ? 'block' : 'none';
  const a = F.a ?? F.b, b = F.b ?? F.a;
  r.style.left = (Math.min(a, b ?? a) / d * 100) + '%'; r.style.width = Math.max(.4, Math.abs((b ?? a) - a) / d * 100) + '%';
  $('#fRange').textContent = ok ? `Sẽ quét lại đoạn ${fmt(F.a)} đến ${fmt(F.b)}.`
    : (F.a != null || F.b != null) ? 'Hãy đặt cả điểm đầu và điểm cuối (điểm cuối phải sau điểm đầu).'
      : 'Chưa chọn đoạn: sẽ quét quanh điểm đang xem (từ −1,5s đến +2s).';
}
function drawTimeline() {
  if (!S.p || !$('#tlSegs')) return;
  const d = S.p.info.duration;
  $('#tlSegs').innerHTML = S.p.cues.map(c => `<i style="left:${c.start / d * 100}%;width:${Math.max(.2, (c.end - c.start) / d * 100)}%"></i>`).join('');
  const cs = S.p.cues.slice().sort((x, y) => x.start - y.start), gaps = [];
  let prev = 0;
  for (const c of cs) { if (c.start - prev >= 3) gaps.push([prev, c.start]); prev = Math.max(prev, c.end); }
  if (d - prev >= 3) gaps.push([prev, d]);
  const g = $('#fGaps');
  g.innerHTML = gaps.length ? gaps.slice(0, 40).map((x, i) => `<button class="btn mini" type="button" data-gap="${i}">${fmt(x[0])} – ${fmt(x[1])}</button>`).join('') + (gaps.length > 40 ? `<span class="hint">và ${gaps.length - 40} khoảng nữa</span>` : '')
    : '<span class="hint">Không có khoảng trống dài.</span>';
  g._gaps = gaps;
  renderAsr();
}
$('#fGaps').addEventListener('click', e => {
  const b = e.target.closest('[data-gap]'); if (!b) return;
  const [a, z] = $('#fGaps')._gaps[+b.dataset.gap];
  F.a = a; F.b = z; setF((a + z) / 2);
});
function setF(t) { F.t = Math.max(0, Math.min(S.p.info.duration, t)); $('#fSlider').value = F.t; updateFinder(); showFrame5(); }
$('#fSlider').addEventListener('input', e => setF(+e.target.value));
$$('[data-fseek]').forEach(b => b.onclick = () => setF(F.t + +b.dataset.fseek));
$('#tlBar').addEventListener('click', e => { const r = e.currentTarget.getBoundingClientRect(); setF((e.clientX - r.left) / r.width * S.p.info.duration); });
$('#fSetA').onclick = () => { F.a = F.t; if (F.b != null && F.b <= F.a) F.b = null; updateFinder(); };
$('#fSetB').onclick = () => { F.b = F.t; if (F.a != null && F.a >= F.b) F.a = null; updateFinder(); };
$('#fAdd').onclick = () => {
  const cs = S.p.cues, t = +F.t.toFixed(2);
  let i = cs.findIndex(c => c.start > t); if (i < 0) i = cs.length;
  const next = cs[i], end = Math.min(t + 2, next ? Math.max(t + .3, next.start) : S.p.info.duration);
  cs.splice(i, 0, { id: 0, start: t, end: +end.toFixed(2), src: '', ref: '', vi: '' });
  renumber(); S.cur = i; renderCues(i); updateStat(); save();
  const row = $('#cues').children[i]; row?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  setTimeout(() => $('textarea.src', row)?.focus(), 350);
  toast('Đã thêm câu trống, hãy gõ nội dung.');
};
$('#rsBtn').onclick = async () => {
  const ok = F.a != null && F.b != null && F.b > F.a;
  const t0 = ok ? F.a : Math.max(0, F.t - 1.5), t1 = ok ? F.b : F.t + 2;
  try {
    await save(true);
    await api(`/api/p/${S.p.id}/rescan`, { json: { t0, t1, min_conf: +$('#rs_conf').value, seg_min: +$('#rs_seg').value, thr: +$('#rs_thr').value } });
  } catch (e) { return toast(e.message, true); }
  runTask($('#rsProg'), onRescanDone, $('#rsBtn'));
};
async function onRescanDone(t) {
  S.p = await api('/api/p/' + S.p.id); renderCues(); updateStat();
  const r = t.result || {};
  toast(r.added ? `Đã thêm ${r.added} câu mới (tìm thấy ${r.found}, ${r.found - r.added} câu trùng bỏ qua)` : `Không tìm thấy câu mới${r.found ? ' (các câu tìm thấy đã có sẵn)' : ''}. Thử hạ "Độ tin cậy" hoặc thêm câu bằng tay.`, !r.added);
}


/* ---- chẩn đoán một khung hình ---- */
const WHY = {
  no_text: (r, pr) => ['bad', 'App KHÔNG thấy chữ trong dải này. Dải có thể đang lệch khỏi dòng chữ (xem ảnh "Dải quét": chữ phải nằm trọn bên trong), hoặc chữ quá mờ/không sáng hơn nền. Thử chỉnh dải ở bước 2 hoặc hạ "Độ nhạy chữ" (số thấp = nhạy hơn).'],
  unreadable: () => ['bad', 'Thấy có nét nhưng Tesseract không đọc ra chữ. Thường do ngôn ngữ của dòng chọn sai (xem ô Ngôn ngữ ở bước 2), dải cắt sát chữ, hoặc chữ quá mờ.'],
  low_conf: (r, pr) => ['bad', `Đọc ra chữ nhưng độ tin cậy ${r.conf} thấp hơn ngưỡng ${pr.min_conf}, nên bị loại. Hạ "Độ tin cậy" khi quét lại đoạn này, hoặc dùng nút bên dưới để tạo câu từ chữ này rồi sửa tay.`],
  ok: (r, pr) => ['good', `Đọc được (tin cậy ${r.conf}). Nếu câu này vẫn không có trong danh sách thì có thể là ${r.primary ? 'câu quá ngắn/hiện chớp (dưới 0,25 giây) hoặc độ tin cậy trung bình cả câu dưới ' + pr.seg_min : 'đây là dòng đối chiếu, không phải dòng chính dùng để chia câu'}.`],
};
$('#fProbe').onclick = async () => {
  const b = $('#fProbe'), out = $('#probeOut');
  b.classList.add('busy'); b.disabled = true; out.innerHTML = '';
  try {
    await save(true);
    const pr = await api(`/api/p/${S.p.id}/probe`, { json: { t: F.t } });
    out.innerHTML = `<h3 style="margin-top:12px">Kết quả đọc thử tại ${fmt(F.t)}</h3>` + pr.bands.map((r, i) => {
      const [cls, msg] = WHY[r.reason](r, pr);
      return `<div class="pband"><h4>${esc(r.name)} <span class="tag">${esc(LANGS[r.lang] || r.lang)}</span>${r.primary ? '<span class="tag">dòng chính</span>' : ''}</h4>
        <div class="why">Dải quét (ảnh cắt từ video):</div><img src="${r.crop}" alt="">
        <div class="why">Chữ app tách được (đen trên trắng):</div><img src="${r.mask}" alt="">
        <div class="ptxt">${r.text ? '“' + esc(r.text) + '”' : '<i>không đọc được chữ</i>'}</div>
        <div class="why ${cls}">${msg}</div>
        ${r.text ? `<button class="btn mini" type="button" data-mk="${i}">+ Tạo câu từ chữ này tại ${fmt(F.t)}</button>` : ''}</div>`;
    }).join('');
    out._res = pr.bands;
  } catch (e) { toast(e.message, true); }
  b.classList.remove('busy'); b.disabled = false;
};
$('#probeOut').addEventListener('click', e => {
  const k = e.target.closest('[data-mk]'); if (!k) return;
  const r = $('#probeOut')._res[+k.dataset.mk];
  $('#fAdd').click();
  const row = $('#cues').children[S.cur], ta = row && $('textarea.src', row);
  if (ta) { ta.value = r.text; ta.dispatchEvent(new Event('input', { bubbles: true })); }
});


/* ---- đối chiếu bằng Whisper ---- */
function asrUncovered() {
  const segs = S.p.asr?.segments || [], cs = S.p.cues, out = [];
  segs.forEach((g, i) => {
    const d = g.end - g.start; if (d < 0.4 || (g.text || '').trim().length < 2) return;
    const need = Math.min(0.3, d * 0.5);
    const hit = cs.some(c => Math.min(c.end + 0.4, g.end) - Math.max(c.start - 0.4, g.start) >= need);
    if (!hit) out.push({ ...g, i });
  });
  return out;
}
function renderAsr() {
  const a = S.p.asr, st = S.status.asr || {}, d = S.p.info.duration;
  const sel = $('#asr_model');
  if (!sel.options.length) {
    sel.innerHTML = (st.models || []).map(m => `<option value="${m}">${m}${m === st.default_model ? ' (đề nghị)' : ''}</option>`).join('');
    sel.value = st.default_model || 'small';
  }
  $('#asrHint').textContent = !st.available ? 'Máy xử lý chưa cài Whisper (faster-whisper). Colab: chạy lại ô cài đặt mới nhất; máy tính: pip install -r requirements-asr.txt.'
    : `Nghe giọng nói rồi so với các câu OCR: chỗ có người nói mà không có câu nào sẽ được đánh dấu. Máy này dùng ${st.device === 'cuda' ? 'GPU (nhanh)' : 'CPU (chậm, nên chọn tiny/base/small)'}; lần đầu phải tải mô hình về.`;
  $('#asrBtn').disabled = !st.available;
  const miss = a ? asrUncovered() : [], bar = $('#tlSp');
  bar.innerHTML = (a?.segments || []).map((g, i) => `<i class="${miss.some(m => m.i === i) ? 'miss' : ''}" style="left:${g.start / d * 100}%;width:${Math.max(.2, (g.end - g.start) / d * 100)}%"></i>`).join('');
  $('#tlSpBar').style.display = a ? 'block' : 'none';
  $('#asrSummary').textContent = a ? `Whisper (${a.model}, ngôn ngữ ${a.lang || '?'}) nghe được ${a.segments.length} đoạn; ${miss.length} đoạn chưa có câu OCR tương ứng.` : '';
  $('#asrBulk').hidden = !miss.length;
  $('#asrList').innerHTML = miss.slice(0, 60).map(m => `<div class="asritem"><span class="tm">${fmt(m.start)}–${fmt(m.end)}</span><span class="tx">${esc(m.text)}</span><span class="bt"><button class="btn mini" type="button" data-av="${m.i}">Xem</button><button class="btn mini" type="button" data-ad="${m.i}">Quét kỹ</button><button class="btn mini" type="button" data-aa="${m.i}">+ Thêm</button></span></div>`).join('')
    + (miss.length > 60 ? `<div class="hint">và ${miss.length - 60} đoạn nữa (thêm hoặc quét lại bớt rồi danh sách sẽ ngắn lại)</div>` : '');
}
function insertCue(start, end, src, extra = {}) {
  const cs = S.p.cues; let i = cs.findIndex(c => c.start > start); if (i < 0) i = cs.length;
  cs.splice(i, 0, { id: 0, start: +start.toFixed(2), end: +end.toFixed(2), src, ref: '', vi: '', ...extra });
  return i;
}
$('#asrBtn').onclick = async () => {
  try { await api(`/api/p/${S.p.id}/asr`, { json: { model: $('#asr_model').value, lang: $('#asr_lang').value } }); }
  catch (e) { return toast(e.message, true); }
  runTask($('#asrProg'), onAsrDone, $('#asrBtn'));
};
async function onAsrDone(t) {
  S.p = await api('/api/p/' + S.p.id); renderCues(); updateStat();
  toast(`Whisper nghe được ${t.result?.segments ?? 0} đoạn. Xem danh sách đoạn chưa có câu bên dưới.`);
}
$('#asrList').addEventListener('click', e => {
  const v = e.target.closest('[data-av]'), a = e.target.closest('[data-aa]'), d = e.target.closest('[data-ad]');
  if (d) { deepScan([S.p.asr.segments[+d.dataset.ad]], d); return; }
  if (v) { const g = S.p.asr.segments[+v.dataset.av]; F.a = g.start; F.b = g.end; setF((g.start + g.end) / 2); $('#fSlider').scrollIntoView({ behavior: 'smooth', block: 'center' }); }
  if (a) { const g = S.p.asr.segments[+a.dataset.aa], i = insertCue(g.start, g.end, g.text, { asr: true }); renumber(); S.cur = i; renderCues(i); updateStat(); save(); toast('Đã thêm câu từ Whisper (nhớ kiểm tra lại chữ).'); }
});
$('#asrAddAll').onclick = () => {
  const m = asrUncovered(); if (!m.length) return;
  if (!confirm(`Thêm ${m.length} câu bằng chữ Whisper? Chữ này là lời nói được nhận dạng, có thể sai chính tả hoặc khác phụ đề gốc; các câu thêm có nhãn "từ Whisper".`)) return;
  m.forEach(g => insertCue(g.start, g.end, g.text, { asr: true }));
  renumber(); renderCues(); updateStat(); save(); toast(`Đã thêm ${m.length} câu từ Whisper.`);
};
async function deepScan(segs, btn) {
  try {
    await save(true);
    await api(`/api/p/${S.p.id}/rescan`, { json: { deep: true, ranges: segs.map(g => [g.start - 0.4, g.end + 0.4]), thr: +$('#rs_thr').value } });
  } catch (e) { return toast(e.message, true); }
  runTask($('#rsProg'), onRescanDone, btn);
}
$('#asrRescan').onclick = () => { const m = asrUncovered(); if (m.length) deepScan(m, $('#asrRescan')); };


/* ---------------------------------------------------------------- lồng tiếng (giọng đọc từng câu) */
const spLen = t => (t || '').replace(/\s+/g, ' ').trim().length;
function dubMax() { return +(cfg().dub?.max_speed || S.status.tts?.max_speed || 1.35); }
function dubSlot(i) { const cs = S.p.cues, c = cs[i], gap = cs[i + 1] ? cs[i + 1].start - c.end : 0.5; return Math.max(0.2, (c.end - c.start) + Math.min(0.25, Math.max(0, gap))); }
/* giống hệt tts.fit ở máy xử lý: trả về { speed, st } với st = none | stale | ok | fast | long */
function dubFit(i) {
  const c = S.p.cues[i], d = c.dub, slot = dubSlot(i);
  if (!d || !d.dur) return { speed: 1, st: 'none', slot };
  if (d.text !== c.vi) return { speed: 1, st: 'stale', slot };
  let sp = d.user ? +d.user : (d.dur <= slot * 1.05 ? 1 : Math.min(dubMax(), d.dur / slot));
  sp = Math.max(0.8, Math.min(2, sp));
  const eff = d.dur / sp;
  return { speed: Math.round(sp * 100) / 100, st: eff > slot * 1.05 ? 'long' : (sp <= 1.05 ? 'ok' : 'fast'), slot, eff };
}
const DB = { none: 'Chưa có giọng', stale: 'Cần tạo lại giọng', ok: 'Khớp tốt', fast: 'Tăng tốc', long: 'Quá dài' };
function dubRowInner(c, i) {
  const f = dubFit(i), d = c.dub;
  const label = f.st === 'fast' ? `Tăng tốc x${f.speed.toFixed(2)}` : DB[f.st];
  const info = d && d.dur ? `${spLen(c.vi)} ký tự · giọng ${d.dur.toFixed(1)}s · khung ${f.slot.toFixed(1)}s` : `${spLen(c.vi)} ký tự · khung ${f.slot.toFixed(1)}s`;
  const ctl = d && d.dur && f.st !== 'stale' ? `<button class="btn mini" type="button" data-dub="play">▶ Nghe</button>
    <span class="spd"><button class="btn mini" type="button" data-dub="slow">−</button><b>x${f.speed.toFixed(2)}${d.user ? '' : ' tự động'}</b><button class="btn mini" type="button" data-dub="fastr">+</button>${d.user ? '<button class="btn mini" type="button" data-dub="auto">Tự động</button>' : ''}</span>` : '';
  return `<span class="dbadge ${f.st}">${label}</span><span>${info}</span>${ctl}<button class="btn mini" type="button" data-dub="regen">${d && d.dur ? 'Tạo lại giọng' : 'Tạo giọng'}</button>`;
}
function refreshDubRow(i) { const row = $('#cues').children[i], el = row && $('.dubrow', row); if (el) el.innerHTML = dubRowInner(S.p.cues[i], i); updateDubSummary(); }
function updateDubSummary() {
  if (!S.dubOn) return;
  const n = { none: 0, stale: 0, ok: 0, fast: 0, long: 0 }; S.p.cues.forEach((c, i) => { if (spLen(c.vi)) n[dubFit(i).st]++; });
  $('#dubSum').textContent = `${n.ok} khớp · ${n.fast} tăng tốc · ${n.long} quá dài · ${n.none + n.stale} chưa có giọng`;
  $('#dubShort').disabled = !n.long;
}
async function initDub() {
  const st = S.status.tts || {};
  $('#dubMax').value = dubMax();
  $('#dubHint').textContent = st.available ? 'Giọng đọc dùng Edge TTS (cần mạng, miễn phí, dịch vụ không chính thức của Microsoft). Tạo giọng xong, bật "Thêm giọng lồng tiếng" ở bước 4 rồi xuất video.' : 'Máy xử lý chưa cài edge-tts (Colab: chạy lại ô cài đặt mới nhất; máy tính: pip install edge-tts).';
  $('#dubAll').disabled = $('#dubRedo').disabled = !st.available;
  const sel = $('#dubVoice');
  if (!sel.options.length) {
    const r = await api('/api/tts/voices').catch(() => ({ voices: [], default: '' }));
    sel.innerHTML = r.voices.map(v => `<option value="${esc(v.id)}">${esc(v.name)}</option>`).join('');
    sel.value = S.p.dub?.voice || r.default || r.voices[0]?.id;
  } else sel.value = S.p.dub?.voice || sel.value;
  updateDubSummary();
}
$('#dubBtn').onclick = () => { S.dubOn = !S.dubOn; $('#dubPanel').hidden = !S.dubOn; renderCues(); if (S.dubOn) initDub(); };
$('#dubVoice').onchange = () => { S.p.dub = { ...(S.p.dub || {}), voice: $('#dubVoice').value }; save(); };
$('#dubMax').onchange = () => { S.p.settings ||= {}; deepSet(S.p.settings, 'dub.max_speed', Math.max(1, Math.min(2, +$('#dubMax').value || 1.35))); save(); renderCues(); updateDubSummary(); };
async function startDub(indices, force) {
  try { await save(true); await api(`/api/p/${S.p.id}/tts/synth`, { json: { voice: $('#dubVoice').value, indices, force } }); }
  catch (e) { return toast(e.message, true); }
  runTask($('#dubProg'), onDubDone, $('#dubAll'));
}
async function onDubDone(t) {
  S.p = await api('/api/p/' + S.p.id); renderCues(); updateStat(); updateDubSummary();
  const r = t.result || {};
  if (t.kind === 'shorten' || r.shortened != null) toast(r.long ? `Đã rút gọn ${r.shortened}/${r.long} câu quá dài và tạo lại giọng.` : 'Không có câu nào quá dài.');
  else toast(r.failed ? `Xong, còn ${r.failed} câu chưa tạo được giọng.` : `Đã tạo giọng cho ${r.cues ?? 0} câu.`, !!r.failed);
}
$('#dubAll').onclick = () => startDub(S.p.cues.map((c, i) => i).filter(i => spLen(S.p.cues[i].vi) && ['none', 'stale'].includes(dubFit(i).st)), false);
$('#dubRedo').onclick = () => { if (confirm('Tạo lại giọng cho tất cả các câu (giữ tốc độ bạn đã chỉnh nếu chữ không đổi)?')) startDub(null, true); };
$('#dubShort').onclick = async () => {
  let key = lsGet('vl_key') || prompt('Dán API key Claude để rút gọn các câu quá dài (không lưu lại):') || '';
  key = key.trim(); if (!key) return;
  try { await save(true); await api(`/api/p/${S.p.id}/shorten`, { json: { api_key: key, model: S.p.translate?.model || S.status.default_model } }); }
  catch (e) { return toast(e.message, true); }
  runTask($('#dubProg'), onDubDone, $('#dubShort'));
};
let dubAudio = null;
$('#cues').addEventListener('click', async e => {
  const b = e.target.closest('[data-dub]'); if (!b) return;
  e.stopPropagation();
  const row = b.closest('.cue'), i = +row.dataset.i, c = S.p.cues[i], act = b.dataset.dub;
  if (act === 'play') {
    const f = dubFit(i); dubAudio?.pause();
    dubAudio = new Audio(U(`/api/p/${S.p.id}/tts/audio/${c.dub.key}`)); dubAudio.playbackRate = f.speed; dubAudio.play().catch(err => toast('Không phát được: ' + err.message, true));
  } else if (act === 'slow' || act === 'fastr') {
    const f = dubFit(i); c.dub.user = Math.max(0.8, Math.min(2, +(f.speed + (act === 'fastr' ? 0.05 : -0.05)).toFixed(2))); save(); refreshDubRow(i);
  } else if (act === 'auto') { c.dub.user = null; save(); refreshDubRow(i); }
  else if (act === 'regen') {
    if (!spLen(c.vi)) return toast('Câu này chưa có bản dịch để đọc.', true);
    b.disabled = true; b.textContent = 'Đang tạo...';
    try { await save(true); c.dub = await api(`/api/p/${S.p.id}/tts/cue`, { json: { i, voice: $('#dubVoice').value, force: true } }); }
    catch (err) { toast(err.message, true); }
    refreshDubRow(i);
  }
}, true);

/* nhập SRT */
$('#impBtn').onclick = () => { $('#impText').value = ''; $('#impFile').value = ''; $('#dlgImp').showModal(); };
$('#impFile').onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  try {
    const b = new Uint8Array(await f.arrayBuffer());
    const enc = b[0] === 0xFF && b[1] === 0xFE ? 'utf-16le' : b[0] === 0xFE && b[1] === 0xFF ? 'utf-16be' : 'utf-8';
    $('#impText').value = new TextDecoder(enc).decode(b).replace(/^\uFEFF/, '');
    toast(`Đã đọc file ${f.name}. Bấm Nhập để áp dụng.`);
  } catch (err) { toast('Không đọc được file: ' + err.message, true); }
};
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
    ['old.enabled', 'toggle', 'Xoá sub cũ bằng làm nhoè'],
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
    ['sub.enabled', 'toggle', 'Ghép phụ đề Việt vào video'],
    ['sub.font', 'select', 'Font', { fonts: true }],
    ['sub.size', 'range', 'Cỡ chữ (% chiều cao)', { min: 2.5, max: 9, step: .1, mul: 100 }],
    ['sub.outline', 'range', 'Độ dày viền', { min: 0, max: 8, step: .2 }],
    ['sub.margin_v', 'range', 'Cách đáy (% chiều cao)', { min: 1, max: 20, step: .2, mul: 100 }],
    ['sub.color', 'color', 'Màu chữ'],
    ['sub.outline_color', 'color', 'Màu viền'],
  ]],
  ['Lồng tiếng', false, [
    ['dub.enabled', 'toggle', 'Thêm giọng lồng tiếng vào video (tạo giọng ở bước 3)'],
    ['dub.orig', 'select', 'Tiếng gốc', { opts: { duck: 'Tự nhỏ đi khi có giọng lồng', low: 'Nhỏ cố định', mute: 'Tắt hẳn', keep: 'Giữ nguyên' } }],
    ['dub.vol', 'range', 'Âm lượng giọng lồng (%)', { min: 50, max: 200, step: 5, mul: 100 }],
    ['dub.orig_vol', 'range', 'Âm lượng tiếng gốc khi chọn "Nhỏ cố định" (%)', { min: 0, max: 100, step: 5, mul: 100 }],
    ['dub.max_speed', 'range', 'Tăng tốc giọng tối đa (x)', { min: 1, max: 2, step: 0.05 }],
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
  const groups = isBrand() ? SCHEMA.filter(g => !['Xoá sub cũ', 'Phụ đề tiếng Việt', 'Lồng tiếng'].includes(g[0])).map(g => ['Logo', 'Chữ watermark ở góc', 'Chữ watermark bay vòng'].includes(g[0]) ? [g[0], true, g[2]] : g) : SCHEMA;
  $('#settingsCard').innerHTML = `<h2>${isBrand() ? 'Logo và watermark' : 'Hiệu ứng và phụ đề'}</h2>` + groups.map(([title, open, fields], gi) => `
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
  const sel = $('#pvCue'), tm = $('#pvTime');
  sel.hidden = isBrand(); tm.hidden = !isBrand();
  if (isBrand()) { tm.max = Math.max(1, Math.floor(S.p.info.duration)); if (!tm._set) { tm.value = Math.min(tm.max, 10); tm._set = 1; } preview(); api(`/api/p/${S.p.id}/check`).then(r => { showResult(r.checks, false); }).catch(() => { }); return; }
  sel.innerHTML = S.p.cues.map((c, i) => `<option value="${i}">Câu #${c.id} · ${fmt(c.start)}</option>`).join('') || '<option value="-1">Không có câu</option>';
  sel.value = Math.min(S.cur, S.p.cues.length - 1);
  preview();
  api(`/api/p/${S.p.id}/check`).then(r => { showResult(r.checks, false); }).catch(() => { });
}
$('#pvCue').onchange = e => { S.cur = +e.target.value; preview(); };
$('#pvTime').addEventListener('input', debounce(() => preview(), 400));
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
  let t;
  if (isBrand()) t = +$('#pvTime').value;
  else { if (!S.p.cues.length) return; const c = S.p.cues[Math.max(0, +$('#pvCue').value)] || S.p.cues[0]; t = (c.start + c.end) / 2; }
  const n = ++S.pvN;
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
  if (cfg().sub.enabled !== false && !S.p.cues.some(c => (c.vi || '').trim())) return toast('Chưa có bản dịch. Hãy dịch hoặc nhập SRT ở bước 3, hoặc tắt "Ghép phụ đề Việt" nếu chỉ gắn logo/watermark.', true);
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
  $('#dlVi').hidden = $('#dlSrc').hidden = isBrand();
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


/* ---------------------------------------------------------------- công cụ riêng: ghép video, cắt video, tách MP3 */
const TL = { on: false, merge: [], cut: null, segs: [], t: 0, mp: null };
const vinfo = v => `${v.name} · ${dur(v.duration)} · ${v.width}x${v.height} · ${mb(v.size)}${v.has_audio ? '' : ' · không có tiếng'}`;
function showTools(on) {
  TL.on = on;
  if (on) {
    $$('.step').forEach(x => x.hidden = true); $('#tools').hidden = false;
    $('#toolsBtn').textContent = '← Về dự án'; $$('#stepper button').forEach(b => b.classList.remove('on'));
    loadToolOut();
    api('/api/tools/task').then(t => { if (t.state === 'running') runTask($('#toolProg'), onToolDone, null, '/api/tools'); }).catch(() => { });
  } else { $('#tools').hidden = true; $('#toolsBtn').textContent = 'Công cụ'; setStep(S.step, true); }
}
$('#toolsBtn').onclick = () => showTools(!TL.on);
$('#toolTabs').addEventListener('click', e => {
  const b = e.target.closest('[data-tool]'); if (!b) return;
  $$('#toolTabs button').forEach(x => x.classList.toggle('on', x === b));
  ['merge', 'cut', 'mp3', 'local'].forEach(k => $('#tool_' + k).hidden = k !== b.dataset.tool);
});
async function loadToolOut() {
  const list = await api('/api/tools/outputs').catch(() => []);
  $('#toolOut').innerHTML = list.length ? list.map((o, i) => `<div class="titem" data-n="${esc(o.name)}" data-a="${o.audio ? 1 : 0}">
    <span class="n">${i + 1}</span><div class="nm"><b>${esc(o.name)}</b><small>${mb(o.size)}</small></div>
    <div class="bt"><a class="btn mini primary" href="${U('/api/tools/file/' + encodeURIComponent(o.name) + '?download=1')}">Tải về</a><button class="btn mini" type="button" data-play>${o.audio ? 'Nghe' : 'Xem'}</button><button class="btn mini danger" type="button" data-del>×</button></div></div>`).join('')
    : '<div class="empty">Chưa có kết quả nào.</div>';
}
$('#toolOut').addEventListener('click', async e => {
  const it = e.target.closest('.titem'); if (!it) return;
  const n = it.dataset.n;
  if (e.target.closest('[data-del]')) {
    if (!confirm('Xoá file này khỏi máy xử lý?')) return;
    await api('/api/tools/file/' + encodeURIComponent(n), { method: 'DELETE' }).catch(err => toast(err.message, true)); loadToolOut();
  } else if (e.target.closest('[data-play]')) {
    const ex = $('audio,video', it); if (ex) { ex.pause(); ex.remove(); return; }
    const m = document.createElement(it.dataset.a === '1' ? 'audio' : 'video'); m.controls = true; m.preload = 'metadata'; m.src = U('/api/tools/file/' + encodeURIComponent(n)); it.appendChild(m); m.play().catch(() => { });
  }
});
function onToolDone(t) {
  const r = t.result || {};
  toast(`Xong! Đã tạo ${r.files?.length || 0} file. Bấm "Tải về" ở phần Kết quả.`);
  const h = $('#toolExport'); h.hidden = !r.export?.length; if (r.export?.length) h.textContent = 'Đã chép thêm vào thư mục lưu trữ: ' + r.export.join(', ');
  loadToolOut();
}
async function startTool(path, body, btn) {
  try { await api('/api/tools/' + path, { json: body }); } catch (e) { return toast(e.message, true); }
  runTask($('#toolProg'), onToolDone, btn, '/api/tools');
}

/* ---- hộp chọn video (thư mục Drive, dự án, đã tải, tải lên, link, đường dẫn) ---- */
let PK = null;
function pickSources(multi, title) {
  return new Promise(async resolve => {
    PK = { multi, sel: [], paths: [], resolve };
    $('#pickTitle').textContent = title || (multi ? 'Chọn các video' : 'Chọn một video');
    $('#pkMsg').textContent = '';
    $('#pkFile').multiple = !!multi;
    await renderPick(); $('#dlgPick').showModal();
  });
}
async function renderPick() {
  const r = await api('/api/tools/sources').catch(() => ({ media: [], projects: [], inputs: [] }));
  const row = (k, v, name, size) => { const on = PK.sel.some(x => x.k === k && x.v === v); return `<label class="prow ${on ? 'sel' : ''}" data-k="${k}" data-v="${esc(v)}"><input type="${PK.multi ? 'checkbox' : 'radio'}" ${on ? 'checked' : ''} tabindex="-1"><span>${esc(name)}</span><small>${size != null ? mb(size) : ''}</small></label>`; };
  const sec = (t, html) => html ? `<h3>${t}</h3>${html}` : '';
  $('#pickBody').innerHTML =
    sec('Video trong thư mục Drive / máy xử lý', r.media.map(m => row('media', m.name, m.name, m.size)).join('')) +
    sec('Video của các dự án', r.projects.map(m => row('project', m.id, m.name, m.size)).join('')) +
    sec('Đã tải lên / tải về', r.inputs.map(m => row('input', m.name, m.name, m.size)).join('')) +
    sec('Đường dẫn đã nhập', PK.paths.map(v => row('path', v, v, null)).join('')) ||
    '<div class="empty">Chưa có video nào. Mở mục "Thêm video mới" bên dưới để tải lên, dán link hoặc nhập đường dẫn.</div>';
}
$('#pickBody').addEventListener('click', e => {
  const r = e.target.closest('.prow'); if (!r) return; e.preventDefault();
  const k = r.dataset.k, v = r.dataset.v, i = PK.sel.findIndex(x => x.k === k && x.v === v);
  if (i >= 0) PK.sel.splice(i, 1); else { if (!PK.multi) PK.sel = []; PK.sel.push({ k, v }); }
  renderPick();
});
$('#dlgPick').addEventListener('close', () => {
  const ok = $('#dlgPick').returnValue === 'go', sel = PK.sel;
  PK.resolve(ok ? sel.map(x => ({ [x.k]: x.v })) : []);
});
function pkAdd(k, v) { if (!PK.multi) PK.sel = []; if (!PK.sel.some(x => x.k === k && x.v === v)) PK.sel.push({ k, v }); renderPick(); }
function upload1(f, onProg) {  // tải 1 file lên /api/tools/upload bằng XHR để có % tiến độ
  return new Promise((ok, bad) => {
    const x = new XMLHttpRequest(), fd = new FormData(); fd.append('file', f);
    x.open('POST', CFG.base + '/api/tools/upload');
    if (CFG.token) x.setRequestHeader('X-Access-Token', CFG.token);
    x.upload.onprogress = ev => ev.lengthComputable && onProg(ev.loaded / ev.total);
    x.onload = () => { let j = {}; try { j = JSON.parse(x.responseText); } catch (e) { } x.status < 300 && j.input ? ok(j) : bad(new Error(j.error || (x.status === 401 ? 'Sai hoặc thiếu mã truy cập.' : x.status === 413 ? 'File quá lớn cho đường truyền này (tunnel giới hạn ~100 MB).' : 'Tải lên lỗi (' + x.status + ')'))); };
    x.onerror = () => bad(new Error('Mất kết nối khi tải lên. Với file lớn hãy dùng thư mục Drive hoặc đường dẫn.'));
    x.send(fd);
  });
}
$('#pkFile').onchange = async e => {
  const files = [...e.target.files]; e.target.value = ''; if (!files.length) return;
  const btn = $('#pickGo'); btn.disabled = true; let okn = 0; const fails = [];
  for (let i = 0; i < files.length; i++) {  // lần lượt, giữ đúng thứ tự đã chọn
    const f = files[i], tag = files.length > 1 ? `(${i + 1}/${files.length}) ` : '';
    try {
      const r = await upload1(f, p => { $('#pkMsg').textContent = `${tag}Đang tải ${f.name}: ${Math.round(p * 100)}%`; });
      pkAdd('input', r.input); okn++;
    } catch (err) { fails.push(`${f.name}: ${String(err.message).split('\n')[0].slice(0, 90)}`); }
  }
  btn.disabled = false;
  $('#pkMsg').textContent = `Đã tải lên ${okn}/${files.length} video.` + (okn ? ' Đã chọn sẵn, bấm "Chọn" để thêm vào danh sách.' : '') + (fails.length ? ' Lỗi: ' + fails.join('; ') : '');
};
$('#pkPathBtn').onclick = () => { const v = $('#pkPath').value.trim(); if (!v) return; if (!PK.paths.includes(v)) PK.paths.push(v); $('#pkPath').value = ''; pkAdd('path', v); };
$('#pkUrlBtn').onclick = async () => {
  const url = $('#pkUrl').value.trim(); if (!url) return;
  try { await api('/api/import_url', { json: { url, to: 'tools' } }); } catch (err) { $('#pkMsg').textContent = err.message; return; }
  const b = $('#pkUrlBtn'); b.disabled = true;
  const iv = setInterval(async () => {
    let t; try { t = await api('/api/import_url'); } catch (err) { return; }
    $('#pkMsg').textContent = (t.message || '') + (t.total ? ` ${mb(t.done)} / ${mb(t.total)}` : t.done ? ' ' + mb(t.done) : '');
    if (t.state === 'running') return;
    clearInterval(iv); b.disabled = false;
    if (t.state === 'error') $('#pkMsg').textContent = t.error; else if (t.input) { $('#pkMsg').textContent = 'Đã tải về.'; $('#pkUrl').value = ''; pkAdd('input', t.input); }
  }, 1000);
};
['pkUrl', 'pkPath'].forEach(id => $('#' + id).addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); $(id === 'pkUrl' ? '#pkUrlBtn' : '#pkPathBtn').click(); } }));
async function regSources(srcs) {
  const out = [];
  for (const src of srcs) { try { out.push(await api('/api/tools/source', { json: { src } })); } catch (e) { toast(e.message, true); } }
  return out;
}

/* ---- ghép video ---- */
function renderMerge() {
  const L = TL.merge, box = $('#mgList');
  box.innerHTML = L.length ? L.map((v, i) => `<div class="titem" data-i="${i}"><span class="n">${i + 1}</span><div class="nm"><b>${esc(v.name)}</b><small>${dur(v.duration)} · ${v.width}x${v.height} · ${mb(v.size)}${v.has_audio ? '' : ' · không có tiếng'}</small></div>
    <div class="bt"><button class="btn mini" type="button" data-up ${i ? '' : 'disabled'}>↑</button><button class="btn mini" type="button" data-dn ${i < L.length - 1 ? '' : 'disabled'}>↓</button><button class="btn mini danger" type="button" data-rm>×</button></div></div>`).join('')
    : '<div class="empty">Chưa chọn video nào. Bấm "+ Thêm video".</div>';
  const same = L.length > 1 && L.every(v => v.codec === L[0].codec && v.width === L[0].width && v.height === L[0].height && v.has_audio === L[0].has_audio);
  $('#mgTotal').textContent = L.length ? `${L.length} video · tổng ${dur(L.reduce((a, v) => a + v.duration, 0))}.` + (L.length > 1 ? (same ? ' Cùng định dạng: ghép nhanh, không mất chất lượng.' : ' Khác định dạng: sẽ tự mã hoá lại về cùng khung hình (chậm hơn).') : '') : '';
}
$('#mgAdd').onclick = async () => { const s = await pickSources(true, 'Chọn các video cần ghép (theo thứ tự chọn)'); TL.merge.push(...await regSources(s)); renderMerge(); };
$('#mgList').addEventListener('click', e => {
  const it = e.target.closest('.titem'); if (!it) return; const i = +it.dataset.i, L = TL.merge;
  if (e.target.closest('[data-rm]')) L.splice(i, 1);
  else if (e.target.closest('[data-up]') && i > 0) [L[i - 1], L[i]] = [L[i], L[i - 1]];
  else if (e.target.closest('[data-dn]') && i < L.length - 1) [L[i + 1], L[i]] = [L[i], L[i + 1]];
  renderMerge();
});
$('#mgRun').onclick = () => {
  if (TL.merge.length < 2) return toast('Hãy chọn ít nhất 2 video để ghép.', true);
  startTool('merge', { sids: TL.merge.map(v => v.sid), name: $('#mgName').value.trim(), mode: $('#mgMode').value, height: +$('#mgRes').value }, $('#mgRun'));
};
renderMerge();

/* ---- cắt video ---- */
const showFrameC = debounce(() => TL.cut && loadImg($('#frameC'), $('#skelC'), U(`/api/tools/frame/${TL.cut.sid}?t=${TL.t.toFixed(2)}&w=960&_=${Date.now() % 100000}`)), 150);
function setC(t) { if (!TL.cut) return; TL.t = Math.max(0, Math.min(TL.cut.duration, t)); $('#ctSlider').value = TL.t; $('#ctOut').textContent = fmt(TL.t); showFrameC(); }
function renderSegs() {
  $('#ctSegs').innerHTML = TL.segs.map((g, i) => `<div class="titem tseg" data-i="${i}"><span class="n">${i + 1}</span>
    <label>Từ<input type="text" data-k="a" value="${fmt(g.a)}"></label><label>Đến<input type="text" data-k="b" value="${fmt(g.b)}"></label>
    <div class="bt"><button class="btn mini" type="button" data-sa>Đầu = thanh</button><button class="btn mini" type="button" data-sb>Cuối = thanh</button><button class="btn mini danger" type="button" data-rm>×</button></div></div>`).join('');
}
$('#ctPick').onclick = async () => {
  const [src] = await pickSources(false, 'Chọn video cần cắt'); if (!src) return;
  const [v] = await regSources([src]); if (!v) return;
  TL.cut = v; TL.segs = [{ a: 0, b: Math.min(v.duration, 10) }];
  $('#ctInfo').textContent = vinfo(v); $('#ctBody').hidden = false; $('#ctSlider').max = v.duration.toFixed(1);
  $('#ctName').placeholder = v.name.replace(/\.[^.]+$/, '') + '_cat'; renderSegs(); setC(0);
};
$('#ctSlider').addEventListener('input', e => setC(+e.target.value));
$$('[data-cseek]').forEach(b => b.onclick = () => setC(TL.t + +b.dataset.cseek));
$('#ctAdd').onclick = () => { if (!TL.cut) return; TL.segs.push({ a: +TL.t.toFixed(2), b: +Math.min(TL.cut.duration, TL.t + 5).toFixed(2) }); renderSegs(); };
$('#ctSegs').addEventListener('click', e => {
  const it = e.target.closest('.titem'); if (!it) return; const g = TL.segs[+it.dataset.i];
  if (e.target.closest('[data-rm]')) TL.segs.splice(+it.dataset.i, 1);
  else if (e.target.closest('[data-sa]')) g.a = +TL.t.toFixed(2);
  else if (e.target.closest('[data-sb]')) g.b = +TL.t.toFixed(2);
  else return;
  renderSegs();
});
$('#ctSegs').addEventListener('change', e => { const it = e.target.closest('.titem'), k = e.target.dataset.k; if (!it || !k) return; const g = TL.segs[+it.dataset.i]; g[k] = parseT(e.target.value); e.target.value = fmt(g[k]); if (TL.cut) setC(g[k]); });
$('#ctRun').onclick = () => {
  if (!TL.cut) return toast('Hãy chọn video trước.', true);
  if (!TL.segs.length) return toast('Chưa có đoạn nào để cắt.', true);
  const bad = TL.segs.findIndex(g => !(g.b > g.a)); if (bad >= 0) return toast(`Đoạn ${bad + 1}: điểm cuối phải sau điểm đầu.`, true);
  startTool('cut', { sid: TL.cut.sid, segments: TL.segs.map(g => [g.a, g.b]), exact: $('#ctExact').checked, join: $('#ctJoin').checked, name: $('#ctName').value.trim() }, $('#ctRun'));
};

/* ---- tách MP3 ---- */
$('#mpPick').onclick = async () => {
  const [src] = await pickSources(false, 'Chọn video cần tách MP3'); if (!src) return;
  const [v] = await regSources([src]); if (!v) return;
  TL.mp = v; $('#mpInfo').textContent = vinfo(v); $('#mpBody').hidden = !v.has_audio;
  if (!v.has_audio) toast('Video này không có âm thanh để tách.', true);
  $('#mpName').placeholder = v.name.replace(/\.[^.]+$/, '');
};
$('#mpRun').onclick = () => {
  if (!TL.mp) return toast('Hãy chọn video trước.', true);
  const a = $('#mpA').value.trim(), b = $('#mpB').value.trim();
  startTool('audio', { sid: TL.mp.sid, bitrate: +$('#mpBr').value, start: a ? parseT(a) : null, end: b ? parseT(b) : null, name: $('#mpName').value.trim() }, $('#mpRun'));
};



/* ---------------------------------------------------------------- xử lý ngay trên máy bạn (ffmpeg.wasm, không tải video lên) */
const LC = { ff: null, loading: null, log: [], cutFile: null, mpFile: null, segs: [], out: [] };
$('#lcTabs').addEventListener('click', e => {
  const b = e.target.closest('[data-lc]'); if (!b) return;
  $$('#lcTabs button').forEach(x => x.classList.toggle('on', x === b));
  ['test', 'cut', 'mp3'].forEach(k => $('#lc_' + k).hidden = k !== b.dataset.lc);
});
async function lcEngine() {
  if (LC.ff) return LC.ff;
  if (!LC.loading) LC.loading = (async () => {
    const { FFmpeg } = await import('/vendor/ffmpeg/index.js');
    const ff = new FFmpeg();
    ff.on('log', ({ message }) => { LC.log.push(message); if (LC.log.length > 200) LC.log.shift(); });
    await ff.load({ coreURL: '/vendor/ffmpeg/ffmpeg-core.js', wasmURL: '/vendor/ffmpeg/ffmpeg-core.wasm' });
    LC.ff = ff; return ff;
  })();
  try { return await LC.loading; } finally { LC.loading = null; }
}
function lcProg(f, msg) { const r = $('#lcProg'); r.hidden = false; $('.bar i', r).style.width = (Math.max(0, Math.min(1, f)) * 100).toFixed(1) + '%'; $('.pm', r).textContent = msg || ''; $('.pp', r).textContent = Math.round(Math.max(0, Math.min(1, f)) * 100) + '%'; }
const lcExt = n => (n.match(/\.[A-Za-z0-9]{1,5}$/) || ['.mp4'])[0].toLowerCase();
const lcBase = n => n.replace(/\.[^.]+$/, '');
/* chạy ffmpeg trên một File (gắn bằng WORKERFS nên KHÔNG nạp cả file vào bộ nhớ). run(ff, inPath) trả về [{name, blob}] */
async function lcWith(file, btn, label, run) {
  btn?.classList.add('busy'); btn && (btn.disabled = true);
  let cancelled = false;
  $('#lcProg [data-cancel]').onclick = async () => { cancelled = true; try { LC.ff?.terminate(); } catch (e) { } LC.ff = null; toast('Đã huỷ'); $('#lcProg').hidden = true; };
  let ff = null, mounted = false;
  try {
    lcProg(0, 'Đang nạp ffmpeg (lần đầu phải tải ~32 MB)...');
    ff = await lcEngine();
    const prog = ({ progress }) => lcProg(0.05 + 0.9 * (isFinite(progress) ? progress : 0), label);
    ff.on('progress', prog);
    const safe = new File([file], 'input' + lcExt(file.name), { type: file.type });  // tên an toàn, không sao chép dữ liệu
    await ff.createDir('/in'); await ff.mount('WORKERFS', { files: [safe] }, '/in'); mounted = true;
    lcProg(0.05, label);
    const outs = await run(ff, '/in/' + safe.name);
    ff.off('progress', prog);
    lcProg(1, 'Xong'); setTimeout(() => { $('#lcProg').hidden = true; }, 700);
    return outs;
  } catch (e) {
    if (!cancelled) { const tail = LC.log.slice(-3).join(' | '); toast('Không xử lý được trên trình duyệt này: ' + (e.message || e) + (tail ? ' — ' + tail : '') + '. Hãy thử tab "Cắt video"/"Tách MP3" dùng máy xử lý.', true); }
    $('#lcProg').hidden = true; return null;
  } finally {
    try { if (mounted && LC.ff) { await LC.ff.unmount('/in'); await LC.ff.deleteDir('/in'); } } catch (e) { }
    btn?.classList.remove('busy'); btn && (btn.disabled = false);
  }
}
async function lcExec(ff, args) { const r = await ff.exec(args); if (r !== 0) throw new Error('ffmpeg trả về mã lỗi ' + r); }
async function lcRead(ff, path, name, mime) { const d = await ff.readFile(path); const blob = new Blob([d.buffer ? d : new Uint8Array(d)], { type: mime }); try { await ff.deleteFile(path); } catch (e) { } return { name, blob }; }
function lcAddOut(o) { LC.out.unshift({ ...o, url: URL.createObjectURL(o.blob) }); renderLcOut(); }
function renderLcOut() {
  $('#lcOut').innerHTML = LC.out.length ? LC.out.map((o, i) => `<div class="titem" data-i="${i}"><span class="n">${i + 1}</span><div class="nm"><b>${esc(o.name)}</b><small>${mb(o.blob.size)}</small></div>
    <div class="bt"><a class="btn mini primary" href="${o.url}" download="${esc(o.name)}">Tải về</a>${navigator.canShare && navigator.canShare({ files: [new File([o.blob], o.name, { type: o.blob.type })] }) ? '<button class="btn mini" type="button" data-share>Lưu/Chia sẻ</button>' : ''}<button class="btn mini danger" type="button" data-del>×</button></div></div>`).join('') : '<div class="empty">Chưa có kết quả.</div>';
}
$('#lcOut').addEventListener('click', async e => {
  const it = e.target.closest('.titem'); if (!it) return; const o = LC.out[+it.dataset.i];
  if (e.target.closest('[data-del]')) { URL.revokeObjectURL(o.url); LC.out.splice(+it.dataset.i, 1); renderLcOut(); }
  else if (e.target.closest('[data-share]')) { try { await navigator.share({ files: [new File([o.blob], o.name, { type: o.blob.type })] }); } catch (err) { if (err.name !== 'AbortError') toast('Không chia sẻ được: ' + err.message, true); } }
});
renderLcOut();

/* ---- kiểm tra thiết bị ---- */
$('#lcTestBtn').onclick = async () => {
  const out = $('#lcTestOut'), btn = $('#lcTestBtn'), rows = [];
  const add = (ok, t) => { rows.push(`<div class="titem" style="grid-template-columns:1fr"><span class="${ok ? 'okline' : 'badline'}">${ok ? '✓' : '✗'} ${esc(t)}</span></div>`); out.innerHTML = rows.join(''); };
  out.innerHTML = ''; btn.classList.add('busy'); btn.disabled = true;
  try {
    add(typeof WebAssembly === 'object', 'Trình duyệt hỗ trợ WebAssembly');
    add(typeof Worker !== 'undefined', 'Hỗ trợ Web Worker');
    add(typeof File !== 'undefined' && typeof Blob !== 'undefined', 'Hỗ trợ đọc file cục bộ');
    add(true, `Thiết bị: ${navigator.userAgent.match(/\(([^)]+)\)/)?.[1] || 'không rõ'}${navigator.deviceMemory ? ', RAM khoảng ' + navigator.deviceMemory + ' GB' : ''}`);
    const t0 = performance.now(); let ff;
    try { ff = await lcEngine(); add(true, `Nạp ffmpeg thành công (${((performance.now() - t0) / 1000).toFixed(1)} giây)`); }
    catch (e) { add(false, 'Không nạp được ffmpeg: ' + (e.message || e) + '. Thiết bị/trình duyệt này không chạy được chế độ trên máy bạn.'); throw e; }
    // thử mã hoá MP3 từ một đoạn âm thanh tự tạo (1 giây)
    const sr = 8000, n = sr, buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf), w = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
    w(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); w(8, 'WAVEfmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, sr, true); v.setUint32(28, sr * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, n * 2, true);
    for (let i = 0; i < n; i++) v.setInt16(44 + i * 2, Math.sin(i / 8) * 8000, true);
    await ff.writeFile('t.wav', new Uint8Array(buf));
    const t1 = performance.now(); await lcExec(ff, ['-i', 't.wav', '-c:a', 'libmp3lame', '-b:a', '128k', 't.mp3']);
    const mp3 = await ff.readFile('t.mp3'); await ff.deleteFile('t.wav'); await ff.deleteFile('t.mp3');
    add(mp3.length > 500, `Mã hoá MP3 chạy được (${mp3.length} byte, ${((performance.now() - t1) / 1000).toFixed(1)} giây)`);
    const f = $('#lcTestFile').files[0];
    if (f) {
      LC.log.length = 0; const safe = new File([f], 'input' + lcExt(f.name), { type: f.type }), t2 = performance.now();
      await ff.createDir('/in'); await ff.mount('WORKERFS', { files: [safe] }, '/in');
      try {
        await ff.exec(['-i', '/in/' + safe.name, '-t', '1', '-f', 'null', '-']);
        const dm = LC.log.join('\n').match(/Duration:\s*(\d+):(\d+):([\d.]+)/);
        add(!!dm, dm ? `Đọc được video "${f.name}" (${mb(f.size)}), thời lượng ${dm[1]}:${dm[2]}:${(+dm[3]).toFixed(0)}, mở file mất ${((performance.now() - t2) / 1000).toFixed(1)} giây, không nạp cả file vào bộ nhớ` : `Không đọc được thông tin video "${f.name}" (định dạng chưa hỗ trợ?)`);
      } finally { try { await ff.unmount('/in'); await ff.deleteDir('/in'); } catch (e) { } }
    }
    add(true, 'Kết luận: thiết bị này dùng được "Cắt nhanh" và "Tách MP3" ngay trên máy. Nếu việc thật bị tắt tab hay báo lỗi bộ nhớ, hãy dùng máy xử lý.');
  } catch (e) { if (!rows.length) add(false, String(e.message || e)); }
  btn.classList.remove('busy'); btn.disabled = false;
};

/* ---- cắt nhanh trên máy ---- */
function lcRenderSegs() {
  $('#lcSegs').innerHTML = LC.segs.map((g, i) => `<div class="titem tseg" data-i="${i}"><span class="n">${i + 1}</span>
    <label>Từ<input type="text" data-k="a" value="${fmt(g.a)}"></label><label>Đến<input type="text" data-k="b" value="${fmt(g.b)}"></label>
    <div class="bt"><button class="btn mini" type="button" data-sa>Đầu = vị trí hiện tại</button><button class="btn mini" type="button" data-sb>Cuối = vị trí hiện tại</button><button class="btn mini danger" type="button" data-rm>×</button></div></div>`).join('');
}
$('#lcCutFile').onchange = e => {
  const f = e.target.files[0]; if (!f) return;
  LC.cutFile = f; const v = $('#lcVideo'); if (v.dataset.url) URL.revokeObjectURL(v.dataset.url);
  v.dataset.url = URL.createObjectURL(f); v.src = v.dataset.url;
  $('#lcCutBody').hidden = false; $('#lcCutName').placeholder = lcBase(f.name) + '_cat';
  $('#lcCutInfo').textContent = `${f.name} · ${mb(f.size)}`;
  v.onloadedmetadata = () => { const d = v.duration || 0; LC.segs = [{ a: 0, b: +Math.min(d || 10, 10).toFixed(2) }]; $('#lcCutInfo').textContent = `${f.name} · ${mb(f.size)} · ${dur(d)}`; lcRenderSegs(); };
  v.onerror = () => { $('#lcCutInfo').textContent = `${f.name} · ${mb(f.size)} · (trình duyệt không xem trước được định dạng này, bạn vẫn có thể gõ thời gian tay)`; LC.segs = [{ a: 0, b: 10 }]; lcRenderSegs(); };
};
$('#lcAdd').onclick = () => { const t = +($('#lcVideo').currentTime || 0).toFixed(2); LC.segs.push({ a: t, b: +(t + 5).toFixed(2) }); lcRenderSegs(); };
$('#lcSegs').addEventListener('click', e => {
  const it = e.target.closest('.titem'); if (!it) return; const g = LC.segs[+it.dataset.i], t = +($('#lcVideo').currentTime || 0).toFixed(2);
  if (e.target.closest('[data-rm]')) LC.segs.splice(+it.dataset.i, 1); else if (e.target.closest('[data-sa]')) g.a = t; else if (e.target.closest('[data-sb]')) g.b = t; else return;
  lcRenderSegs();
});
$('#lcSegs').addEventListener('change', e => { const it = e.target.closest('.titem'), k = e.target.dataset.k; if (!it || !k) return; const g = LC.segs[+it.dataset.i]; g[k] = parseT(e.target.value); e.target.value = fmt(g[k]); });
$('#lcCutRun').onclick = async () => {
  const f = LC.cutFile; if (!f) return toast('Hãy chọn video trước.', true);
  const bad = LC.segs.findIndex(g => !(g.b > g.a)); if (!LC.segs.length || bad >= 0) return toast(bad >= 0 ? `Đoạn ${bad + 1}: điểm cuối phải sau điểm đầu.` : 'Chưa có đoạn nào để cắt.', true);
  const d = $('#lcVideo').duration || 0, keep = LC.segs.reduce((a, g) => a + (g.b - g.a), 0);
  if (d > 0 && f.size * (keep / d) > 700 * 1048576 && !confirm(`Các đoạn cắt ước tính khoảng ${mb(f.size * keep / d)}. Trình duyệt có thể hết bộ nhớ và tắt tab. Vẫn tiếp tục?`)) return;
  const ext = ['.mp4', '.mov', '.m4v'].includes(lcExt(f.name)) ? '.mp4' : '.mkv', mime = ext === '.mp4' ? 'video/mp4' : 'video/x-matroska', base = $('#lcCutName').value.trim().replace(/\.[^.]+$/, '') || lcBase(f.name) + '_cat';
  const join = $('#lcJoin').checked && LC.segs.length > 1, total = keep;
  let done = 0;
  const outs = await lcWith(f, $('#lcCutRun'), 'Đang cắt (không mã hoá lại)', async (ff, inPath) => {
    const files = [];
    for (let i = 0; i < LC.segs.length; i++) {
      const g = LC.segs[i], out = join ? `s${i}${ext}` : `o${i}${ext}`;
      await lcExec(ff, ['-ss', g.a.toFixed(3), '-i', inPath, '-t', (g.b - g.a).toFixed(3), '-c', 'copy', '-avoid_negative_ts', 'make_zero', out]);
      done += g.b - g.a; lcProg(0.05 + 0.9 * done / total, `Đã cắt đoạn ${i + 1}/${LC.segs.length}`);
      files.push(out);
    }
    if (join) {
      await ff.writeFile('list.txt', files.map(x => `file '${x}'`).join('\n') + '\n');
      await lcExec(ff, ['-f', 'concat', '-safe', '0', '-i', 'list.txt', '-c', 'copy', 'joined' + ext]);
      for (const x of files) await ff.deleteFile(x); await ff.deleteFile('list.txt');
      return [await lcRead(ff, 'joined' + ext, base + ext, mime)];
    }
    const res = []; for (let i = 0; i < files.length; i++) res.push(await lcRead(ff, files[i], base + (files.length > 1 ? '_' + (i + 1) : '') + ext, mime));
    return res;
  });
  if (outs) { outs.forEach(lcAddOut); toast(`Xong! Đã cắt ${outs.length} file. Bấm "Tải về" ngay (kết quả chỉ nằm trong trang này).`); }
};

/* ---- tách MP3 trên máy ---- */
$('#lcMp3File').onchange = e => {
  const f = e.target.files[0]; if (!f) return; LC.mpFile = f; $('#lcMp3Body').hidden = false;
  $('#lcMp3Info').textContent = `${f.name} · ${mb(f.size)}`; $('#lcMpName').placeholder = lcBase(f.name);
};
$('#lcMpRun').onclick = async () => {
  const f = LC.mpFile; if (!f) return toast('Hãy chọn video trước.', true);
  const a = $('#lcMpA').value.trim() ? parseT($('#lcMpA').value) : 0, b = $('#lcMpB').value.trim() ? parseT($('#lcMpB').value) : 0;
  if (b && b <= a) return toast('Điểm cuối phải sau điểm đầu.', true);
  const base = $('#lcMpName').value.trim().replace(/\.[^.]+$/, '') || lcBase(f.name);
  const outs = await lcWith(f, $('#lcMpRun'), 'Đang tách MP3', async (ff, inPath) => {
    await lcExec(ff, [...(a > 0 ? ['-ss', a.toFixed(3)] : []), '-i', inPath, ...(b ? ['-t', (b - a).toFixed(3)] : []), '-vn', '-c:a', 'libmp3lame', '-b:a', $('#lcMpBr').value + 'k', '-id3v2_version', '3', 'a.mp3']);
    return [await lcRead(ff, 'a.mp3', base + '.mp3', 'audio/mpeg')];
  });
  if (outs) { outs.forEach(lcAddOut); toast('Xong! Đã tách MP3. Bấm "Tải về" ngay (kết quả chỉ nằm trong trang này).'); }
};


init();
