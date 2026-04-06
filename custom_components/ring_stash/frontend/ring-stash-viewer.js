/**
 * Ring Stash Clip Viewer — HA sidebar panel
 *
 * Performance design:
 *  - Server paginates clips (PAGE_SIZE per request); the client accumulates
 *    pages as the user scrolls via an IntersectionObserver sentinel.
 *  - Thumbnails are extracted lazily — only when a card enters the viewport
 *    (another IntersectionObserver). Extracted frames are cached in IndexedDB
 *    so re-visits are instant.
 *  - Kind and camera filters are client-side (fast, no re-fetch).
 *  - Search and date range filters are server-side (reset the accumulated list).
 *
 * The entire module is wrapped in an IIFE so const declarations don't pollute
 * the global scope and the script can safely be re-evaluated when HA re-mounts
 * the panel (e.g. navigation away and back).
 */

(() => {

const MEDIA_BASE = "/ring_stash_media";
const PAGE_SIZE  = 48;

const KIND_COLOR = { Doorbell: "#7c8cf8", Motion: "#f8c87c", Live: "#8cf87c" };
const KIND_ICON  = { Doorbell: "🔔", Motion: "👁", Live: "📹" };

function _esc(s) {
  return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function _formatBytes(bytes) {
  if (bytes >= 1_073_741_824) return (bytes / 1_073_741_824).toFixed(1) + " GB";
  if (bytes >= 1_048_576)     return (bytes / 1_048_576).toFixed(1) + " MB";
  if (bytes >= 1024)          return (bytes / 1024).toFixed(0) + " KB";
  return bytes + " B";
}

/* ── Styles ──────────────────────────────────────────────────────────────── */
const CSS = `
  :host {
    display: flex; flex-direction: column; height: 100%;
    background: var(--primary-background-color, #0f1117);
    color: var(--primary-text-color, #e2e4f0);
    font-family: var(--paper-font-body1_-_font-family, sans-serif);
    overflow: hidden; box-sizing: border-box;
  }
  .toolbar {
    display: flex; align-items: center; gap: 10px;
    padding: 12px 20px; flex-shrink: 0; flex-wrap: wrap;
    background: var(--card-background-color, #1e2130);
    border-bottom: 1px solid rgba(255,255,255,0.07);
  }
  .back-btn {
    background: none; border: none; color: var(--primary-text-color, #e2e4f0);
    font-size: 1.4rem; cursor: pointer; padding: 0 4px; line-height: 1;
    display: flex; align-items: center; flex-shrink: 0;
    opacity: 0.7; transition: opacity 0.15s;
  }
  .back-btn:hover { opacity: 1; }
  .toolbar-title { font-size: 1.05rem; font-weight: 700; flex: 1; min-width: 120px; }
  .pill-count {
    background: var(--primary-color, #7c8cf8); color: #fff;
    border-radius: 20px; padding: 2px 10px; font-size: 0.75rem; font-weight: 700;
  }
  .pill-storage {
    background: rgba(255,255,255,0.08); color: var(--secondary-text-color, #888);
    border-radius: 20px; padding: 2px 10px; font-size: 0.75rem; font-weight: 600;
  }
  .filter-wrap { display: flex; gap: 8px; flex-wrap: wrap; }
  .filter-btn {
    background: var(--secondary-background-color, #181b24);
    color: var(--primary-text-color, #e2e4f0);
    border: 1px solid rgba(255,255,255,0.1); border-radius: 20px;
    padding: 5px 13px; font-size: 0.78rem; cursor: pointer; transition: all 0.15s;
  }
  .filter-btn.active {
    background: var(--primary-color, #7c8cf8);
    border-color: var(--primary-color, #7c8cf8); color: #fff;
  }
  .filter-btn:hover:not(.active) { border-color: rgba(255,255,255,0.3); }
  select.filter-select {
    background: var(--secondary-background-color, #181b24);
    color: var(--primary-text-color, #e2e4f0);
    border: 1px solid rgba(255,255,255,0.1); border-radius: 20px;
    padding: 5px 13px; font-size: 0.78rem; cursor: pointer; outline: none;
  }
  .search-input {
    flex: 1 1 240px; min-width: min(240px, 100%); max-width: 420px;
    background: var(--secondary-background-color, #181b24);
    color: var(--primary-text-color, #e2e4f0);
    border: 1px solid rgba(255,255,255,0.1); border-radius: 20px;
    padding: 6px 14px; font-size: 0.78rem; outline: none;
  }
  .search-input::placeholder { color: var(--secondary-text-color, #888); }
  .search-input:focus { border-color: rgba(124,140,248,0.7); }
  .refresh-btn {
    background: none; border: 1px solid rgba(255,255,255,0.12); border-radius: 50%;
    color: var(--secondary-text-color, #888); width: 30px; height: 30px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center; font-size: 1rem;
    cursor: pointer; transition: border-color 0.15s, color 0.15s; line-height: 1;
  }
  .refresh-btn:hover { border-color: rgba(255,255,255,0.3); color: var(--primary-text-color, #e2e4f0); }
  .refresh-btn.spinning { animation: spin 0.7s linear infinite; }
  /* Date range row — flex-basis:100% pushes it onto its own line */
  .date-range {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    flex-basis: 100%; padding-top: 2px;
  }
  .date-lbl { font-size: 0.75rem; color: var(--secondary-text-color, #888); }
  .date-input {
    background: var(--secondary-background-color, #181b24);
    color: var(--primary-text-color, #e2e4f0);
    border: 1px solid rgba(255,255,255,0.1); border-radius: 8px;
    padding: 4px 8px; font-size: 0.78rem; outline: none; cursor: pointer;
  }
  .date-input::-webkit-calendar-picker-indicator { filter: invert(0.7); cursor: pointer; }
  .clear-date {
    background: none; border: 1px solid rgba(255,255,255,0.15);
    color: var(--secondary-text-color, #888); border-radius: 8px;
    padding: 4px 10px; font-size: 0.75rem; cursor: pointer; transition: all 0.15s;
  }
  .clear-date:hover { border-color: rgba(255,255,255,0.3); color: var(--primary-text-color, #e2e4f0); }
  .date-active { border-color: var(--primary-color, #7c8cf8) !important; }
  /* Grid */
  .grid-wrap { flex: 1; overflow-y: auto; padding: 18px 20px; box-sizing: border-box; }
  .grid { display: flex; flex-direction: column; gap: 28px; }
  .date-header {
    display: flex; align-items: center; justify-content: space-between;
    padding-bottom: 10px; margin-bottom: 14px;
    border-bottom: 1px solid rgba(255,255,255,0.08);
  }
  .date-label { font-size: 0.95rem; font-weight: 700; letter-spacing: 0.01em; }
  .date-count {
    font-size: 0.72rem; font-weight: 600; padding: 2px 10px; border-radius: 20px;
    background: rgba(255,255,255,0.07); color: var(--secondary-text-color, #888);
  }
  .date-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }
  /* Clip cards */
  .clip-card {
    background: var(--card-background-color, #1e2130); border-radius: 12px;
    overflow: hidden; cursor: pointer; display: flex; flex-direction: column;
    border: 1px solid rgba(255,255,255,0.06);
    transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s;
  }
  .clip-card:hover {
    transform: translateY(-3px); box-shadow: 0 8px 24px rgba(0,0,0,0.45);
    border-color: var(--primary-color, #7c8cf8);
  }
  .thumb-wrap { position: relative; width: 100%; aspect-ratio: 16/9; background: #0a0b0f; overflow: hidden; }
  .thumb-canvas { width: 100%; height: 100%; display: block; object-fit: cover; }
  .thumb-placeholder {
    width: 100%; height: 100%; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 6px;
    color: rgba(255,255,255,0.2); font-size: 2.2rem;
  }
  .thumb-placeholder span { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.1em; }
  .thumb-loading {
    position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    background: rgba(0,0,0,0.2);
  }
  .spinner {
    width: 24px; height: 24px; border: 2px solid rgba(255,255,255,0.15);
    border-top-color: rgba(255,255,255,0.7); border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .play-btn {
    position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    opacity: 0; transition: opacity 0.15s; background: rgba(0,0,0,0.3);
  }
  .play-btn svg { width: 52px; height: 52px; filter: drop-shadow(0 2px 8px rgba(0,0,0,0.7)); }
  .clip-card:hover .play-btn { opacity: 1; }
  .kind-badge {
    position: absolute; top: 8px; left: 8px; padding: 3px 9px; border-radius: 20px;
    font-size: 0.68rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #111;
  }
  .lock-btn {
    position: absolute; top: 8px; right: 8px; z-index: 2;
    background: rgba(0,0,0,0.55); border: 1px solid rgba(255,255,255,0.2);
    border-radius: 50%; width: 26px; height: 26px; font-size: 0.75rem;
    display: flex; align-items: center; justify-content: center;
    cursor: pointer; opacity: 0; transition: opacity 0.15s, background 0.15s;
    color: #fff; line-height: 1;
  }
  .clip-card:hover .lock-btn { opacity: 1; }
  .lock-btn.locked {
    opacity: 1; background: rgba(124,140,248,0.75);
    border-color: var(--primary-color, #7c8cf8);
  }
  .lock-btn.locked:hover { background: rgba(124,140,248,0.95); }
  .clip-body { padding: 11px 13px 13px; display: flex; flex-direction: column; gap: 3px; }
  .clip-cam { font-size: 0.82rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .clip-ai {
    font-size: 0.7rem; color: var(--secondary-text-color, #7d8390); font-style: italic;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .clip-date { font-size: 0.75rem; color: var(--secondary-text-color, #888); }
  .clip-label {
    font-size: 0.74rem; color: var(--primary-text-color, #d0d3e0); cursor: text;
    min-height: 1.15em; border-radius: 4px; padding: 1px 3px; margin: 0 -3px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .clip-label:hover { background: rgba(255,255,255,0.06); }
  .label-placeholder { color: rgba(255,255,255,0.2); font-style: italic; font-size: 0.7rem; }
  .label-input {
    width: 100%; box-sizing: border-box;
    background: rgba(0,0,0,0.45); border: 1px solid var(--primary-color, #7c8cf8);
    border-radius: 4px; color: var(--primary-text-color, #e2e4f0);
    font-size: 0.74rem; padding: 2px 5px; outline: none; font-family: inherit;
  }
  .clip-size { font-size: 0.7rem; color: var(--secondary-text-color, #666); margin-top: 1px; }
  /* Modal label row */
  .modal-label-row { display: flex; align-items: center; gap: 8px; margin-top: 6px; justify-content: center; flex-wrap: wrap; }
  .modal-ai { color: rgba(255,255,255,0.55); font-size: 0.8rem; font-style: italic; }
  .modal-label-text { color: rgba(255,255,255,0.8); font-size: 0.82rem; }
  .modal-label-empty { color: rgba(255,255,255,0.25); font-style: italic; font-size: 0.8rem; cursor: pointer; }
  .modal-label-text { cursor: pointer; border-bottom: 1px dashed rgba(255,255,255,0.2); }
  .modal-label-text:hover, .modal-label-empty:hover { color: rgba(255,255,255,0.7); }
  .modal-label-input {
    background: rgba(0,0,0,0.5); border: 1px solid var(--primary-color, #7c8cf8);
    border-radius: 6px; color: #fff; font-size: 0.82rem; padding: 4px 10px; outline: none;
    width: min(320px, 80vw); font-family: inherit;
  }
  /* Deleted clip cards */
  .deleted-card {
    background: var(--card-background-color, #1e2130); border-radius: 12px;
    padding: 14px 16px; display: flex; align-items: center; gap: 12px;
    border: 1px solid rgba(255,80,80,0.2);
  }
  .deleted-icon { font-size: 1.6rem; flex-shrink: 0; opacity: 0.5; }
  .deleted-info { flex: 1; min-width: 0; }
  .deleted-name { font-size: 0.82rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .deleted-meta { font-size: 0.72rem; color: var(--secondary-text-color, #888); margin-top: 2px; }
  .restore-btn {
    flex-shrink: 0; background: rgba(124,140,248,0.15);
    border: 1px solid var(--primary-color, #7c8cf8); color: var(--primary-color, #7c8cf8);
    padding: 6px 14px; border-radius: 20px; font-size: 0.78rem; cursor: pointer;
    transition: background 0.15s; white-space: nowrap;
  }
  .restore-btn:hover { background: rgba(124,140,248,0.3); }
  /* Modal delete button */
  .modal-delete-btn {
    background: none; border: 1px solid rgba(255,80,80,0.4);
    color: rgba(255,100,100,0.8); padding: 6px 12px; border-radius: 20px;
    font-size: 0.78rem; cursor: pointer; transition: all 0.15s; white-space: nowrap; flex-shrink: 0;
  }
  .modal-delete-btn:hover { background: rgba(255,80,80,0.15); border-color: rgba(255,80,80,0.7); color: #ff6464; }
  /* Footer / infinite scroll sentinel */
  .load-footer {
    padding: 20px; text-align: center;
    font-size: 0.8rem; color: var(--secondary-text-color, #666);
  }
  .sentinel { height: 1px; }
  /* State messages */
  .state-msg {
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; padding: 80px 20px; gap: 16px; opacity: 0.5;
    font-size: 0.95rem; text-align: center;
  }
  .state-msg .icon { font-size: 3rem; }
  /* Modal */
  .modal-bg {
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.92);
    z-index: 9999; flex-direction: column; align-items: center;
    justify-content: center; padding: 20px; box-sizing: border-box;
  }
  .modal-bg.open { display: flex; }
  .modal-close {
    position: fixed; top: 14px; right: 18px; background: rgba(255,255,255,0.1);
    border: none; color: #fff; width: 36px; height: 36px; border-radius: 50%;
    font-size: 1.2rem; cursor: pointer; display: flex; align-items: center;
    justify-content: center; transition: background 0.15s;
  }
  .modal-close:hover { background: rgba(255,255,255,0.22); }
  .modal-video {
    width: 100%; max-width: min(92vw, 1100px); max-height: 72vh;
    border-radius: 10px; outline: none; background: #000;
    box-shadow: 0 20px 60px rgba(0,0,0,0.7);
  }
  .modal-detail {
    margin-top: 12px; width: 100%; max-width: min(92vw, 1100px);
    background: rgba(255,255,255,0.05); border-radius: 10px;
    padding: 12px 16px; box-sizing: border-box;
    display: flex; flex-direction: column; gap: 10px;
  }
  /* Top bar: kind badge left, action buttons right */
  .modal-detail-header {
    display: flex; align-items: center; justify-content: space-between; gap: 8px;
  }
  .modal-kind { padding: 3px 12px; border-radius: 20px; font-size: 0.78rem; font-weight: 700; color: #111; flex-shrink: 0; }
  .modal-actions { display: flex; gap: 8px; flex-shrink: 0; }
  /* 2-column metadata grid */
  .modal-meta-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 8px 20px;
  }
  .modal-meta-item { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .modal-meta-label {
    font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.08em;
    color: rgba(255,255,255,0.3);
  }
  .modal-meta-value {
    font-size: 0.84rem; color: rgba(255,255,255,0.85); font-weight: 500;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .modal-lock-btn {
    background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.18);
    color: #fff; padding: 6px 14px; border-radius: 20px; cursor: pointer;
    font-size: 0.8rem; transition: background 0.15s, border-color 0.15s; white-space: nowrap;
  }
  .modal-lock-btn:hover { background: rgba(255,255,255,0.16); }
  .modal-lock-btn.locked {
    background: rgba(124,140,248,0.25); border-color: var(--primary-color, #7c8cf8);
    color: var(--primary-color, #7c8cf8);
  }
  .modal-lock-btn.locked:hover { background: rgba(124,140,248,0.4); }
  .modal-ai-text { font-size: 0.82rem; color: rgba(255,255,255,0.5); font-style: italic; }
  .modal-nav { display: flex; gap: 12px; margin-top: 14px; }
  .nav-btn {
    background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.12);
    color: #fff; padding: 8px 20px; border-radius: 8px; cursor: pointer;
    font-size: 0.85rem; transition: background 0.15s;
  }
  .nav-btn:hover:not(:disabled) { background: rgba(255,255,255,0.2); }
  .nav-btn:disabled { opacity: 0.25; cursor: default; }
`;

/* ── Thumbnail IndexedDB cache ───────────────────────────────────────────── */
const _THUMB_DB_NAME = "ring_stash_thumbs";
const _THUMB_STORE   = "thumbnails";

function _openThumbDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(_THUMB_DB_NAME, 1);
    req.onupgradeneeded = e => e.target.result.createObjectStore(_THUMB_STORE);
    req.onsuccess = e => resolve(e.target.result);
    req.onerror   = e => reject(e.target.error);
  });
}

