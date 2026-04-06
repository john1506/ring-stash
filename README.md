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
- 🧭 **Paged history recovery** — recovers older missed clips instead of stopping at the most recent 20 events
- 🗂️ **Sidebar viewer** — browse clips in a grid, search AI descriptions and notes, filter by date, camera, or event type, and play inline
- 🏷️ **Labels and locks** — add your own notes to clips and protect important recordings from retention cleanup
- 🗑️ **Retention policy** — auto-delete clips older than your configured limit while preserving locked files
- 🔒 **No extra credentials** — reuses your existing Ring integration auth token
- 📊 **Sensor entities** — per-doorbell and global archive, storage, and health sensors
- 🧪 **Diagnostics support** — download a redacted diagnostics bundle from the integration page for troubleshooting
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

Copy `custom_components/ring_stash/` into your HA `config/custom_components/` directory and restart.

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Ring Stash**
3. Configure your download path (must be inside `/media`), retention period, and poll interval
4. Done — clips will start appearing in **Settings → Media → Local Media → ring_clips** and in the **Ring Stash** sidebar panel

## Sidebar Panel

The **Ring Stash** panel (📹 in the sidebar) shows a filterable grid of all downloaded clips. You can:

- Search filenames, labels, and stored AI descriptions
- Filter by date range and clip type
- Play clips inline
- Lock clips against retention cleanup
- Add your own labels for later search

Navigate between clips with arrow keys or the on-screen buttons.

## Entities

Exact entity IDs depend on Home Assistant's slugging, but the integration creates the following sensor groups.

### Per-doorbell sensors

| Sensor | Description |
|---|---|
| Last Clip | Timestamp of the most recently recorded clip in the local archive |
| Clips Today | Number of clips recorded today |
| Clips This Week | Number of clips recorded in the last 7 days |
| Clips This Month | Number of clips recorded in the last 30 days |
| Total Clips | Total archived clips for that doorbell |
| Motion Clips | Total archived motion clips |
| Doorbell Clips | Total archived ring events |
| Live Clips | Total archived live-view recordings |
| Storage Used | Disk space used by that doorbell's clips |

### Global sensors

| Sensor | Description |
|---|---|
| Ring Stash Total Clips | Total archived clips across all doorbells |
| Ring Stash Total Storage | Total disk space used across all doorbells |
| Ring Stash Clips Today | Total clips recorded today |
| Ring Stash Clips This Week | Total clips recorded in the last 7 days |
| Ring Stash Clips This Month | Total clips recorded in the last 30 days |
| Ring Stash Oldest Clip | Oldest recorded clip still in the archive |
| Ring Stash Pending Downloads | Clips waiting for Ring's download URL to become ready |
| Ring Stash Locked Clips | Clips protected from retention cleanup |
| Ring Stash Free Space | Free space on the media volume |

## Configuration options

| Option | Default | Description |
|---|---|---|
| Download path | `/media/ring_clips` | Where clips are saved (must be inside `/media`) |
| Retention days | 30 | Clips older than this are automatically deleted |
| Poll interval | 5 min | How often to check for new clips |
| Panel title | `Ring Stash` | Sidebar title for the built-in viewer panel |

## Notes

- "Clips Today", "This Week", and "This Month" are based on the Ring event timestamp, not when the file happened to be recovered later.
- Ring Stash only keeps events that still have a downloadable recording. Metadata-only entries are skipped.

## Troubleshooting

- Use **Download diagnostics** from the integration menu in Home Assistant to capture a redacted support bundle.
- If you change the download path or panel title, reloading the integration is enough; a full Home Assistant restart is not required.

## Contributing

Pull requests welcome. Please open an issue first for significant changes.

## License

MIT — see [LICENSE](LICENSE)
