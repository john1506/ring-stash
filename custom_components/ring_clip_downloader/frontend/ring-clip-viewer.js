/**
 * Ring Clip Viewer — HA sidebar panel
 *
 * Renders a responsive grid of downloaded Ring clips browsable and playable
 * inline. All media is served from HA's /media endpoint — no external requests.
 *
 * Architecture:
 *  - Pure vanilla JS, no framework dependencies (keeps the component self-contained)
 *  - LitElement-style web component registered as <ring-clip-viewer>
 *  - Fetches clip list from /api/ring_clip_downloader/clips (registered in __init__.py)
 *  - Video plays inline in a modal overlay; no data leaves the local network
 */

const CLIPS_API = "/api/ring_clip_downloader/clips";
const MEDIA_BASE = "/media/local/ring_clips";

const KIND_ICON = { Doorbell: "🔔", Motion: "👁️", Live: "📹" };
const KIND_COLOR = { Doorbell: "#7c8cf8", Motion: "#f8c87c", Live: "#8cf87c" };

const CSS = `
  :host {
    display: block;
    height: 100%;
    background: var(--primary-background-color, #0f1117);
    color: var(--primary-text-color, #e2e4f0);
    font-family: var(--paper-font-body1_-_font-family, sans-serif);
    overflow: hidden;
  }
  .toolbar {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 16px 20px 12px;
    background: var(--card-background-color, #1e2130);
    border-bottom: 1px solid rgba(255,255,255,0.07);
    flex-wrap: wrap;
  }
  .toolbar h1 {
    margin: 0;
    font-size: 1.1rem;
    font-weight: 600;
    flex: 1;
    min-width: 160px;
  }
  .toolbar select, .toolbar input[type=text] {
    background: var(--secondary-background-color, #181b24);
    color: var(--primary-text-color, #e2e4f0);
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 0.85rem;
    cursor: pointer;
  }
  .toolbar .badge {
    background: var(--accent-color, #a5b4fc);
    color: #111;
    border-radius: 12px;
    padding: 2px 10px;
    font-size: 0.78rem;
    font-weight: 700;
  }
  .grid-wrap {
    height: calc(100% - 64px);
    overflow-y: auto;
    padding: 16px 20px;
    box-sizing: border-box;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 14px;
  }
  .clip-card {
    background: var(--card-background-color, #1e2130);
    border-radius: 10px;
    overflow: hidden;
    cursor: pointer;
    transition: transform 0.15s, box-shadow 0.15s;
    border: 1px solid rgba(255,255,255,0.06);
    position: relative;
  }
  .clip-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgba(0,0,0,0.4);
  }
  .clip-thumb {
    width: 100%;
    aspect-ratio: 16/9;
    object-fit: cover;
    display: block;
    background: #111;
  }
  .play-overlay {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    width: 48px;
    height: 48px;
    background: rgba(0,0,0,0.55);
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.4rem;
    pointer-events: none;
    transition: background 0.15s;
  }
  .clip-card:hover .play-overlay { background: rgba(0,0,0,0.75); }
  .clip-info {
    padding: 10px 12px;
  }
  .clip-kind {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.72rem;
    font-weight: 700;
    margin-bottom: 4px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .clip-name {
    font-size: 0.88rem;
    font-weight: 500;
    margin-bottom: 2px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .clip-meta {
    font-size: 0.76rem;
    color: var(--secondary-text-color, #888);
  }
  .empty {
    grid-column: 1/-1;
    text-align: center;
    padding: 60px 20px;
    opacity: 0.5;
    font-size: 1rem;
  }
  .loading {
    grid-column: 1/-1;
    text-align: center;
    padding: 60px 20px;
    opacity: 0.5;
  }

  /* Modal overlay */
  .modal-bg {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.88);
    z-index: 9999;
    align-items: center;
    justify-content: center;
    flex-direction: column;
  }
  .modal-bg.open { display: flex; }
  .modal-video {
    max-width: min(90vw, 960px);
    max-height: 70vh;
    width: 100%;
    border-radius: 8px;
    outline: none;
  }
  .modal-meta {
    margin-top: 12px;
    color: #ccc;
    font-size: 0.9rem;
    text-align: center;
  }
  .modal-close {
    position: absolute;
    top: 16px;
    right: 20px;
    background: none;
    border: none;
    color: #fff;
    font-size: 1.8rem;
    cursor: pointer;
    line-height: 1;
    padding: 4px 8px;
  }
  .modal-nav {
    display: flex;
    gap: 20px;
    margin-top: 14px;
  }
  .modal-nav button {
    background: rgba(255,255,255,0.12);
    border: none;
    color: #fff;
    padding: 8px 18px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 0.9rem;
    transition: background 0.15s;
  }
  .modal-nav button:hover { background: rgba(255,255,255,0.25); }
  .modal-nav button:disabled { opacity: 0.3; cursor: default; }
`;