async function _getCached(key) {
  try {
    const db = await _openThumbDB();
    return await new Promise((res, rej) => {
      const req = db.transaction(_THUMB_STORE).objectStore(_THUMB_STORE).get(key);
      req.onsuccess = e => { db.close(); res(e.target.result ?? null); };
      req.onerror   = e => { db.close(); rej(e.target.error); };
    });
  } catch { return null; }
}

async function _putCached(key, dataUrl) {
  try {
    const db = await _openThumbDB();
    await new Promise((res, rej) => {
      const tx  = db.transaction(_THUMB_STORE, "readwrite");
      const req = tx.objectStore(_THUMB_STORE).put(dataUrl, key);
      req.onsuccess = () => { db.close(); res(); };
      req.onerror   = e => { db.close(); rej(e.target.error); };
    });
  } catch { /* non-fatal */ }
}

async function _getAllThumbKeys() {
  try {
    const db = await _openThumbDB();
    return await new Promise((res, rej) => {
      const req = db.transaction(_THUMB_STORE).objectStore(_THUMB_STORE).getAllKeys();
      req.onsuccess = e => { db.close(); res(e.target.result ?? []); };
      req.onerror   = e => { db.close(); rej(e.target.error); };
    });
  } catch { return []; }
}

async function _deleteThumbKeys(keys) {
  if (!keys.length) return;
  try {
    const db    = await _openThumbDB();
    const tx    = db.transaction(_THUMB_STORE, "readwrite");
    const store = tx.objectStore(_THUMB_STORE);
    keys.forEach(k => store.delete(k));
    await new Promise((res, rej) => {
      tx.oncomplete = () => { db.close(); res(); };
      tx.onerror    = e  => { db.close(); rej(e.target.error); };
    });
  } catch { /* non-fatal */ }
}

