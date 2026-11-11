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
      item.textContent = `${room.id} · ${room.players}/${room.max_players} spelare`;
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
      body: JSON.stringify({ private: document.querySelector("#private-room").checked }),
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
  const playerList = document.querySelector("#players");
  playerList.replaceChildren();
  for (const player of gameState.players) {
    const item = document.createElement("li");
    item.textContent = `${player.name} · ${player.alive ? `${player.lives} liv · ${player.coil_slots} lediga coils` : "ute"}`;
    if (!player.alive) item.style.color = "#78869e";
    playerList.append(item);
  }
  if (gameState.winner_id) {
    const winner = gameState.players.find((player) => player.id === gameState.winner_id);
    status.textContent = `${winner.name} vann matchen!`;
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
