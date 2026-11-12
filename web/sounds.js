// Game sound effects, synthesized with the Web Audio API (no audio files).
// `Sounds.onState(previous, next, memberId)` compares two room states and
// plays a sound for each event it finds.
const Sounds = (() => {
  const STORAGE_KEY = "tesla-arena-muted";
  let audio;
  let master;
  let muted = false;
  try {
    muted = localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    // Storage can be blocked; sound just starts unmuted.
  }

  // Browsers only allow audio after a user gesture, so create it lazily.
  function ensureAudio() {
    if (!audio) {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (!AudioContext) return null;
      audio = new AudioContext();
      master = audio.createGain();
      master.gain.value = 0.45;
      master.connect(audio.destination);
    }
    if (audio.state === "suspended") audio.resume();
    return audio;
  }
  for (const type of ["pointerdown", "keydown"]) {
    window.addEventListener(type, ensureAudio, { passive: true });
  }

  function ready() {
    return !muted && audio && audio.state === "running";
  }

  // One oscillator note with a quick attack and exponential decay.
  function tone({ freq, to, duration = 0.15, type = "sine", volume = 0.3, delay = 0 }) {
    const start = audio.currentTime + delay;
    const oscillator = audio.createOscillator();
    const gain = audio.createGain();
    oscillator.type = type;
    oscillator.frequency.setValueAtTime(freq, start);
    if (to) oscillator.frequency.exponentialRampToValueAtTime(to, start + duration);
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(volume, start + 0.008);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    oscillator.connect(gain).connect(master);
    oscillator.start(start);
    oscillator.stop(start + duration + 0.02);
  }

  // Filtered white noise: crackle, crunch and clicks.
  function noise({ duration = 0.2, filter = "bandpass", freq = 2000, q = 1, volume = 0.3, delay = 0 }) {
    const start = audio.currentTime + delay;
    const length = Math.ceil(audio.sampleRate * duration);
    const buffer = audio.createBuffer(1, length, audio.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < length; i += 1) data[i] = Math.random() * 2 - 1;
    const source = audio.createBufferSource();
    source.buffer = buffer;
    const shaping = audio.createBiquadFilter();
    shaping.type = filter;
    shaping.frequency.value = freq;
    shaping.Q.value = q;
    const gain = audio.createGain();
    gain.gain.setValueAtTime(volume, start);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    source.connect(shaping).connect(gain).connect(master);
    source.start(start);
  }

  const effects = {
    countdown() {
      tone({ freq: 660, duration: 0.14, type: "square", volume: 0.12 });
    },
    go() {
      tone({ freq: 880, duration: 0.1, type: "square", volume: 0.12 });
      tone({ freq: 1320, duration: 0.3, type: "square", volume: 0.12, delay: 0.09 });
    },
    coil(own) {
      const volume = own ? 1 : 0.4;
      tone({ freq: 190, to: 90, duration: 0.18, volume: 0.35 * volume });
      tone({ freq: 1760, duration: 0.12, type: "triangle", volume: 0.05 * volume, delay: 0.02 });
    },
    lightning(count) {
      const volume = Math.min(1, 0.6 + 0.15 * count);
      noise({ duration: 0.45, freq: 2600, q: 0.8, volume: 0.5 * volume });
      tone({ freq: 1400, to: 60, duration: 0.35, type: "sawtooth", volume: 0.14 * volume });
      tone({ freq: 55, to: 35, duration: 0.5, volume: 0.4 * volume });
    },
    crate() {
      noise({ duration: 0.18, filter: "lowpass", freq: 700, volume: 0.35, delay: 0.04 });
    },
    kick() {
      noise({ duration: 0.04, filter: "highpass", freq: 3000, volume: 0.12 });
    },
    pickup() {
      [660, 880, 1320].forEach((freq, index) =>
        tone({ freq, duration: 0.12, type: "triangle", volume: 0.18, delay: index * 0.06 }));
    },
    hit(own) {
      const volume = own ? 1 : 0.45;
      tone({ freq: 420, to: 90, duration: 0.4, type: "sawtooth", volume: 0.16 * volume });
      noise({ duration: 0.25, filter: "lowpass", freq: 1200, volume: 0.25 * volume });
    },
    eliminated(own) {
      const volume = own ? 0.18 : 0.08;
      [523, 392, 262].forEach((freq, index) =>
        tone({ freq, duration: 0.22, type: "square", volume, delay: index * 0.16 }));
    },
    win() {
      [523, 659, 784, 1047].forEach((freq, index) =>
        tone({ freq, duration: index === 3 ? 0.5 : 0.16, type: "square", volume: 0.12, delay: index * 0.13 }));
    },
    alarm() {
      [0, 0.32, 0.64].forEach((delay) =>
        tone({ freq: 520, to: 980, duration: 0.28, type: "sawtooth", volume: 0.12, delay }));
    },
    lose() {
      [392, 370, 349, 262].forEach((freq, index) =>
        tone({ freq, duration: index === 3 ? 0.6 : 0.22, type: "triangle", volume: 0.2, delay: index * 0.2 }));
    },
  };

  const lightningKey = (effect) => `${effect.center}|${effect.cells.join(";")}`;

  function gainedPowerup(before, after) {
    return after.coil_capacity > before.coil_capacity
      || after.coil_range > before.coil_range
      || (after.can_kick && !before.can_kick)
      || (after.has_speed_up && !before.has_speed_up)
      || (after.has_remote && !before.has_remote);
  }

  function onState(previous, next, memberId) {
    if (!previous || !next || !ready()) return;

    if (next.phase === "countdown" && next.countdown_seconds !== previous.countdown_seconds) {
      effects.countdown();
    }
    if (previous.phase === "countdown" && next.phase === "playing") effects.go();

    const timer = next.timer;
    const oldTimer = previous.timer;
    if (timer?.stage === "closing" && oldTimer?.stage === "match") effects.alarm();
    else if (
      timer?.stage === "match" && next.phase === "playing"
      && timer.seconds <= 5 && timer.seconds !== oldTimer?.seconds
    ) effects.countdown();

    const oldCoils = new Map(previous.coils.map((coil) => [coil.id, coil]));
    const ownCoilsBefore = previous.players.find((player) => player.id === memberId)?.coil_slots;
    let newCoil = false;
    let kicked = false;
    for (const coil of next.coils) {
      const old = oldCoils.get(coil.id);
      if (!old) newCoil = true;
      else if (old.x !== coil.x || old.y !== coil.y) kicked = true;
    }
    if (newCoil) {
      const ownAfter = next.players.find((player) => player.id === memberId)?.coil_slots;
      effects.coil(ownCoilsBefore !== undefined && ownAfter < ownCoilsBefore);
    }
    if (kicked) effects.kick();

    const oldLightning = new Set((previous.lightning || []).map(lightningKey));
    const strikes = (next.lightning || []).filter((effect) => !oldLightning.has(lightningKey(effect)));
    if (strikes.length) effects.lightning(strikes.length);
    if (next.soft_blocks_remaining < previous.soft_blocks_remaining) effects.crate();

    const oldPlayers = new Map(previous.players.map((player) => [player.id, player]));
    for (const player of next.players) {
      const old = oldPlayers.get(player.id);
      if (!old || player.left) continue;
      const own = player.id === memberId;
      if (old.alive && !player.alive) effects.eliminated(own);
      else if (player.lives < old.lives) effects.hit(own);
      if (own && player.alive && gainedPowerup(old, player)) effects.pickup();
    }

    if (previous.phase !== "finished" && next.phase === "finished") {
      const spectating = !next.players.some((player) => player.id === memberId);
      if (next.winner_id === memberId || (spectating && next.winner_id)) effects.win();
      else effects.lose();
    }
  }

  function setMuted(value) {
    muted = value;
    try {
      localStorage.setItem(STORAGE_KEY, muted ? "1" : "0");
    } catch {
      // Not remembered across visits, but still muted for now.
    }
  }

  return {
    onState,
    isMuted: () => muted,
    setMuted,
  };
})();