/* ── Canvas thumbnail extraction ─────────────────────────────────────────── */
async function extractThumbnail(videoUrl, canvas, onDone) {
  const cached = await _getCached(videoUrl);
  if (cached) {
    const img = new Image();
    img.onload = () => {
      canvas.width  = img.naturalWidth;
      canvas.height = img.naturalHeight;
      canvas.getContext("2d").drawImage(img, 0, 0);
      onDone(true);
    };
    img.onerror = () => _extractFromVideo(videoUrl, canvas, onDone);
    img.src = cached;
    return;
  }
  _extractFromVideo(videoUrl, canvas, onDone);
}

function _extractFromVideo(videoUrl, canvas, onDone) {
  const vid = document.createElement("video");
  vid.muted = true;
  vid.preload = "metadata";
  vid.crossOrigin = "use-credentials";
  let done = false;

  const finish = (ok) => {
    if (done) return;
    done = true;
    vid.src = "";
    vid.load();
    onDone(ok);
  };

  vid.addEventListener("loadedmetadata", () => {
    vid.currentTime = Math.min(2, (vid.duration || 4) * 0.15);
  });

  vid.addEventListener("seeked", () => {
    try {
      const ctx = canvas.getContext("2d");
      canvas.width  = vid.videoWidth  || 640;
      canvas.height = vid.videoHeight || 360;
      ctx.drawImage(vid, 0, 0, canvas.width, canvas.height);
      const cx = Math.floor(canvas.width / 2), cy = Math.floor(canvas.height / 2);
      const px = ctx.getImageData(cx - 4, cy - 4, 8, 8).data;
      const hasContent = px.some((v, i) => i % 4 !== 3 && v > 8);
      if (hasContent) _putCached(videoUrl, canvas.toDataURL("image/jpeg", 0.7));
      finish(true);
    } catch {
      finish(false);
    }
  });

  vid.addEventListener("error", () => finish(false));
  setTimeout(() => finish(false), 8000);
  vid.src = videoUrl;
  vid.load();
}

