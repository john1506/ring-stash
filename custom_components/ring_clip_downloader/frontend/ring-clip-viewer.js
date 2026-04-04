/**
 * RingStash Clip Viewer — HA sidebar panel
 *
 * Canvas-based thumbnail generation: each clip card loads the video into
 * a hidden <video>, seeks to 2 seconds, then paints that frame onto a
 * <canvas> which becomes the visible thumbnail. No server-side ffmpeg needed.
 */

const CLIPS_API  = "/api/ring_clip_downloader/clips";
const MEDIA_BASE = "/ring_clip_downloader_media";

const KIND_COLOR = { Doorbell: "#7c8cf8", Motion: "#f8c87c", Live: "#8cf87c" };
const KIND_ICON  = { Doorbell: "🔔", Motion: "👁", Live: "📹" };

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
    padding: 14px 20px; flex-shrink: 0; flex-wrap: wrap;
    background: var(--card-background-color, #1e2130);
    border-bottom: 1px solid rgba(255,255,255,0.07);
  }
  .toolbar-title { font-size: 1.05rem; font-weight: 700; flex: 1; min-width: 120px; }
  .pill-count {
    background: var(--primary-color, #7c8cf8); color: #fff;
    border-radius: 20px; padding: 2px 10px; font-size: 0.75rem; font-weight: 700;
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
  .grid-wrap { flex: 1; overflow-y: auto; padding: 18px 20px; box-sizing: border-box; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }
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
  .clip-body { padding: 11px 13px 13px; display: flex; flex-direction: column; gap: 3px; }
  .clip-cam { font-size: 0.82rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .clip-date { font-size: 0.75rem; color: var(--secondary-text-color, #888); }
  .clip-size { font-size: 0.7rem; color: var(--secondary-text-color, #666); margin-top: 1px; }
  .state-msg {
    grid-column: 1/-1; display: flex; flex-direction: column; align-items: center;
    justify-content: center; padding: 80px 20px; gap: 16px; opacity: 0.5;
    font-size: 0.95rem; text-align: center;
  }
  .state-msg .icon { font-size: 3rem; }
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
  .modal-info { margin-top: 14px; display: flex; align-items: center; gap: 14px; flex-wrap: wrap; justify-content: center; }
  .modal-kind { padding: 3px 12px; border-radius: 20px; font-size: 0.78rem; font-weight: 700; color: #111; }
  .modal-meta { color: rgba(255,255,255,0.65); font-size: 0.85rem; }
  .modal-nav { display: flex; gap: 12px; margin-top: 16px; }
  .nav-btn {
    background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.12);
    color: #fff; padding: 8px 20px; border-radius: 8px; cursor: pointer;
    font-size: 0.85rem; transition: background 0.15s;
  }
  .nav-btn:hover:not(:disabled) { background: rgba(255,255,255,0.2); }
  .nav-btn:disabled { opacity: 0.25; cursor: default; }
`;

/* ── Canvas thumbnail extraction ─────────────────────────────────────────── */
function extractThumbnail(videoUrl, canvas, onDone) {
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

/* ── Component ───────────────────────────────────────────────────────────── */
class RingClipViewer extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._clips = []; this._filtered = [];
    this._modalIdx = -1;
    this._filterKind = "all"; this._filterDoorbell = "all";
    this._hass = null;
    this._loaded = false;
  }

  // HA passes the hass object to every custom panel via this setter
  set hass(hass) {
    this._hass = hass;
    if (!this._loaded) {
      this._loaded = true;
      this._loadClips();
    }
  }

  connectedCallback() { this._render(); }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>${CSS}</style>
      <div class="toolbar">
        <div class="toolbar-title">📹 RingStash</div>
        <span class="pill-count" id="count">–</span>
        <div class="filter-wrap" id="kind-filters">
          <button class="filter-btn active" data-kind="all">All</button>
          <button class="filter-btn" data-kind="Doorbell">🔔 Doorbell</button>
          <button class="filter-btn" data-kind="Motion">👁 Motion</button>
          <button class="filter-btn" data-kind="Live">📹 Live</button>
        </div>
        <select class="filter-select" id="cam-filter"><option value="all">All cameras</option></select>
      </div>
      <div class="grid-wrap">
        <div class="grid" id="grid"><div class="state-msg"><div class="icon">⏳</div>Loading clips…</div></div>
      </div>
      <div class="modal-bg" id="modal">
        <button class="modal-close" id="modal-close" title="Close (Esc)">✕</button>
        <video class="modal-video" id="modal-video" controls autoplay></video>
        <div class="modal-info">
          <span class="modal-kind" id="modal-kind"></span>
          <span class="modal-meta" id="modal-meta"></span>
        </div>
        <div class="modal-nav">
          <button class="nav-btn" id="nav-prev">◀ Previous</button>
          <button class="nav-btn" id="nav-next">Next ▶</button>
        </div>
      </div>`;

    this.shadowRoot.getElementById("kind-filters").addEventListener("click", e => {
      const btn = e.target.closest(".filter-btn");
      if (!btn) return;
      this.shadowRoot.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      this._filterKind = btn.dataset.kind;
      this._applyFilters();
    });
    this.shadowRoot.getElementById("cam-filter").onchange = e => {
      this._filterDoorbell = e.target.value; this._applyFilters();
    };
    this.shadowRoot.getElementById("modal-close").onclick = () => this._closeModal();
    this.shadowRoot.getElementById("modal").onclick = e => {
      if (e.target.id === "modal") this._closeModal();
    };
    this.shadowRoot.getElementById("nav-prev").onclick = () => this._navigate(-1);
    this.shadowRoot.getElementById("nav-next").onclick  = () => this._navigate(1);
    document.addEventListener("keydown", e => {
      if (!this.shadowRoot.getElementById("modal").classList.contains("open")) return;
      if (e.key === "Escape") this._closeModal();
      if (e.key === "ArrowLeft")  this._navigate(-1);
      if (e.key === "ArrowRight") this._navigate(1);
    });
  }

  async _loadClips() {
    try {
      const resp = await fetch(CLIPS_API, { headers: { Authorization: `Bearer ${this._hass.auth.data.access_token}` } });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      this._clips = await resp.json();
      const cams = [...new Set(this._clips.map(c => c.doorbell))].sort();
      const sel = this.shadowRoot.getElementById("cam-filter");
      sel.innerHTML = `<option value="all">All cameras</option>` +
        cams.map(c => `<option value="${c}">${c}</option>`).join("");
      this._applyFilters();
    } catch (err) {
      this.shadowRoot.getElementById("grid").innerHTML =
        `<div class="state-msg"><div class="icon">⚠️</div>Failed to load clips:<br>${err.message}</div>`;
    }
  }

  _applyFilters() {
    this._filtered = this._clips.filter(c =>
      (this._filterKind === "all" || c.kind === this._filterKind) &&
      (this._filterDoorbell === "all" || c.doorbell === this._filterDoorbell)
    );
    this._renderGrid();
  }

  _renderGrid() {
    const grid = this.shadowRoot.getElementById("grid");
    this.shadowRoot.getElementById("count").textContent =
      `${this._filtered.length} clip${this._filtered.length !== 1 ? "s" : ""}`;

    if (!this._filtered.length) {
      grid.innerHTML = `<div class="state-msg"><div class="icon">🎬</div>No clips match this filter.</div>`;
      return;
    }

    grid.innerHTML = this._filtered.map((clip, idx) => {
      const color   = KIND_COLOR[clip.kind] || "#888";
      const icon    = KIND_ICON[clip.kind]  || "🎥";
      const date    = new Date(clip.recorded_at);
      const dateStr = date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
      const timeStr = date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
      return `
        <div class="clip-card" data-idx="${idx}">
          <div class="thumb-wrap">
            <canvas class="thumb-canvas" data-src="${MEDIA_BASE}/${encodeURIComponent(clip.filename)}"></canvas>
            <div class="thumb-loading"><div class="spinner"></div></div>
            <span class="kind-badge" style="background:${color}">${icon} ${clip.kind}</span>
            <div class="play-btn">
              <svg viewBox="0 0 80 80" fill="none" xmlns="http://www.w3.org/2000/svg">
                <circle cx="40" cy="40" r="38" fill="rgba(0,0,0,0.5)" stroke="rgba(255,255,255,0.8)" stroke-width="2"/>
                <polygon points="32,24 60,40 32,56" fill="white"/>
              </svg>
            </div>
          </div>
          <div class="clip-body">
            <div class="clip-cam">${clip.doorbell}</div>
            <div class="clip-date">${dateStr} · ${timeStr}</div>
            <div class="clip-size">${clip.size_kb} KB</div>
          </div>
        </div>`;
    }).join("");

    grid.querySelectorAll(".clip-card").forEach(card => {
      card.onclick = () => this._openModal(parseInt(card.dataset.idx, 10));
    });

    // Stagger thumbnail extraction — 3 at a time every 200ms
    const canvases = [...grid.querySelectorAll(".thumb-canvas")];
    const next = (i) => {
      if (i >= canvases.length) return;
      canvases.slice(i, i + 3).forEach(canvas => {
        extractThumbnail(canvas.dataset.src, canvas, ok => {
          const spinner = canvas.nextElementSibling;
          spinner && spinner.remove();
          if (!ok) {
            const ph = document.createElement("div");
            ph.className = "thumb-placeholder";
            ph.innerHTML = `<span>${KIND_ICON[this._filtered[canvas.closest(".clip-card").dataset.idx]?.kind] || "🎬"}</span><span>preview unavailable</span>`;
            canvas.replaceWith(ph);
          }
        });
      });
      setTimeout(() => next(i + 3), 200);
    };
    next(0);
  }

  _openModal(idx) {
    this._modalIdx = idx;
    const clip  = this._filtered[idx];
    const video = this.shadowRoot.getElementById("modal-video");
    video.src = `${MEDIA_BASE}/${encodeURIComponent(clip.filename)}`;
    video.load();
    video.play().catch(() => {});
    this.shadowRoot.getElementById("modal-kind").textContent = `${KIND_ICON[clip.kind] || ""} ${clip.kind}`;
    this.shadowRoot.getElementById("modal-kind").style.background = KIND_COLOR[clip.kind] || "#888";
    this.shadowRoot.getElementById("modal-meta").textContent =
      `${clip.doorbell} · ${new Date(clip.recorded_at).toLocaleString()} · ${clip.size_kb} KB`;
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

customElements.define("ring-clip-viewer", RingClipViewer);