class RingClipViewer extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._clips = [];
    this._filtered = [];
    this._modalIdx = -1;
    this._filterKind = "all";
    this._filterDoorbell = "all";
  }

  connectedCallback() {
    this._render();
    this._loadClips();
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>${CSS}</style>
      <div class="toolbar">
        <h1>📹 Ring Clips</h1>
        <span class="badge" id="count">–</span>
        <select id="filter-doorbell"><option value="all">All cameras</option></select>
        <select id="filter-kind">
          <option value="all">All types</option>
          <option value="Doorbell">🔔 Doorbell</option>
          <option value="Motion">👁️ Motion</option>
          <option value="Live">📹 Live</option>
        </select>
      </div>
      <div class="grid-wrap">
        <div class="grid" id="grid"><div class="loading">Loading clips…</div></div>
      </div>
      <div class="modal-bg" id="modal">
        <button class="modal-close" id="modal-close">✕</button>
        <video class="modal-video" id="modal-video" controls autoplay></video>
        <div class="modal-meta" id="modal-meta"></div>
        <div class="modal-nav">
          <button id="nav-prev">◀ Previous</button>
          <button id="nav-next">Next ▶</button>
        </div>
      </div>
    `;

    this.shadowRoot.getElementById("modal-close").onclick = () => this._closeModal();
    this.shadowRoot.getElementById("modal").onclick = (e) => {
      if (e.target === this.shadowRoot.getElementById("modal")) this._closeModal();
    };
    this.shadowRoot.getElementById("nav-prev").onclick = () => this._navigate(-1);
    this.shadowRoot.getElementById("nav-next").onclick = () => this._navigate(1);
    this.shadowRoot.getElementById("filter-kind").onchange = (e) => {
      this._filterKind = e.target.value;
      this._applyFilters();
    };
    this.shadowRoot.getElementById("filter-doorbell").onchange = (e) => {
      this._filterDoorbell = e.target.value;
      this._applyFilters();
    };

    document.addEventListener("keydown", (e) => {
      if (!this.shadowRoot.getElementById("modal").classList.contains("open")) return;
      if (e.key === "Escape") this._closeModal();
      if (e.key === "ArrowLeft") this._navigate(-1);
      if (e.key === "ArrowRight") this._navigate(1);
    });
  }

  async _loadClips() {
    try {
      const resp = await fetch(CLIPS_API, {
        headers: { Authorization: `Bearer ${this._getToken()}` },
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      this._clips = await resp.json();
      this._populateFilters();
      this._applyFilters();
    } catch (err) {
      this.shadowRoot.getElementById("grid").innerHTML =
        `<div class="empty">Failed to load clips: ${err.message}</div>`;
    }
  }

  _getToken() {
    // HA stores the auth token in localStorage
    try {
      const auth = JSON.parse(localStorage.getItem("hassTokens") || "{}");
      return auth.access_token || "";
    } catch {
      return "";
    }
  }

  _populateFilters() {
    const doorbells = [...new Set(this._clips.map((c) => c.doorbell))].sort();
    const sel = this.shadowRoot.getElementById("filter-doorbell");
    sel.innerHTML = `<option value="all">All cameras</option>` +
      doorbells.map((d) => `<option value="${d}">${d}</option>`).join("");
  }

  _applyFilters() {
    this._filtered = this._clips.filter((c) => {
      if (this._filterKind !== "all" && c.kind !== this._filterKind) return false;
      if (this._filterDoorbell !== "all" && c.doorbell !== this._filterDoorbell) return false;
      return true;
    });
    this._renderGrid();
  }

  _renderGrid() {
    const grid = this.shadowRoot.getElementById("grid");
    const count = this.shadowRoot.getElementById("count");
    count.textContent = `${this._filtered.length} clip${this._filtered.length !== 1 ? "s" : ""}`;

    if (this._filtered.length === 0) {
      grid.innerHTML = `<div class="empty">No clips found. Ring events will appear here after the next poll.</div>`;
      return;
    }

    grid.innerHTML = this._filtered
      .map((clip, idx) => {
        const color = KIND_COLOR[clip.kind] || "#888";
        const icon = KIND_ICON[clip.kind] || "🎥";
        const date = new Date(clip.recorded_at);
        const dateStr = date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
        const timeStr = date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
        return `
          <div class="clip-card" data-idx="${idx}">
            <video class="clip-thumb" preload="metadata" muted
              src="${MEDIA_BASE}/${encodeURIComponent(clip.filename)}#t=0.5">
            </video>
            <div class="play-overlay">▶</div>
            <div class="clip-info">
              <span class="clip-kind" style="background:${color};color:#111">${icon} ${clip.kind}</span>
              <div class="clip-name">${clip.doorbell}</div>
              <div class="clip-meta">${dateStr} · ${timeStr} · ${clip.size_kb} KB</div>
            </div>
          </div>
        `;
      })
      .join("");

    grid.querySelectorAll(".clip-card").forEach((card) => {
      card.onclick = () => this._openModal(parseInt(card.dataset.idx, 10));
    });
  }

  _openModal(idx) {
    this._modalIdx = idx;
    const clip = this._filtered[idx];
    const modal = this.shadowRoot.getElementById("modal");
    const video = this.shadowRoot.getElementById("modal-video");
    const meta = this.shadowRoot.getElementById("modal-meta");

    video.src = `${MEDIA_BASE}/${encodeURIComponent(clip.filename)}`;
    video.load();
    video.play().catch(() => {});

    const date = new Date(clip.recorded_at);
    meta.textContent = `${clip.doorbell} · ${clip.kind} · ${date.toLocaleString()} · ${clip.size_kb} KB`;

    modal.classList.add("open");
    this._updateNavButtons();
  }

  _closeModal() {
    const modal = this.shadowRoot.getElementById("modal");
    const video = this.shadowRoot.getElementById("modal-video");
    video.pause();
    video.src = "";
    modal.classList.remove("open");
  }

  _navigate(dir) {
    const next = this._modalIdx + dir;
    if (next >= 0 && next < this._filtered.length) this._openModal(next);
  }

  _updateNavButtons() {
    this.shadowRoot.getElementById("nav-prev").disabled = this._modalIdx <= 0;
    this.shadowRoot.getElementById("nav-next").disabled =
      this._modalIdx >= this._filtered.length - 1;
  }
}

customElements.define("ring-clip-viewer", RingClipViewer);