/* ── Date grouping helpers ───────────────────────────────────────────────── */
function _localDateKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function _dateGroupLabel(key) {
  const todayKey     = _localDateKey(new Date());
  const yesterdayKey = _localDateKey(new Date(Date.now() - 86400000));
  if (key === todayKey)     return "Today";
  if (key === yesterdayKey) return "Yesterday";
  if (!key) return "Unknown date";
  const [y, m, d] = key.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    weekday: "long", day: "numeric", month: "long", year: "numeric",
  });
}

/* ── Component ───────────────────────────────────────────────────────────── */
class RingClipViewer extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    // Paginated clip state
    this._allClips = [];  // clips fetched from server (accumulates across pages)
    this._filtered = [];  // after kind/camera client-side filter
    this._total    = 0;   // total clips on server matching the active server-side filters
    this._offset   = 0;   // number of server clips fetched so far
    this._loading  = false;
    // Active filters
    this._fromDate       = "";
    this._toDate         = "";
    this._searchText     = "";
    this._filterKind     = "all";
    this._filterDoorbell = "all";
    this._filterLocked   = false;
    this._filterDeleted  = false;
    this._totalBytes     = 0;
    // Misc
    this._modalIdx      = -1;
    this._panel         = null;
    this._hass          = null;
    this._loaded        = false;
    this._cacheChecked  = false; // run stale-thumbnail purge once per session
    this._pollTimer     = null;  // setInterval handle for auto-refresh polling
    this._searchDebounce = null; // timeout handle for server-backed search input
    this._thumbObs      = null;  // IntersectionObserver for lazy thumbnails
    this._sentinelObs   = null;  // IntersectionObserver for infinite scroll
  }

  set panel(p) { this._panel = p; }

  set hass(h) {
    this._hass = h;
    if (!this._loaded) { this._loaded = true; this._loadClips(); }
  }

  connectedCallback() { this._render(); }

  disconnectedCallback() {
    this._stopPolling();
    if (this._searchDebounce) { clearTimeout(this._searchDebounce); this._searchDebounce = null; }
    if (this._thumbObs)    { this._thumbObs.disconnect();    this._thumbObs    = null; }
    if (this._sentinelObs) { this._sentinelObs.disconnect(); this._sentinelObs = null; }
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>${CSS}</style>
      <div class="toolbar">
        <button class="back-btn" id="back-btn" title="Go back">&#8592;</button>
        <div class="toolbar-title">📹 ${this._panel?.config?.panel_title ?? "Ring Stash"}</div>
        <button class="refresh-btn" id="refresh-btn" title="Refresh clips">↻</button>
        <span class="pill-count" id="count">–</span>
        <span class="pill-storage" id="storage">–</span>
        <input type="search" class="search-input" id="search-input" placeholder="Search AI descriptions, notes, cameras…">
        <div class="filter-wrap" id="kind-filters">
          <button class="filter-btn active" data-kind="all">All</button>
          <button class="filter-btn" data-kind="Doorbell">🔔 Doorbell</button>
          <button class="filter-btn" data-kind="Motion">👁 Motion</button>
          <button class="filter-btn" data-kind="Live">📹 Live</button>
          <button class="filter-btn" id="locked-filter-btn">🔒 Archived</button>
          <button class="filter-btn" id="deleted-filter-btn">🗑️ Deleted</button>
        </div>
        <select class="filter-select" id="cam-filter"><option value="all">All cameras</option></select>
        <div class="date-range">
          <span class="date-lbl">From</span>
          <input type="date" class="date-input" id="from-date">
          <span class="date-lbl">To</span>
          <input type="date" class="date-input" id="to-date">
          <button class="clear-date" id="clear-date">Clear dates</button>
        </div>
      </div>
      <div class="grid-wrap">
        <div class="grid" id="grid">
          <div class="state-msg"><div class="icon">⏳</div>Loading clips…</div>
        </div>
      </div>
      <div class="modal-bg" id="modal">
        <button class="modal-close" id="modal-close" title="Close (Esc)">✕</button>
        <video class="modal-video" id="modal-video" controls autoplay></video>
        <div class="modal-detail" id="modal-detail">
          <div class="modal-detail-header">
            <span class="modal-kind" id="modal-kind"></span>
            <div class="modal-actions">
              <button class="modal-lock-btn" id="modal-lock-btn">🔒 Archived</button>
              <button class="modal-delete-btn" id="modal-delete-btn">🗑️ Delete</button>
            </div>
          </div>
          <div class="modal-meta-grid">
            <div class="modal-meta-item">
              <span class="modal-meta-label">Camera</span>
              <span class="modal-meta-value" id="modal-camera"></span>
            </div>
            <div class="modal-meta-item">
              <span class="modal-meta-label">Date</span>
              <span class="modal-meta-value" id="modal-date"></span>
            </div>
            <div class="modal-meta-item">
              <span class="modal-meta-label">Time</span>
              <span class="modal-meta-value" id="modal-time"></span>
            </div>
            <div class="modal-meta-item">
              <span class="modal-meta-label">Size</span>
              <span class="modal-meta-value" id="modal-size"></span>
            </div>
          </div>
          <div id="modal-ai-row"></div>
          <div class="modal-label-row" id="modal-label-row"></div>
        </div>
        <div class="modal-nav">
          <button class="nav-btn" id="nav-prev">◀ Previous</button>
          <button class="nav-btn" id="nav-next">Next ▶</button>
        </div>
      </div>`;

    this.shadowRoot.getElementById("back-btn").onclick = () => history.back();
    this.shadowRoot.getElementById("refresh-btn").onclick = () => this._resetAndLoad();
    this.shadowRoot.getElementById("search-input").value = this._searchText;
    this.shadowRoot.getElementById("search-input").addEventListener("input", e => {
      const next = e.target.value.trim();
      if (next === this._searchText) return;
      this._searchText = next;
      this._scheduleSearch();
    });

    this.shadowRoot.getElementById("kind-filters").addEventListener("click", e => {
      const btn = e.target.closest(".filter-btn");
      if (!btn) return;
      if (btn.id === "locked-filter-btn") {
        this._filterLocked = !this._filterLocked;
        btn.classList.toggle("active", this._filterLocked);
        this._applyFilters();
        return;
      }
      if (btn.id === "deleted-filter-btn") {
        this._filterDeleted = !this._filterDeleted;
        btn.classList.toggle("active", this._filterDeleted);
        if (this._filterDeleted) this._loadDeletedClips();
        else this._renderGrid();
        return;
      }
      this.shadowRoot.querySelectorAll(".filter-btn:not(#locked-filter-btn)").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      this._filterKind = btn.dataset.kind;
      this._applyFilters();
    });

    this.shadowRoot.getElementById("modal-lock-btn").onclick = () => {
      const clip = this._filtered[this._modalIdx];
      if (!clip) return;
      // Find the lock btn on the card and delegate to existing toggle logic
      const cardBtn = Array.from(this.shadowRoot.querySelectorAll(".lock-btn"))
        .find(b => b.dataset.filename === clip.filename);
      const nowLocked = !clip.locked;
      // Update modal button immediately
      const modalBtn = this.shadowRoot.getElementById("modal-lock-btn");
      modalBtn.classList.toggle("locked", nowLocked);
      modalBtn.textContent = nowLocked ? "🔒 Archived" : "🔓 Not archived";
      // Update clip state and card
      clip.locked = nowLocked;
      if (cardBtn) {
        cardBtn.classList.toggle("locked", nowLocked);
        cardBtn.textContent = nowLocked ? "🔒" : "🔓";
        cardBtn.title = nowLocked ? "Unlock (allow auto-deletion)" : "Lock (preserve from auto-deletion)";
      }
      this._hass.callApi("POST", "ring_stash/lock", { filename: clip.filename, locked: nowLocked })
        .catch(() => {
          // Revert on failure
          clip.locked = !nowLocked;
          modalBtn.classList.toggle("locked", !nowLocked);
          modalBtn.textContent = !nowLocked ? "🔒 Archived" : "🔓 Not archived";
          if (cardBtn) {
            cardBtn.classList.toggle("locked", !nowLocked);
            cardBtn.textContent = !nowLocked ? "🔒" : "🔓";
          }
        });
    };

    this.shadowRoot.getElementById("modal-delete-btn").onclick = () => {
      const clip = this._filtered[this._modalIdx];
      if (!clip) return;
      if (!confirm(`Delete "${clip.filename}" permanently?\n\nThe clip will be removed from disk. You can restore it later if Ring still has a copy.`)) return;
      const btn = this.shadowRoot.getElementById("modal-delete-btn");
      btn.disabled = true;
      btn.textContent = "Deleting…";
      this._hass.callApi("POST", "ring_stash/delete", { filename: clip.filename })
        .then(() => {
          // Remove from in-memory list and close modal
          this._allClips = this._allClips.filter(c => c.filename !== clip.filename);
          this._total = Math.max(0, this._total - 1);
          this._closeModal();
          this._applyFilters();
        })
        .catch(() => {
          btn.disabled = false;
          btn.textContent = "🗑️ Delete";
          alert("Failed to delete clip. Please try again.");
        });
    };

    this.shadowRoot.getElementById("cam-filter").onchange = e => {
      this._filterDoorbell = e.target.value;
      this._applyFilters();
    };

    this.shadowRoot.getElementById("from-date").onchange = e => {
      this._fromDate = e.target.value;
      e.target.classList.toggle("date-active", !!this._fromDate);
      this._resetAndLoad();
    };

    this.shadowRoot.getElementById("to-date").onchange = e => {
      this._toDate = e.target.value;
      e.target.classList.toggle("date-active", !!this._toDate);
      this._resetAndLoad();
    };

    this.shadowRoot.getElementById("clear-date").onclick = () => {
      this._fromDate = "";
      this._toDate   = "";
      const f = this.shadowRoot.getElementById("from-date");
      const t = this.shadowRoot.getElementById("to-date");
      f.value = ""; f.classList.remove("date-active");
      t.value = ""; t.classList.remove("date-active");
      this._resetAndLoad();
    };

    this.shadowRoot.getElementById("modal-close").onclick = () => this._closeModal();
    this.shadowRoot.getElementById("modal").onclick = e => {
      if (e.target.id === "modal") this._closeModal();
    };
    this.shadowRoot.getElementById("nav-prev").onclick = () => this._navigate(-1);
    this.shadowRoot.getElementById("nav-next").onclick  = () => this._navigate(1);
    document.addEventListener("keydown", e => {
      if (!this.shadowRoot.getElementById("modal")?.classList.contains("open")) return;
      if (e.key === "Escape")     this._closeModal();
      if (e.key === "ArrowLeft")  this._navigate(-1);
      if (e.key === "ArrowRight") this._navigate(1);
    });
  }

  // ── Data loading ────────────────────────────────────────────────────────

  _scheduleSearch() {
    if (this._searchDebounce) clearTimeout(this._searchDebounce);
    this._searchDebounce = setTimeout(() => {
      this._searchDebounce = null;
      this._resetAndLoad();
    }, 200);
  }

  _resetAndLoad() {
    this._allClips    = [];
    this._filtered    = [];
    this._offset      = 0;
    this._total       = 0;
    this._cacheChecked = false;
    this._stopPolling();
    if (this._thumbObs)    { this._thumbObs.disconnect();    this._thumbObs    = null; }
    if (this._sentinelObs) { this._sentinelObs.disconnect(); this._sentinelObs = null; }
    const grid = this.shadowRoot.getElementById("grid");
    if (grid) grid.innerHTML = `<div class="state-msg"><div class="icon">⏳</div>Loading clips…</div>`;
    this._loadClips();
  }

  async _loadClips() {
    if (this._loading) return;
    this._loading = true;
    this._updateRefreshBtn();

    const p = new URLSearchParams({ limit: PAGE_SIZE, offset: this._offset });
    if (this._fromDate) p.set("from_date", this._fromDate);
    if (this._toDate)   p.set("to_date",   this._toDate);
    if (this._searchText) p.set("search", this._searchText);

    try {
      const data = await this._hass.callApi("GET", `ring_stash/clips?${p}`);
      this._total = data.total ?? 0;
      if (data.total_bytes != null) {
        this._totalBytes = data.total_bytes;
        const storageEl = this.shadowRoot.getElementById("storage");
        if (storageEl) storageEl.textContent = _formatBytes(data.total_bytes);
      }
      this._allClips.push(...(data.clips ?? []));
      this._offset = this._allClips.length;

      // Update doorbell dropdown (accumulate unique cameras as pages load)
      const cams = [...new Set(this._allClips.map(c => c.doorbell))].sort();
      const sel  = this.shadowRoot.getElementById("cam-filter");
      if (sel) {
        sel.innerHTML = `<option value="all">All cameras</option>` +
          cams.map(c => `<option value="${c}" ${c === this._filterDoorbell ? "selected" : ""}>${c}</option>`).join("");
      }

      this._applyFilters();

      // Start polling for new clips after first page loads
      if (!this._pollTimer) this._startPolling();

      // Purge stale thumbnail cache once per reset, after first page loads
      if (!this._cacheChecked) {
        this._cacheChecked = true;
        this._purgeStaleCache(); // fire and forget — non-blocking
      }
    } catch (err) {
      if (!this._allClips.length) {
        const grid = this.shadowRoot.getElementById("grid");
        if (grid) grid.innerHTML =
          `<div class="state-msg"><div class="icon">⚠️</div>Failed to load clips:<br>${err.message}</div>`;
      }
    } finally {
      this._loading = false;
      this._updateRefreshBtn();
    }
  }

  _updateRefreshBtn() {
    this.shadowRoot?.getElementById("refresh-btn")?.classList.toggle("spinning", this._loading);
  }

  // ── Auto-refresh polling ─────────────────────────────────────────────────

  _startPolling() {
    this._stopPolling();
    this._pollTimer = setInterval(() => this._checkForUpdates(), 60_000);
  }

  _stopPolling() {
    if (this._pollTimer) { clearInterval(this._pollTimer); this._pollTimer = null; }
  }

  async _checkForUpdates() {
    if (this._loading) return;
    try {
      // Fetch only the first clip with the same date filter — cheap single-item query
      const p = new URLSearchParams({ limit: 1, offset: 0 });
      if (this._fromDate) p.set("from_date", this._fromDate);
      if (this._toDate)   p.set("to_date",   this._toDate);
      if (this._searchText) p.set("search", this._searchText);
      const data = await this._hass.callApi("GET", `ring_stash/clips?${p}`);
      const totalChanged   = data.total !== this._total;
      const newestChanged  = data.clips?.[0]?.filename !== this._allClips[0]?.filename;
      if (totalChanged || newestChanged) this._resetAndLoad();
    } catch { /* non-fatal — will retry next interval */ }
  }

  async _purgeStaleCache() {
    // Fetch the full list of filenames currently on disk (cheap — no stat calls)
    // then remove any IndexedDB thumbnail entries whose file no longer exists.
    try {
      const data       = await this._hass.callApi("GET", "ring_stash/filenames");
      const validKeys  = new Set(
        (data.filenames ?? []).map(f => `${MEDIA_BASE}/${encodeURIComponent(f)}`)
      );
      const cachedKeys = await _getAllThumbKeys();
      const stale      = cachedKeys.filter(k => !validKeys.has(k));
      if (stale.length) {
        await _deleteThumbKeys(stale);
        console.debug(`[Ring Stash] Purged ${stale.length} stale thumbnail(s) from cache`);
      }
    } catch {
      // Non-fatal — stale entries just occupy a little storage until next purge
    }
  }

  // ── Filtering & rendering ───────────────────────────────────────────────

  async _loadDeletedClips() {
    const grid = this.shadowRoot.getElementById("grid");
    if (grid) grid.innerHTML = `<div class="state-msg"><div class="icon">⏳</div>Loading deleted clips…</div>`;
    try {
      const data = await this._hass.callApi("GET", "ring_stash/deleted");
      const clips = data.clips ?? [];
      if (!grid) return;
      if (!clips.length) {
        grid.innerHTML = `<div class="state-msg"><div class="icon">🗑️</div>No deleted clips.</div>`;
        return;
      }
      grid.innerHTML = `<div class="grid">${clips.map(clip => {
        const icon = KIND_ICON[clip.kind] || "🎬";
        const date = clip.recorded_at ? new Date(clip.recorded_at).toLocaleString() : "Unknown date";
        return `
          <div class="deleted-card">
            <div class="deleted-icon">${icon}</div>
            <div class="deleted-info">
              <div class="deleted-name">${_esc(clip.filename)}</div>
              <div class="deleted-meta">${_esc(clip.doorbell)} · ${date}</div>
            </div>
            <button class="restore-btn" data-filename="${_esc(clip.filename)}">↩️ Restore</button>
          </div>`;
      }).join("")}</div>`;
      grid.querySelectorAll(".restore-btn").forEach(btn => {
        btn.onclick = async () => {
          btn.disabled = true;
          btn.textContent = "Requesting…";
          try {
            await this._hass.callApi("POST", "ring_stash/restore", { filename: btn.dataset.filename });
            // Show inline feedback on the card
            const card = btn.closest(".deleted-card");
            if (card) {
              card.style.opacity = "0.5";
              btn.textContent = "⏳ Re-downloading from Ring…";
            }
            // Switch back to normal view after ~10 s — enough time for the 30-second
            // retry cycle to pick up the clip and download it
            setTimeout(() => {
              this._filterDeleted = false;
              const delBtn = this.shadowRoot.getElementById("deleted-filter-btn");
              if (delBtn) delBtn.classList.remove("active");
              this._resetAndLoad();
            }, 10_000);
          } catch {
            btn.disabled = false;
            btn.textContent = "↩️ Restore";
          }
        };
      });
    } catch (err) {
      if (grid) grid.innerHTML = `<div class="state-msg"><div class="icon">⚠️</div>Failed to load: ${err.message}</div>`;
    }
  }

  _applyFilters() {
    this._filtered = this._allClips.filter(c =>
      (this._filterKind     === "all" || c.kind     === this._filterKind) &&
      (this._filterDoorbell === "all" || c.doorbell === this._filterDoorbell) &&
      (!this._filterLocked  || c.locked)
    );
    this._renderGrid();
  }

  _renderGrid() {
    const grid = this.shadowRoot.getElementById("grid");
    if (!grid) return;

    // Update count pill
    const countEl = this.shadowRoot.getElementById("count");
    if (countEl) {
      const n = this._filtered.length;
      countEl.textContent = (this._offset < this._total)
        ? `${n} of ~${this._total}`
        : `${n} clip${n !== 1 ? "s" : ""}`;
    }

    if (!this._filtered.length) {
      grid.innerHTML = `<div class="state-msg"><div class="icon">🎬</div>No clips match this filter.</div>`;
      return;
    }

    // Group by local calendar date, keeping flat indices into this._filtered
    const groups = new Map();
    this._filtered.forEach((clip, idx) => {
      const key = clip.recorded_at ? _localDateKey(new Date(clip.recorded_at)) : "";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push({ clip, idx });
    });
    const sortedKeys = [...groups.keys()].sort((a, b) => b.localeCompare(a));

    grid.innerHTML = sortedKeys.map(key => {
      const items = groups.get(key);
      const cards = items.map(({ clip, idx }) => {
        const color   = KIND_COLOR[clip.kind] || "#888";
        const icon    = KIND_ICON[clip.kind]  || "🎥";
        const timeStr = clip.recorded_at
          ? new Date(clip.recorded_at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
          : "";
        return `
          <div class="clip-card" data-idx="${idx}">
            <div class="thumb-wrap">
              <canvas class="thumb-canvas" data-src="${MEDIA_BASE}/${encodeURIComponent(clip.filename)}"></canvas>
              <div class="thumb-loading"><div class="spinner"></div></div>
              <span class="kind-badge" style="background:${color}">${icon} ${clip.kind}</span>
              <button class="lock-btn ${clip.locked ? "locked" : ""}" data-filename="${clip.filename}"
                title="${clip.locked ? "Unlock (allow auto-deletion)" : "Lock (preserve from auto-deletion)"}">
                ${clip.locked ? "🔒" : "🔓"}
              </button>
              <div class="play-btn">
                <svg viewBox="0 0 80 80" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <circle cx="40" cy="40" r="38" fill="rgba(0,0,0,0.5)" stroke="rgba(255,255,255,0.8)" stroke-width="2"/>
                  <polygon points="32,24 60,40 32,56" fill="white"/>
                </svg>
              </div>
            </div>
            <div class="clip-body">
              <div class="clip-cam">${_esc(clip.doorbell)}</div>
              ${clip.ai_description ? `<div class="clip-ai" title="${_esc(clip.ai_description)}">${_esc(clip.ai_description)}</div>` : ""}
              <div class="clip-date">${timeStr}</div>
              <div class="clip-label" data-filename="${_esc(clip.filename)}">${
                clip.label
                  ? `<span title="${_esc(clip.label)}">${_esc(clip.label)}</span>`
                  : `<span class="label-placeholder">＋ note</span>`
              }</div>
              <div class="clip-size">${clip.size_kb} KB</div>
            </div>
          </div>`;
      }).join("");

      return `
        <div class="date-group">
          <div class="date-header">
            <span class="date-label">${_dateGroupLabel(key)}</span>
            <span class="date-count">${items.length} clip${items.length !== 1 ? "s" : ""}</span>
          </div>
          <div class="date-grid">${cards}</div>
        </div>`;
    }).join("");

    // Infinite scroll — observe a sentinel below the last group
    if (this._sentinelObs) { this._sentinelObs.disconnect(); this._sentinelObs = null; }
    if (this._offset < this._total) {
      const sentinel = document.createElement("div");
      sentinel.className = "sentinel";
      grid.appendChild(sentinel);
      this._sentinelObs = new IntersectionObserver(entries => {
        if (entries[0].isIntersecting && !this._loading) this._loadClips();
      }, { rootMargin: "400px" });
      requestAnimationFrame(() => {
        if (sentinel.isConnected) this._sentinelObs.observe(sentinel);
      });
    } else if (this._total > PAGE_SIZE) {
      // Only show "all loaded" message when it was actually paginated
      const footer = document.createElement("div");
      footer.className = "load-footer";
      footer.textContent = `All ${this._total} clip${this._total !== 1 ? "s" : ""} loaded`;
      grid.appendChild(footer);
    }

    // Card click opens modal; lock button toggles lock without opening modal
    grid.querySelectorAll(".clip-card").forEach(card => {
      card.onclick = () => this._openModal(parseInt(card.dataset.idx, 10));
    });

    grid.querySelectorAll(".lock-btn").forEach(btn => {
      btn.onclick = e => {
        e.stopPropagation(); // don't open modal
        this._toggleLock(btn);
      };
    });

    grid.querySelectorAll(".clip-label").forEach(el => {
      el.onclick = e => {
        e.stopPropagation(); // don't open modal
        this._editLabel(el);
      };
    });

    // Lazy thumbnail loading — only extract frames for cards near/in the viewport
    if (this._thumbObs) { this._thumbObs.disconnect(); this._thumbObs = null; }
    this._thumbObs = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        const canvas = entry.target;
        this._thumbObs.unobserve(canvas);
        extractThumbnail(canvas.dataset.src, canvas, ok => {
          canvas.nextElementSibling?.remove(); // remove spinner
          if (!ok) {
            const ph = document.createElement("div");
            ph.className = "thumb-placeholder";
            const idx = canvas.closest(".clip-card")?.dataset.idx;
            ph.innerHTML = `<span>${KIND_ICON[this._filtered[idx]?.kind] || "🎬"}</span><span>preview unavailable</span>`;
            canvas.replaceWith(ph);
          }
        });
      });
    }, { rootMargin: "200px" });

    grid.querySelectorAll(".thumb-canvas").forEach(c => this._thumbObs.observe(c));
  }

  // ── Lock / preserve ──────────────────────────────────────────────────────

  async _toggleLock(btn) {
    const filename  = btn.dataset.filename;
    const nowLocked = !btn.classList.contains("locked");
    // Optimistic UI update
    btn.classList.toggle("locked", nowLocked);
    btn.textContent = nowLocked ? "🔒" : "🔓";
    btn.title       = nowLocked ? "Unlock (allow auto-deletion)" : "Lock (preserve from auto-deletion)";
    // Update in-memory clip state so the modal and future renders are consistent
    const clip = this._allClips.find(c => c.filename === filename);
    if (clip) clip.locked = nowLocked;
    try {
      await this._hass.callApi("POST", "ring_stash/lock", { filename, locked: nowLocked });
    } catch {
      // Revert optimistic update on failure
      btn.classList.toggle("locked", !nowLocked);
      btn.textContent = !nowLocked ? "🔒" : "🔓";
      btn.title       = !nowLocked ? "Unlock (allow auto-deletion)" : "Lock (preserve from auto-deletion)";
      if (clip) clip.locked = !nowLocked;
    }
  }

  // ── Label editing ────────────────────────────────────────────────────────

  _editLabel(labelEl) {
    const filename = labelEl.dataset.filename;
    const clip     = this._allClips.find(c => c.filename === filename);
    const current  = clip?.label ?? "";

    const input = document.createElement("input");
    input.className   = "label-input";
    input.value       = current;
    input.placeholder = "Add a note…";
    input.maxLength   = 140;
    labelEl.replaceWith(input);
    input.focus();
    input.select();

    const restore = (newLabel) => {
      const el = document.createElement("div");
      el.className        = "clip-label";
      el.dataset.filename = filename;
      el.innerHTML = newLabel
        ? `<span title="${_esc(newLabel)}">${_esc(newLabel)}</span>`
        : `<span class="label-placeholder">＋ note</span>`;
      input.replaceWith(el);
      el.onclick = e => { e.stopPropagation(); this._editLabel(el); };
    };

    const save = async () => {
      const newLabel = input.value.trim();
      restore(newLabel);
      if (newLabel === current) return;
      if (clip) clip.label = newLabel;
      try {
        await this._hass.callApi("POST", "ring_stash/label", { filename, label: newLabel });
      } catch { /* non-fatal — label stays in-memory even if persist fails */ }
    };

    input.addEventListener("blur",    () => save());
    input.addEventListener("keydown", e => {
      if (e.key === "Enter")  { e.preventDefault(); input.blur(); }
      if (e.key === "Escape") { input.value = current; input.blur(); }
    });
  }

  _editModalLabel(labelEl) {
    const filename = labelEl.dataset.filename;
    const clip     = this._allClips.find(c => c.filename === filename);
    const current  = clip?.label ?? "";

    const input = document.createElement("input");
    input.className   = "modal-label-input";
    input.value       = current;
    input.placeholder = "Add a note…";
    input.maxLength   = 140;
    labelEl.replaceWith(input);
    input.focus();
    input.select();

    const save = async () => {
      const newLabel = input.value.trim();
      const el = document.createElement("span");
      el.dataset.filename = filename;
      el.className   = newLabel ? "modal-label-text" : "modal-label-empty";
      el.textContent = newLabel || "＋ Add note";
      el.title       = newLabel ? "Click to edit note" : "Click to add a note";
      el.onclick     = () => this._editModalLabel(el);
      input.replaceWith(el);

      if (newLabel === current) return;
      if (clip) clip.label = newLabel;
      try {
        await this._hass.callApi("POST", "ring_stash/label", { filename, label: newLabel });
      } catch { /* non-fatal */ }
    };

    input.addEventListener("blur",    () => save());
    input.addEventListener("keydown", e => {
      if (e.key === "Enter")  { e.preventDefault(); input.blur(); }
      if (e.key === "Escape") { input.value = current; input.blur(); }
    });
  }

  // ── Modal ────────────────────────────────────────────────────────────────

  _openModal(idx) {
    this._modalIdx = idx;
    const clip  = this._filtered[idx];
    const video = this.shadowRoot.getElementById("modal-video");
    video.src = `${MEDIA_BASE}/${encodeURIComponent(clip.filename)}`;
    video.load();
    video.play().catch(() => {});
    this.shadowRoot.getElementById("modal-kind").textContent = `${KIND_ICON[clip.kind] || ""} ${clip.kind}`;
    this.shadowRoot.getElementById("modal-kind").style.background = KIND_COLOR[clip.kind] || "#888";
    const recDate = clip.recorded_at ? new Date(clip.recorded_at) : null;
    this.shadowRoot.getElementById("modal-camera").textContent = clip.doorbell || "—";
    this.shadowRoot.getElementById("modal-date").textContent   = recDate
      ? recDate.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—";
    this.shadowRoot.getElementById("modal-time").textContent   = recDate
      ? recDate.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
    this.shadowRoot.getElementById("modal-size").textContent   = clip.size_kb
      ? _formatBytes(clip.size_kb * 1024) : "—";

    // Lock button
    const lockBtn = this.shadowRoot.getElementById("modal-lock-btn");
    lockBtn.classList.toggle("locked", !!clip.locked);
    lockBtn.textContent = clip.locked ? "🔒 Archived" : "🔓 Not archived";

    // AI description
    const aiRow = this.shadowRoot.getElementById("modal-ai-row");
    if (aiRow) {
      aiRow.innerHTML = clip.ai_description
        ? `<div class="modal-ai-text">${_esc(clip.ai_description)}</div>`
        : "";
    }

    // Label row
    const labelRow = this.shadowRoot.getElementById("modal-label-row");
    if (labelRow) {
      labelRow.innerHTML = "";
      const labelEl = document.createElement("span");
      labelEl.className        = clip.label ? "modal-label-text" : "modal-label-empty";
      labelEl.dataset.filename = clip.filename;
      labelEl.textContent      = clip.label || "＋ Add note";
      labelEl.title            = clip.label ? "Click to edit note" : "Click to add a note";
      labelEl.onclick          = () => this._editModalLabel(labelEl);
      labelRow.appendChild(labelEl);
    }

    this.shadowRoot.getElementById("modal").classList.add("open");
    this.shadowRoot.getElementById("nav-prev").disabled = idx <= 0;
    this.shadowRoot.getElementById("nav-next").disabled = idx >= this._filtered.length - 1;
  }

  _closeModal() {
    const video = this.shadowRoot.getElementById("modal-video");
    video.pause(); video.src = "";
    this.shadowRoot.getElementById("modal").classList.remove("open");
  }

  _navigate(dir) {
    const next = this._modalIdx + dir;
    if (next >= 0 && next < this._filtered.length) this._openModal(next);
  }
}

if (!customElements.get("ring-stash-viewer")) {
  customElements.define("ring-stash-viewer", RingClipViewer);
}

})(); // end IIFE
