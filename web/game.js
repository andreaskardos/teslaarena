const lobby = document.querySelector("#lobby");
const gamePanel = document.querySelector("#game");
const status = document.querySelector("#status");
const canvas = document.querySelector("#arena");
const context = canvas.getContext("2d");
const colors = ["#54d8ff", "#ff738c", "#9cf276", "#ffc857", "#b89bff", "#ff9f68"];
let socket;
let session;
let gameState;

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Något gick fel.");
  return data;
}

async function refreshRooms() {
  try {
    const rooms = await request("/api/rooms");
    const list = document.querySelector("#rooms");
    list.replaceChildren();
    for (const room of rooms) {
      const item = document.createElement("li");
      item.textContent = `${room.id} · ${room.players}/${room.max_players} spelare · ${room.soft_block_count} lådor`;
      list.append(item);
    }
    if (!rooms.length) {
      const item = document.createElement("li");
      item.textContent = "Inga öppna rum just nu.";
      list.append(item);
    }
  } catch (error) {
    status.textContent = error.message;
  }
}

async function joinRoom(roomId, inviteCode, name) {
  session = await request(`/api/rooms/${encodeURIComponent(roomId)}/join`, {
    method: "POST",
    body: JSON.stringify({ name, invite_code: inviteCode || null }),
  });
  lobby.classList.add("hidden");
  gamePanel.classList.remove("hidden");
  document.querySelector("#room-title").textContent = `Rum ${session.room_id}`;
  document.querySelector("#room-meta").textContent = `Du spelar som ${name}. Dela rumskod eller privat inbjudningskod.`;
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${protocol}//${location.host}${session.websocket_path}?token=${encodeURIComponent(session.token)}`);
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "state") {
      gameState = message.state;
      render();
    } else if (message.type === "error") {
      status.textContent = message.message;
    }
  });
  socket.addEventListener("close", () => { status.textContent = "Anslutningen till spelet stängdes."; });
}

document.querySelector("#create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const room = await request("/api/rooms", {
      method: "POST",
      body: JSON.stringify({
        private: document.querySelector("#private-room").checked,
        soft_block_count: Number(document.querySelector("#soft-block-count").value),
      }),
    });
    document.querySelector("#room-id").value = room.room_id;
    if (room.invite_code) {
      document.querySelector("#invite-code").value = room.invite_code;
      status.textContent = `Privat rum skapat. Inbjudningskod: ${room.invite_code}`;
    }
    await joinRoom(room.room_id, room.invite_code, document.querySelector("#player-name").value || "Spelare");
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
  socket?.close();
  session = null;
  gameState = null;
  gamePanel.classList.add("hidden");
  lobby.classList.remove("hidden");
  refreshRooms();
});

const directions = {
  ArrowUp: "up", w: "up", W: "up",
  ArrowDown: "down", s: "down", S: "down",
  ArrowLeft: "left", a: "left", A: "left",
  ArrowRight: "right", d: "right", D: "right",
};

window.addEventListener("keydown", (event) => {
  if (!session || socket?.readyState !== WebSocket.OPEN) return;
  const direction = directions[event.key];
  if (direction) {
    event.preventDefault();
    socket.send(JSON.stringify({ type: "move", direction }));
  } else if (event.code === "Space") {
    event.preventDefault();
    socket.send(JSON.stringify({ type: "place_coil" }));
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
  gameState.players.forEach((player, index) => {
    if (!player.alive) return;
    const center = cellCenter(player.x, player.y, cellSize);
    context.fillStyle = colors[index % colors.length];
    context.beginPath();
    context.arc(center.x, center.y, cellSize * 0.38, 0, Math.PI * 2);
    context.fill();
    context.fillStyle = "#102033";
    context.font = "bold 10px sans-serif";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(player.name.slice(0, 2).toUpperCase(), center.x, center.y);
  });
  drawLightning(gameState.lightning || [], cellSize);
  const playerList = document.querySelector("#players");
  playerList.replaceChildren();
  for (const player of gameState.players) {
    const item = document.createElement("li");
    const abilities = [
      `${player.lives} liv`,
      `${player.coil_slots}/${player.coil_capacity} coils`,
      `Räckvidd ${player.coil_range}`,
      ...(player.can_kick ? ["Kick"] : []),
    ].join(" · ");
    item.textContent = `${player.name} · ${player.alive ? abilities : "ute"}`;
    if (!player.alive) item.style.color = "#78869e";
    playerList.append(item);
  }
  if (gameState.winner_id) {
    const winner = gameState.players.find((player) => player.id === gameState.winner_id);
    status.textContent = `${winner.name} vann matchen!`;
  }
}

function drawPowerups(powerups, size) {
  for (const powerup of powerups) {
    if (!["kick", "fire", "bomb"].includes(powerup.type)) continue;
    const center = cellCenter(powerup.x, powerup.y, size);
    context.save();
    context.translate(center.x, center.y);
    context.rotate(Math.PI / 4);
    const colors = {
      kick: { foreground: "#8cff9b", background: "#153f35", icon: "K" },
      fire: { foreground: "#ffd166", background: "#49351b", icon: "F" },
      bomb: { foreground: "#ff9ac1", background: "#492437", icon: "B" },
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

refreshRooms();
window.setInterval(refreshRooms, 5000);
