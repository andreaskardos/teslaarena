# Tesla Arena

Tesla Arena is a server-authoritative, browser-based multiplayer arena game. Its
first playable version supports up to 12 players in public or invite-only rooms.

## Set up on Windows

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, allow it for the current terminal only, then
activate the environment:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Activation is needed again in each new terminal. After a PC restart, open a
PowerShell terminal in the project folder and run:

```powershell
.\.venv\Scripts\Activate.ps1
```

If `.venv` is missing, recreate it and install dependencies with the setup
commands above. `.venv` is local and is not committed; `requirements.txt` lists
the packages needed to restore it.

## Run locally

With `.venv` activated:

```powershell
python -m uvicorn tesla_arena.server:app --app-dir src --reload
```

Open <http://127.0.0.1:8000>. Run the test suite with:

```powershell
python -m pytest
```

For the development roadmap and steps to resume work in a new session, see
[ROADMAP.md](ROADMAP.md). For a concise restart and project handover guide, see
[HANDOVER.md](HANDOVER.md).

## Battle rules

- When creating a room, choose a 25×25, 35×35, or 45×45 arena and set the
  player limit from 2 to 12. Twelve-player rooms retain their clear perimeter
  spawns, at least 10 Manhattan tiles apart on the default 35×35 arena.
- Choose destructible block density from 0% to 100%. The percentage is applied
  to available interior tiles after spawn safety zones are reserved. The
  default is 2.3% (16 blocks on the default 35×35, 12-player map).
- Players can place their starting Tesla coil immediately after joining.
- Players start with one Tesla coil slot. Bomb Up power-ups increase the number
  of coils they can place at once. Coils discharge after
  2.5 seconds, sending a cross-shaped electric attack up to two tiles in each
  direction. Hard walls stop the attack; a soft block is destroyed and absorbs
  the rest of that ray.
- A player hit by electricity is eliminated. The last player alive wins.
- Destroyed soft blocks have a chance to drop a Kick power-up. A player with Kick
  can walk into a Tesla coil to send it one tile at a time down a corridor; it
  stops at obstacles and still explodes when its original fuse expires.
- Destroyed soft blocks can also drop Fire Up, which permanently increases that
  player's Tesla coil lightning range by one tile for the current match.
- Bomb Up can also drop from destroyed blocks and permanently adds one
  simultaneous Tesla coil slot for that player during the match.
- Speed Up can drop from destroyed blocks and lets its collector move one tile
  every 0.12 seconds instead of the usual 0.2 seconds.
- The room creator starts the match. Game actions are disabled in the lobby;
  anyone joining after the match starts is an observer and cannot control a
  player.
- Before the match, the creator can add AI bots of any skill (easy, medium or
  hard) alongside human players, or remove them again. Bots take player slots.
- A creator who starts alone without adding bots plays against Volt (easy),
  Ampere (medium) and Tesla (hard), limited by the free player slots. Easy bots react
  slowly and sometimes misjudge their own coils; hard bots react quickly,
  foresee chain reactions and hunt other players.
- Remote Control can drop from destroyed blocks. New coils belonging to its
  collector wait until they are manually triggered with the R key or the
  on-screen control; the resulting lightning can chain-detonate other coils.
- Create a public room to list it in the lobby. A private room is hidden from
  the list and requires its invitation code to join.

Movement uses the arrow keys or WASD; Space places a coil, and R triggers
Remote Control coils. Room state is held in memory and is lost when the server
restarts.

## Leaving, reconnecting and rematches

- A player whose connection drops has 15 seconds to come back. The browser
  reconnects automatically, also after a page reload in the same tab. After
  that the player is removed from the lobby, or eliminated and shown as "left"
  during a match.
- **Lämna rum** leaves at once. If the creator leaves, the next human player
  becomes the creator.
- When a match is over, the creator can press **Spela igen**. The room returns
  to its lobby with a new arena of the same size and density, keeping the
  remaining players and bots, so bots can be adjusted before the next start.
- Rooms without any players are deleted after 30 seconds.
- Each address can create 5 rooms and join 30 times per minute, and each
  connection can send 20 game actions per second (bursts of 40). The server
  hosts at most 200 rooms with up to 50 spectators each.
