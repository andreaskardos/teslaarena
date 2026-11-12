# Tesla Arena handover

Updated: 2026-11-11

## Current state

- Repository: `andreaskardos/kronsmash`, branch `master`.
- Use `git --no-pager log -5 --oneline` for the latest commit.
- The game supports listed rooms with Join/Watch buttons and match stats, arenas up to 45×45, up to 8 players, configurable crate density, Tesla coils and power-ups.
- Each room runs one game loop (20 ticks per second) that sends state only when it changes.
- The room creator starts the match. AI bots (easy, medium, hard) can be added in the lobby; a creator who starts alone gets three automatically. Late arrivals can only spectate.
- Dropped players have 15 seconds to reconnect; the creator can start a rematch when a match ends; empty rooms are deleted.
- Remote Control allows its owner to trigger their coils with **R** or the in-game button.
- Requests are rate limited per IP (5 rooms and 30 joins per minute) and per connection (20 actions per second); the server holds at most 200 rooms and 50 spectators per room.
- Last test run: **77 passed**. `node --check web\game.js` and `git diff --check` passed. There is one existing Starlette/AnyIO deprecation warning.

## Resume after restarting the PC

Open PowerShell and run:

```powershell
Set-Location C:\Users\Nec\repos\kronsmash
git status --short
git --no-pager log -5 --oneline
```

The latest work is already pushed. If the repository needs updating from GitHub:

```powershell
git pull --ff-only origin master
```

Activate the existing virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

If `.venv` is missing, recreate it and install dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, allow it for this terminal only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Run the tests and optionally check the browser script:

```powershell
python -m pytest -q
node --check web\game.js
```

Start the local server:

```powershell
python -m uvicorn tesla_arena.server:app --app-dir src --reload
```

Open <http://127.0.0.1:8000>. Stop the server with `Ctrl+C`. Room and match
state is in memory, so rooms are lost when the server stops.

## Recommended next work

The next roadmap item is a real multi-browser playtest. Bots make it possible
to play a full match alone. Verify public and private rooms, adding and
removing bots, creator-only start, late-join spectators, a full 12-player room,
movement and Tesla-coil combat, Remote Control, the winner display, rematches,
and reloading the page or closing a tab mid-match. Record any issues as
regression tests before changing game behavior.

A scripted Playwright playtest in Edge (2026-11-11) passed: private rooms and
invite codes, a full 12-player room with a rejected 13th player, a late
spectator, and a 12-player match. Move latency on localhost was about 35-50 ms
median with no dropped moves. A two-browser run in Edge also covers the lobby, adding and
removing bots, joining by code, reloading, closing a tab mid-match, rematch
and leaving. Still to check by hand: private rooms, a full 12-player room, and
how movement and the bots feel.

Note: the per-IP limits use the direct client address. Behind a reverse proxy
they need the forwarded address instead (roadmap stage 4).

See [ROADMAP.md](ROADMAP.md) for the broader roadmap and [README.md](README.md)
for setup and gameplay rules.
