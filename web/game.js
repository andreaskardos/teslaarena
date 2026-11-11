const status = document.querySelector("#status");
const canvas = document.querySelector("#arena");
const context = canvas.getContext("2d");
// One color per player slot (the server assigns each player a unique index).
// Yellow is left out because it is the Tesla coil color.
const playerColors = [
  "#54d8ff", "#ff738c", "#9cf276", "#b89bff", "#ff9f68", "#f2f5ff",
  "#4f7cff", "#ff5ad1", "#2fd6a8", "#d4e157", "#ff4040", "#8b5cf6",
];

function playerColor(player) {
  return playerColors[player.color % playerColors.length];
}
let socket;
let session;
let gameState;
let lastMoveSentAt = 0;

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Something went wrong.");
  return data;
}

async function refreshRooms() {
  try {
    const rooms = await request("/api/rooms");
    const list = document.querySelector("#rooms");
    list.replaceChildren();
    for (const room of rooms) {
      const item = document.createElement("li");
      item.textContent = `${room.id} · ${room.players}/${room.max_players} players · ${room.width}×${room.height} · ${room.soft_block_count} crates (${room.soft_block_percentage.toFixed(1)}%)`;
      list.append(item);
    }
    if (!rooms.length) {
      const item = document.createElement("li");
      item.textContent = "No open rooms right now.";
      list.append(item);
    }
  } catch (error) {
    status.textContent = error.message;
  }
}

const SESSION_KEY = "tesla-arena-session";
const ROUTINE_ERRORS = new Set([
  "Moving too quickly.",
  "That tile is blocked.",
  "The Tesla coil cannot be kicked in that direction.",
  // A key press can race the state update that tells the client it died.
  "Player is missing or has been eliminated.",
  "No Tesla coil slots are available.",
  "A Tesla coil is already on this tile.",
]);
const RECONNECT_DELAY_MS = 1000;
let reconnectTimer;
let previousPhase;

// The session survives a page reload in this tab, so the player can rejoin
// within the server's reconnect grace period.
function saveSession() {
  try { sessionStorage.setItem(SESSION_KEY, JSON.stringify(session)); } catch { /* storage unavailable */ }
}

function loadSession() {
  try { return JSON.parse(sessionStorage.getItem(SESSION_KEY)); } catch { return null; }
}

function clearSession() {
  try { sessionStorage.removeItem(SESSION_KEY); } catch { /* storage unavailable */ }
}

function isCreator() {
  return Boolean(session && gameState && gameState.creator_id === session.member_id);
}

async function joinRoom(roomId, inviteCode, name) {
  session = await request(`/api/rooms/${encodeURIComponent(roomId)}/join`, {
    method: "POST",
    body: JSON.stringify({ name, invite_code: inviteCode || null }),
  });
  session.name = name;
  saveSession();
  enterRoom();
}

function enterRoom(message = "") {
  // Replace the lobby prompt with a room-related message (or nothing).
  status.textContent = message;
  lastMoveSentAt = 0;
  gameState = null;
  previousPhase = null;
  // Switches the page to the three-column game layout.
  document.body.classList.add("in-game");
  document.querySelector("#room-title").textContent = `Room ${session.room_id}`;
  document.querySelector("#room-meta").textContent =
    session.role === "spectator" ? `Watching as ${session.name}.` : `Playing as ${session.name}.`;
  for (const id of ["#start-button", "#rematch-button", "#bot-controls"]) {
    document.querySelector(id).classList.add("hidden");
  }
  connect();
}

