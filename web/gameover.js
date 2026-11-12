// The game-over modal: announces the winner over fireworks in their color.
const GameOver = (() => {
  const dialog = document.querySelector("#game-over");
  const canvas = document.querySelector("#fireworks");
  const context = canvas.getContext("2d");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let frame = 0;
  let rockets = [];
  let sparks = [];
  let palette = [];
  let nextLaunchAt = 0;

  function resize() {
    canvas.width = window.innerWidth * devicePixelRatio;
    canvas.height = window.innerHeight * devicePixelRatio;
    context.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
  }

  function launch() {
    const width = window.innerWidth;
    const height = window.innerHeight;
    rockets.push({
      x: width * (0.15 + Math.random() * 0.7),
      y: height + 10,
      vx: (Math.random() - 0.5) * 2,
      vy: -(height / 75 + Math.random() * 4),
      fuse: height * (0.15 + Math.random() * 0.35),
      color: palette[Math.floor(Math.random() * palette.length)],
    });
  }

  function burst(rocket) {
    const count = 70 + Math.floor(Math.random() * 40);
    const power = 3 + Math.random() * 3;
    for (let i = 0; i < count; i += 1) {
      const angle = (Math.PI * 2 * i) / count;
      const speed = power * (0.4 + Math.random() * 0.6);
      sparks.push({
        x: rocket.x,
        y: rocket.y,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed,
        life: 1,
        decay: 0.008 + Math.random() * 0.012,
        // Mostly the winner's color, with a few white-hot sparks.
        color: Math.random() < 0.2 ? "#fff6dc" : rocket.color,
      });
    }
  }

  function step(time) {
    // Fade the previous frame instead of clearing it, leaving light trails.
    context.globalCompositeOperation = "destination-out";
    context.fillStyle = "rgba(0, 0, 0, 0.22)";
    context.fillRect(0, 0, window.innerWidth, window.innerHeight);
    context.globalCompositeOperation = "lighter";

    if (time >= nextLaunchAt) {
      launch();
      nextLaunchAt = time + 350 + Math.random() * 600;
    }
    rockets = rockets.filter((rocket) => {
      rocket.x += rocket.vx;
      rocket.y += rocket.vy;
      rocket.vy += 0.12;
      context.fillStyle = rocket.color;
      context.fillRect(rocket.x - 1.5, rocket.y - 1.5, 3, 3);
      if (rocket.y <= rocket.fuse || rocket.vy >= 0) {
        burst(rocket);
        return false;
      }
      return true;
    });
    sparks = sparks.filter((spark) => {
      spark.x += spark.vx;
      spark.y += spark.vy;
      spark.vx *= 0.985;
      spark.vy = spark.vy * 0.985 + 0.05;
      spark.life -= spark.decay;
      if (spark.life <= 0) return false;
      context.globalAlpha = spark.life;
      context.fillStyle = spark.color;
      context.beginPath();
      context.arc(spark.x, spark.y, 2, 0, Math.PI * 2);
      context.fill();
      return true;
    });
    context.globalAlpha = 1;
    frame = requestAnimationFrame(step);
  }

  function stopFireworks() {
    cancelAnimationFrame(frame);
    frame = 0;
    rockets = [];
    sparks = [];
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, canvas.width, canvas.height);
  }

  function startFireworks(color) {
    stopFireworks();
    if (reducedMotion.matches) return;
    palette = [color, color, color, "#ffca62"];
    resize();
    nextLaunchAt = 0;
    frame = requestAnimationFrame(step);
  }

  // `winner` is null for a draw.
  function show({ winner, color, isYou, isSpectator, seconds, canRematch }) {
    dialog.classList.toggle("draw", !winner);
    dialog.style.setProperty("--winner-color", color || "#8a99b8");
    document.querySelector("#game-over-trophy").textContent = winner ? "🏆" : "🤝";
    document.querySelector("#game-over-title").textContent = winner
      ? isYou ? "You win!" : `${winner} wins!`
      : "Draw!";
    document.querySelector("#game-over-text").textContent = winner
      ? isYou
        ? "Last one standing. Nicely done!"
        : isSpectator ? "The last one standing takes the round." : "Better luck next round."
      : "Nobody survived the lightning.";
    document.querySelector("#game-over-time").textContent =
      seconds != null ? `Match length ${formatDuration(seconds)}` : "";
    document.querySelector("#game-over-rematch").classList.toggle("hidden", !canRematch);
    if (!dialog.open) dialog.showModal();
    // Focus the card, not a button, so a stray Space press does nothing.
    document.querySelector("#game-over-card").focus();
    if (winner) startFireworks(color);
    else stopFireworks();
  }

  function hide() {
    // Called on every state update outside "finished"; usually nothing to do.
    if (!dialog.open && !frame) return;
    stopFireworks();
    if (dialog.open) dialog.close();
  }

  dialog.addEventListener("close", stopFireworks);
  document.querySelector("#game-over-close").addEventListener("click", hide);
  window.addEventListener("resize", () => {
    if (frame) resize();
  });

  return { show, hide };
})();
