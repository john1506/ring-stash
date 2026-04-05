# Ring Stash

**Your Ring clips, your server.**

Ring Stash is a Home Assistant custom integration that automatically downloads and stores Ring doorbell clips to your local Home Assistant server the moment they're ready. Browse, filter, and watch footage from a built-in sidebar panel — no Ring app, no cloud dependency for playback.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=john1506&repository=ring-stash&category=integration)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## Features

- 📥 **Auto-download** — clips appear locally within seconds of Ring processing them
- 🔄 **Smart retry** — polls until the clip URL is ready (up to 3 minutes), never drops a recording
- 🗂️ **Sidebar viewer** — browse clips in a grid, filter by camera or event type, play inline
- 🗑️ **Retention policy** — auto-delete clips older than your configured limit
- 🔒 **No extra credentials** — reuses your existing Ring integration auth token
- 📊 **Sensor entities** — last clip timestamp, clips today count per doorbell
- ⚡ **Rate-limit safe** — polls Ring API at ≤3 req/min, well under Ring's 12 req/min limit

## Requirements

- Home Assistant 2024.1.0+
- [Ring integration](https://www.home-assistant.io/integrations/ring/) configured and working
- Ring subscription (for recorded clip access)
- HACS installed

## Installation

### Via HACS (recommended)

1. In HACS, go to **Integrations → ⋮ → Custom repositories**
2. Add `https://github.com/john1506/ring-stash` as an **Integration**
3. Search for **Ring Stash** and install
4. Restart Home Assistant

### Manual

Copy `custom_components/ring_clip_downloader/` into your HA `config/custom_components/` directory and restart.

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Ring Stash**
3. Configure your download path (must be inside `/media`), retention period, and poll interval
4. Done — clips will start appearing in **Settings → Media → Local Media → ring_clips** and in the **Ring Clips** sidebar panel

## Sidebar Panel

The **Ring Clips** panel (📹 in the sidebar) shows a filterable grid of all downloaded clips. Click any clip to play it inline. Use the camera and event type filters to find what you're looking for. Navigate between clips with arrow keys or the on-screen buttons.

## Entities

| Entity | Description |
|---|---|
| `sensor.{doorbell}_last_clip` | Timestamp of the most recently downloaded clip |
| `sensor.{doorbell}_clips_today` | Number of clips downloaded today |

## Configuration options

| Option | Default | Description |
|---|---|---|
| Download path | `/media/ring_clips` | Where clips are saved (must be inside `/media`) |
| Retention days | 30 | Clips older than this are automatically deleted |
| Poll interval | 5 min | How often to check for new clips |

## Contributing

Pull requests welcome. Please open an issue first for significant changes.

## License

MIT — see [LICENSE](LICENSE)