function connect() {
  window.clearTimeout(reconnectTimer);
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${location.host}${session.websocket_path}?token=${encodeURIComponent(session.token)}`);
  socket = ws;
  ws.addEventListener("open", () => {
    if (status.dataset.reconnecting) {
      delete status.dataset.reconnecting;
      status.textContent = "Reconnected to the room.";
    }
  });
  ws.addEventListener("message", (event) => handleMessage(JSON.parse(event.data)));
  ws.addEventListener("close", (event) => {
    // Ignore sockets that were replaced or closed because the player left.
    if (socket !== ws || !session) return;
    if (event.code === 1008) {
      returnToLobby("Your place in the room is no longer available.");
      return;
    }
    status.dataset.reconnecting = "true";
    status.textContent = "Connection lost. Trying to reconnect…";
    reconnectTimer = window.setTimeout(connect, RECONNECT_DELAY_MS);
  });
}

function returnToLobby(message) {
  window.clearTimeout(reconnectTimer);
  const ws = socket;
  socket = null;
  session = null;
  gameState = null;
  clearSession();
  ws?.close();
  delete status.dataset.reconnecting;
  status.textContent = message;
  document.body.classList.remove("in-game");
  refreshRooms();
}

function handleMessage(message) {
  if (message.type === "error") {
    // Bumping into walls or pressing keys fast is normal play, not an error.
    if (!ROUTINE_ERRORS.has(message.message)) status.textContent = message.message;
    return;
  }
  if (message.type !== "state") return;
  gameState = message.state;
  const creator = isCreator();
  const phase = gameState.phase;
  if (previousPhase === "finished" && phase === "lobby") {
    status.textContent = "New round! Waiting for the host to start.";
  }
  previousPhase = phase;

  const startButton = document.querySelector("#start-button");
  const playingAlone = gameState.players.length === 1;
  startButton.disabled = phase !== "lobby";
  startButton.textContent = playingAlone ? "Start against bots" : "Start match";
  startButton.classList.toggle("hidden", !creator || phase !== "lobby");
  document.querySelector("#rematch-button").classList.toggle("hidden", !creator || phase !== "finished");
  document.querySelector("#bot-controls").classList.toggle("hidden", !canManageBots());
  document.querySelector("#add-bot-button").disabled =
    gameState.players.length >= gameState.player_limit;
  const localPlayer = gameState.players.find((player) => player.id === session.member_id);
  document.querySelector("#remote-button").classList.toggle(
    "hidden",
    session.role === "spectator"
      || phase !== "playing"
      || !localPlayer?.alive
      || !localPlayer.has_remote,
  );
  const gameStatus = document.querySelector("#game-status");
  if (phase === "lobby") {
    gameStatus.textContent = creator
      ? playingAlone
        ? `Waiting for players (1/${gameState.player_limit}). Add AI bots, wait for more players, or start alone against bots of different skill levels.`
        : `Waiting for players (${gameState.players.length}/${gameState.player_limit}). You start the match.`
      : `Waiting for the host to start (${gameState.players.length}/${gameState.player_limit}).`;
  } else if (phase === "finished") {
    const outcome = gameState.winner_id ? "The match is over." : "Draw – nobody survived.";
    if (!gameState.winner_id) status.textContent = outcome;
    gameStatus.textContent = creator
      ? `${outcome} Press Play again for a new round.`
      : `${outcome} Waiting for the host to start a new round.`;
  } else if (session.role === "spectator") {
    gameStatus.textContent = "You are watching the match as a spectator.";
  } else {
    gameStatus.textContent =
      `Match in progress${localPlayer?.alive ? "" : " · You are out and now watching as a spectator"}.`;
  }
  document.querySelector("#room-meta").textContent =
    `${gameState.width}×${gameState.height} · ${gameState.players.length}/${gameState.player_limit} players · ${gameState.spectator_count} spectators · ${gameState.soft_block_count} crates`;
  render();
}

const softBlockPercentage = document.querySelector("#soft-block-percentage");
softBlockPercentage.addEventListener("input", () => {
  document.querySelector("#soft-block-value").value = `${Number(softBlockPercentage.value).toFixed(1)}%`;
});

document.querySelector("#start-button").addEventListener("click", async () => {
  try {
    await request(`/api/rooms/${encodeURIComponent(session.room_id)}/start`, {
      method: "POST",
      headers: { "X-Player-Token": session.token },
    });
  } catch (error) {
    status.textContent = error.message;
  }
});

function canManageBots() {
  return isCreator() && gameState?.phase === "lobby";
}

async function creatorRequest(path, options) {
  try {
    await request(path, {
      ...options,
      headers: { "X-Player-Token": session.token },
    });
  } catch (error) {
    status.textContent = error.message;
  }
}

document.querySelector("#add-bot-button").addEventListener("click", () => {
  creatorRequest(`/api/rooms/${encodeURIComponent(session.room_id)}/bots`, {
    method: "POST",
    body: JSON.stringify({ skill: document.querySelector("#bot-skill").value }),
  });
});

document.querySelector("#rematch-button").addEventListener("click", () => {
  creatorRequest(`/api/rooms/${encodeURIComponent(session.room_id)}/rematch`, { method: "POST" });
});

document.querySelector("#remote-button").addEventListener("click", () => {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "detonate_remote" }));
  }
});

document.querySelector("#create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const room = await request("/api/rooms", {
      method: "POST",
      body: JSON.stringify({
        private: document.querySelector("#private-room").checked,
        arena_size: Number(document.querySelector("#arena-size").value),
        player_limit: Number(document.querySelector("#player-limit").value),
        soft_block_percentage: Number(softBlockPercentage.value),
      }),
    });
    document.querySelector("#room-id").value = room.room_id;
    if (room.invite_code) document.querySelector("#invite-code").value = room.invite_code;
    await joinRoom(room.room_id, room.invite_code, document.querySelector("#player-name").value || "Player");
    if (room.invite_code) {
      // Shown after entering the room so other players can be invited.
      status.textContent = `Private room created. Invite code: ${room.invite_code}`;
    }
  } catch (error) {
    status.textContent = error.message;
  }
});

document.querySelector("#join-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await joinRoom(
      document.querySelector("#room-id").value.trim(),
      document.querySelector("#invite-code").value.trim(),
      document.querySelector("#player-name").value.trim(),
    );
  } catch (error) {
    status.textContent = error.message;
  }
});

document.querySelector("#leave-button").addEventListener("click", () => {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "leave" }));
  }
  returnToLobby("You left the room.");
});

const directions = {
  ArrowUp: "up", w: "up", W: "up",
  ArrowDown: "down", s: "down", S: "down",
  ArrowLeft: "left", a: "left", A: "left",
  ArrowRight: "right", d: "right", D: "right",
};

window.addEventListener("keydown", (event) => {
  if (!session || socket?.readyState !== WebSocket.OPEN) return;
  if (session.role === "spectator" || gameState?.phase !== "playing") return;
  const localPlayer = gameState.players.find((player) => player.id === session.member_id);
  if (!localPlayer?.alive) return;
  const direction = directions[event.key];
  if (direction) {
    event.preventDefault();
    const player = gameState?.players.find((item) => item.id === session.member_id);
    const moveInterval = (player?.move_interval ?? 0.2) * 1000;
    const now = performance.now();
    if (now - lastMoveSentAt < moveInterval) return;
    lastMoveSentAt = now;
    socket.send(JSON.stringify({ type: "move", direction }));
  } else if (event.code === "Space") {
    event.preventDefault();
    socket.send(JSON.stringify({ type: "place_coil" }));
  } else if (event.key === "r" || event.key === "R") {
    socket.send(JSON.stringify({ type: "detonate_remote" }));
  }
});

function render() {
  if (!gameState) return;
  const cellSize = 18;
  canvas.width = gameState.width * cellSize;
  canvas.height = gameState.height * cellSize;
  context.fillStyle = "#141d2e";
  context.fillRect(0, 0, canvas.width, canvas.height);
  drawCells(gameState.hard_walls, "#35445e", cellSize, 2);
  drawCells(gameState.soft_blocks, "#a35c44", cellSize, 3);
  drawPowerups(gameState.powerups || [], cellSize);
  for (const coil of gameState.coils) {
    const center = cellCenter(coil.x, coil.y, cellSize);
    context.fillStyle = "#ffca62";
    context.beginPath();
    context.arc(center.x, center.y, cellSize * 0.34, 0, Math.PI * 2);
    context.fill();
    context.fillStyle = "#fff4d5";
    context.beginPath();
    context.arc(center.x, center.y, cellSize * 0.13, 0, Math.PI * 2);
    context.fill();
  }
  for (const player of gameState.players) {
    if (!player.alive) continue;
    const center = cellCenter(player.x, player.y, cellSize);
    context.fillStyle = playerColor(player);
    context.beginPath();
    context.arc(center.x, center.y, cellSize * 0.38, 0, Math.PI * 2);
    context.fill();
    if (player.id === session?.member_id) {
      // Mark the local player so they can find themselves without a label.
      context.strokeStyle = "#ffffff";
      context.lineWidth = 2;
      context.beginPath();
      context.arc(center.x, center.y, cellSize * 0.48, 0, Math.PI * 2);
      context.stroke();
    }
  }
  drawLightning(gameState.lightning || [], cellSize);
  const playerList = document.querySelector("#players");
  playerList.replaceChildren();
  for (const player of gameState.players) {
    const item = document.createElement("li");
    const abilities = [
      `${player.lives} ${player.lives === 1 ? "life" : "lives"}`,
      `${player.coil_slots}/${player.coil_capacity} coils`,
      `Range ${player.coil_range}`,
      ...(player.can_kick ? ["Kick"] : []),
      ...(player.has_speed_up ? ["Speed Up"] : []),
      ...(player.has_remote ? ["Remote Control"] : []),
    ].join(" · ");
    const playerStatus = player.left ? "left" : player.alive ? abilities : "out";
    const swatch = document.createElement("span");
    swatch.className = "player-swatch";
    swatch.style.background = playerColor(player);
    const isYou = player.id === session?.member_id;
    const info = document.createElement("div");
    info.className = "player-info";
    const name = document.createElement("strong");
    name.textContent = `${player.bot_skill ? "🤖 " : ""}${player.name}${isYou ? " (you)" : ""}`;
    const stats = document.createElement("small");
    stats.textContent = playerStatus;
    info.append(name, stats);
    item.append(swatch, info);
    if (!player.alive) item.classList.add("out");
    if (player.bot_skill && canManageBots()) {
      const remove = document.createElement("button");
      remove.className = "secondary remove-bot";
      remove.type = "button";
      remove.textContent = "✕";
      remove.title = `Remove ${player.name}`;
      remove.setAttribute("aria-label", `Remove ${player.name}`);
      remove.addEventListener("click", () => creatorRequest(
        `/api/rooms/${encodeURIComponent(session.room_id)}/bots/${encodeURIComponent(player.id)}`,
        { method: "DELETE" },
      ));
      item.append(remove);
    }
    playerList.append(item);
  }
  const spectatorList = document.querySelector("#spectators");
  spectatorList.replaceChildren();
  for (const name of gameState.spectators || []) {
    const item = document.createElement("li");
    item.textContent = name;
    spectatorList.append(item);
  }
  document.querySelector("#spectator-heading").classList.toggle(
    "hidden",
    !gameState.spectators?.length,
  );
  if (gameState.winner_id) {
    const winner = gameState.players.find((player) => player.id === gameState.winner_id);
    status.textContent = `${winner.name} won the match!`;
  }
}

function drawPowerups(powerups, size) {
  for (const powerup of powerups) {
    if (!["kick", "fire", "bomb", "speed", "remote"].includes(powerup.type)) continue;
    const center = cellCenter(powerup.x, powerup.y, size);
    context.save();
    context.translate(center.x, center.y);
    context.rotate(Math.PI / 4);
    const colors = {
      kick: { foreground: "#8cff9b", background: "#153f35", icon: "K" },
      fire: { foreground: "#ffd166", background: "#49351b", icon: "F" },
      bomb: { foreground: "#ff9ac1", background: "#492437", icon: "B" },
      speed: { foreground: "#79d7ff", background: "#173848", icon: "S" },
      remote: { foreground: "#e5a0ff", background: "#382448", icon: "R" },
    };
    const { foreground, background, icon } = colors[powerup.type];
    context.fillStyle = background;
    context.fillRect(-size * 0.34, -size * 0.34, size * 0.68, size * 0.68);
    context.strokeStyle = foreground;
    context.lineWidth = 2;
    context.strokeRect(-size * 0.34, -size * 0.34, size * 0.68, size * 0.68);
    context.restore();
    context.fillStyle = foreground;
    context.font = `bold ${Math.round(size * 0.62)}px sans-serif`;
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(icon, center.x, center.y + 1);
  }
}

function drawLightning(effects, size) {
  for (const effect of effects) {
    const cells = effect.cells.map(([x, y]) => ({ x, y }));
    const center = { x: effect.center[0], y: effect.center[1] };

    context.save();
    context.globalCompositeOperation = "lighter";
    for (const cell of cells) {
      const point = cellCenter(cell.x, cell.y, size);
      const glow = context.createRadialGradient(point.x, point.y, 1, point.x, point.y, size * 0.72);
      glow.addColorStop(0, "rgba(255,255,255,0.98)");
      glow.addColorStop(0.25, "rgba(76,225,255,0.92)");
      glow.addColorStop(1, "rgba(24,118,255,0)");
      context.fillStyle = glow;
      context.fillRect(point.x - size, point.y - size, size * 2, size * 2);
    }

    const frame = Math.floor(performance.now() / 75);
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const end = cells.reduce((farthest, cell) => {
        if ((cell.x - center.x) * dx + (cell.y - center.y) * dy
          > (farthest.x - center.x) * dx + (farthest.y - center.y) * dy) {
          return cell;
        }
        return farthest;
      }, center);
      if (end.x === center.x && end.y === center.y) continue;

      const startPoint = cellCenter(center.x, center.y, size);
      const endPoint = cellCenter(end.x, end.y, size);
      const length = Math.abs(end.x - center.x) + Math.abs(end.y - center.y);
      const perpendicular = dx === 0 ? 1 : 0;
      const points = [startPoint];
      for (let step = 1; step < length * 2; step += 1) {
        const progress = step / (length * 2);
        const jitter = Math.sin(frame * 2.1 + step * 12.7 + center.x * 3 + center.y) * size * 0.15;
        points.push({
          x: startPoint.x + (endPoint.x - startPoint.x) * progress + (perpendicular ? jitter : 0),
          y: startPoint.y + (endPoint.y - startPoint.y) * progress + (perpendicular ? 0 : jitter),
        });
      }
      points.push(endPoint);

      for (const [color, width, blur] of [
        ["#138bff", size * 0.58, size * 0.75],
        ["#29cfff", size * 0.34, size * 0.42],
        ["#f3ffff", size * 0.13, size * 0.12],
      ]) {
        context.beginPath();
        context.moveTo(points[0].x, points[0].y);
        for (const point of points.slice(1)) context.lineTo(point.x, point.y);
        context.strokeStyle = color;
        context.lineWidth = width;
        context.lineCap = "round";
        context.lineJoin = "round";
        context.shadowColor = "#48dfff";
        context.shadowBlur = blur;
        context.stroke();
      }
    }
    context.restore();
  }
}

function drawCells(cells, color, size, inset) {
  context.fillStyle = color;
  for (const [x, y] of cells) {
    context.fillRect(x * size + inset, y * size + inset, size - inset * 2, size - inset * 2);
  }
}

function cellCenter(x, y, size) {
  return { x: x * size + size / 2, y: y * size + size / 2 };
}

const savedSession = loadSession();
if (savedSession) {
  session = savedSession;
  enterRoom("Reconnecting to your room…");
}
refreshRooms();
window.setInterval(refreshRooms, 5000);
