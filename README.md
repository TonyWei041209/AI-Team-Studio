# AI Team Studio

A local-first, multi-model, multi-role desktop AI workstation for software project management.

## Quick Start

### Prerequisites

- Node.js 18+
- Python 3.11+
- Rust (via rustup)

### Development

**Option 1: Using dev script (Windows)**
```bat
scripts\dev.bat
```

**Option 2: Manual startup**

Terminal 1 - Start the runtime:
```bash
cd services/runtime
pip install -r requirements.txt
python main.py
```

Terminal 2 - Start the desktop app:
```bash
cd apps/desktop
npm install
npm run tauri:dev
```

### Verify

- Runtime health: http://127.0.0.1:9800/api/health
- Desktop app should show connection status on the home page

## Project Structure

```
apps/desktop/        Tauri + React + TypeScript
services/runtime/    Python FastAPI server
packages/shared/     Shared types (future)
data/                SQLite database
docs/                Documentation
scripts/             Dev scripts
```

## Tech Stack

- **Desktop**: Tauri v2
- **Frontend**: React + TypeScript + Vite
- **Runtime**: Python + FastAPI
- **Database**: SQLite
