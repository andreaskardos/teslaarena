# Tesla Arena handover

Updated: 2026-11-11

## Current state

- Repository: `andreaskardos/kronsmash`, branch `master`.
- Latest verified and pushed implementation commit: `1bace2a` — `Add host-started matches and remote control`.
- The working tree was clean when this handover was prepared.
- The game supports public/private rooms, arenas up to 45×45, up to 12 players, configurable crate density, Tesla coils and power-ups.
- A room creator starts the match once at least two players have joined. Late arrivals can only spectate.
- Remote Control allows its owner to trigger their coils with **R** or the in-game button.
- Last test run: **45 passed**. `node --check web\game.js` and `git diff --check` passed. There is one existing Starlette/AnyIO deprecation warning.

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

The next roadmap item is a real multi-browser playtest. Verify public and
private rooms, creator-only start, late-join spectators, a full 12-player room,
movement and Tesla-coil combat, Remote Control, and the winner display. Record
any issues as regression tests before changing game behavior.

See [ROADMAP.md](ROADMAP.md) for the broader roadmap and [README.md](README.md)
for setup and gameplay rules.
