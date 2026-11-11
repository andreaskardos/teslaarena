# Tesla Arena roadmap

## Current project status

- [x] Python game rules for grid movement, obstacles, Tesla coils, lightning,
      elimination, and the last-player-standing result.
- [x] Twelve-player rooms, public room discovery, private invitation codes, and
      authenticated WebSocket connections.
- [x] Kick power-up drops, collection, and coil sliding.
- [x] Fire Up power-up drops, collection, and increased lightning range.
- [x] Bomb Up power-up drops, collection, and increased coil capacity.
- [x] Players start with one coil slot; Bomb Up adds another slot.
- [x] Speed Up power-up drops, collection, and faster movement.
- [x] Choose the number of destructible blocks when creating a room.
- [x] Browser lobby and arena with keyboard controls.
- [x] Automated game, room, HTTP, and WebSocket tests.
- [x] Windows virtual-environment setup and dependency list.
- [ ] Complete a real multi-browser playtest and tune arena balance.

## Roadmap

### 1. Stabilize the first multiplayer release

- Start the server and verify the lobby in a browser.
- Test a public room and an invitation-only room in separate browser sessions.
- Join with all 12 player slots and confirm a 13th join is rejected.
- Test movement, coil placement, wall/block interactions, self-elimination,
  player elimination, and winner display during a real match.
- Fix issues found in that playtest and add regression tests.

### 2. Improve match and connection lifecycle

- Define when a room becomes a started match and whether late joins are allowed.
- Handle disconnects, reconnects, and abandoned-room cleanup explicitly.
- Add a rematch flow and a clear match-over state.
- Add sensible limits for room creation and incoming game actions.

### 3. Polish the battle experience

- Improve movement timing and visuals for lightning and block destruction.
- Add sound, mobile-friendly controls, and clearer player/team status.
- Add remote-trigger power-up.
- Tune arena size, spawn distance, obstacles, fuse time, and attack range through
  playtesting.

### 4. Add persistent and deployable hosting

- Choose storage for accounts, room history, and persistent settings if needed.
- Configure production hosting, HTTPS/WSS, logging, and health checks.
- Document deployment, backup, monitoring, and upgrade procedures.

### 5. Build adventure mode

- Add campaign levels, enemies, and player lives.
- Hide exits under destructible blocks and unlock them after enemies are defeated.
- Add level progression and adventure-specific tests.

## Resume work after a new session or PC restart

1. Open a terminal in the repository folder:

   ```powershell
   Set-Location C:\Users\Nec\repos\kronsmash
   ```

2. Check repository state before editing:

   ```powershell
   git status --short
   git --no-pager log -5 --oneline
   ```

3. Activate the existing virtual environment. If it does not exist, recreate
   it and restore dependencies:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

   If activation is blocked, run
   `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that terminal
   and activate again.

4. Run all tests before starting new work:

   ```powershell
   python -m pytest -q
   ```

5. Start the development server and open the local game:

   ```powershell
   python -m uvicorn tesla_arena.server:app --app-dir src --reload
   ```

   Browse to <http://127.0.0.1:8000>. Stop the server with `Ctrl+C`.

6. Continue with roadmap phase 1. The repository currently has no Git commits;
   once the first verified commit is created, use `git log` above to identify
   the latest saved state.
