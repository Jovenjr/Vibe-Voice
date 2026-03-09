/**
 * app.js — Vibe Voice
 *
 * PROTOCOLO: usa msg.event (no msg.type) — compatible con el servidor original.
 *
 * CAMBIOS vs versión anterior:
 * - Web Worker inline para el ping loop → inmune al throttling de Chrome en pestañas inactivas
 * - Page Visibility API → fuerza get_state al volver al frente
 * - window.focus handler → idem al recuperar foco
 * - Reconexión con exponential backoff + jitter
 * - Indicador de latencia WebSocket en topbar
 * - Heartbeat del servidor actualiza indicadores de estado
 * - Botón "Detener audio" envía tts_stop
 * - Auto-trim: máximo 500 mensajes en DOM
 * - get_state devuelve tts_voice_index y llm_enabled → sincroniza UI al reconectar
 */

const WS_URL_DEFAULT = "ws://localhost:8765";
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const MAX_MESSAGES_DOM = 500;
const PING_INTERVAL_MS = 4000;

// ─── Web Worker inline ────────────────────────────────────────────────────────
// Corre en su propio thread → no se ve afectado por el throttling de Chrome
// que reduce setInterval a 1Hz en pestañas en segundo plano.
const WORKER_SRC = `
let iv = null;
self.onmessage = function(e) {
  if (e.data === 'start') {
    clearInterval(iv);
    iv = setInterval(() => self.postMessage('tick'), ${PING_INTERVAL_MS});
  } else if (e.data === 'stop') {
    clearInterval(iv);
  }
};
`;

function createPingWorker(onTick) {
  try {
    const blob = new Blob([WORKER_SRC], { type: "application/javascript" });
    const worker = new Worker(URL.createObjectURL(blob));
    worker.onmessage = onTick;
    worker.postMessage('start');
    return worker;
  } catch (e) {
    console.warn("[app] Web Worker no disponible, usando setInterval:", e);
    return null;
  }
}

// ─── Clase principal ──────────────────────────────────────────────────────────

class VibeVoiceViewer {
  constructor() {
    this.ws = null;
    this.reconnectAttempts = 0;
    this.reconnectTimer = null;
    this.pingWorker = null;
    this.pingFallback = null;
    this.isConnected = false;
    this.pingsSent = 0;
    this.currentRequestIndex = -1;

    // Cola de audio para modo Docker (TTS server-side)
    this._audioQueue = [];
    this._isPlayingAudio = false;
    this._currentAudio = null;

    // Historial
    this._selectedSessionId = null;

    this.prefs = this._loadPrefs();

    // DOM refs
    this.$messages    = document.getElementById("messages");
    this.$statusDot   = document.getElementById("status-dot");
    this.$statusText  = document.getElementById("status-text");
    this.$latency     = document.getElementById("latency");
    this.$activeFile  = document.getElementById("active-file");
    this.$watching    = document.getElementById("watching-count");

    this._applyPrefs();
    this._applyTheme();
    this._bindUI();
    this._initSettingsModal();
    this._connect();
    this._setupVisibility();
    this._startPingLoop();
  }

  // ─── Theme ───────────────────────────────────────────────────────────────────

  _applyTheme() {
    const theme = this.prefs.theme || "dark";
    document.documentElement.setAttribute("data-theme", theme);
    this._updateThemeButton(theme);
  }

  _toggleTheme() {
    const current = this.prefs.theme || "dark";
    const next = current === "dark" ? "light" : "dark";
    this.prefs.theme = next;
    this._savePrefs();
    document.documentElement.setAttribute("data-theme", next);
    this._updateThemeButton(next);
  }

  _updateThemeButton(theme) {
    const btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.textContent = theme === "dark" ? "🌙" : "☀️";
      btn.title = theme === "dark" ? "Cambiar a tema claro" : "Cambiar a tema oscuro";
    }
  }

  // ─── WebSocket ─────────────────────────────────────────────────────────────

  _connect() {
    const url = this.prefs.wsUrl || WS_URL_DEFAULT;
    this._setStatus("connecting", `Conectando a ${url}…`);

    try {
      this.ws = new WebSocket(url);
    } catch (e) {
      this._scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.isConnected = true;
      this.reconnectAttempts = 0;
      this._setStatus("connected", "Conectado");
      // get_state no necesario: register() en servidor ya envía el estado
      this._send({ action: "tts_get_voices" });
      this._send({ action: "tts_telegram_status" });
      this._send({ action: "telegram_input_status" });
      
      // Sincronizar TODAS las preferencias con el servidor
      this._send({ action: "tts_enable", enabled: this.prefs.ttsEnabled });
      this._send({ action: "tts_llm_enable", enabled: this.prefs.ttsLlmEnabled });
      this._send({ action: "tts_telegram_enable", enabled: this.prefs.ttsTelegram || false });
      this._send({ action: "telegram_input_enable", enabled: this.prefs.telegramInput || false });
      this._send({ action: "set_ide", ide: this.prefs.ideFilter });
      this._send({ action: "set_include_thinking", enabled: this.prefs.includeThinking });
      this._send({ action: "set_include_codex_progress", enabled: this.prefs.includeCodexProgress });
      
      // Cargar historial de base de datos
      this._send({ action: "db_get_sessions", limit: 20 });
      this._send({ action: "db_stats" });
      
      this._toast("✓ Conectado al servidor", "success");
    };

    this.ws.onmessage = (e) => {
      try {
        this._handle(JSON.parse(e.data));
      } catch (err) {
        console.error("[app] Error parseando mensaje:", err);
      }
    };

    this.ws.onclose = (ev) => {
      this.isConnected = false;
      const reason = ev.wasClean ? "cerrado" : `código ${ev.code}`;
      this._setStatus("disconnected", `Desconectado (${reason})`);
      this._scheduleReconnect();
    };

    this.ws.onerror = () => { /* onclose se llama después */ };
  }

  _scheduleReconnect() {
    if (this.reconnectTimer) return;
    const delay = Math.min(
      RECONNECT_BASE_MS * Math.pow(1.5, this.reconnectAttempts) + Math.random() * 500,
      RECONNECT_MAX_MS
    );
    this.reconnectAttempts++;
    this._setStatus("reconnecting", `Reconectando en ${(delay/1000).toFixed(1)}s… (intento ${this.reconnectAttempts})`);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this._connect();
    }, delay);
  }

  _send(obj) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  // ─── Ping loop (Web Worker) ────────────────────────────────────────────────

  _startPingLoop() {
    const onTick = () => {
      if (!this.isConnected) return;
      this._send({ action: "ping", _t: Date.now() });
    };
    this.pingWorker = createPingWorker(onTick);
    if (!this.pingWorker) {
      this.pingFallback = setInterval(onTick, PING_INTERVAL_MS);
    }
  }

  // ─── Page Visibility API ───────────────────────────────────────────────────

  _setupVisibility() {
    // Cuando la pestaña vuelve al frente, forzar refresh de datos
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        if (this.isConnected) {
          this._send({ action: "get_state" });
        } else {
          // Reconectar inmediatamente en lugar de esperar el timer
          if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
          }
          this._connect();
        }
      }
    });

    // También al recuperar foco de ventana
    window.addEventListener("focus", () => {
      if (this.isConnected) {
        this._send({ action: "get_state" });
      }
    });
  }

  // ─── Manejador de mensajes ─────────────────────────────────────────────────

  _handle(msg) {
    // El servidor usa msg.event (no msg.type)
    const ev = msg.event;

    switch (ev) {

      case "pong": {
        if (msg._t) {
          const latency = Date.now() - msg._t;
          if (this.$latency) this.$latency.textContent = `${latency}ms`;
        }
        break;
      }

      case "heartbeat": {
        if (this.$watching)   this.$watching.textContent   = msg.watching   ?? "–";
        if (this.$activeFile) this.$activeFile.textContent = msg.active_file || "ninguno";
        break;
      }

      case "session_state": {
        if (this.$watching)   this.$watching.textContent   = "–";
        if (this.$activeFile) this.$activeFile.textContent = msg.file ? msg.file.split(/[/\\]/).pop() : "ninguno";
        // Sincronizar UI de TTS con estado del servidor
        if (msg.tts_enabled    !== undefined) this._setToggle("tts-enabled",     msg.tts_enabled);
        if (msg.tts_voice_index !== undefined) this._setSelect("tts-voice",      msg.tts_voice_index);
        if (msg.llm_enabled    !== undefined) this._setToggle("tts-llm-enabled", msg.llm_enabled);
        break;
      }

      case "no_session": {
        this._appendSystem("⚠️ No hay sesiones de chat disponibles.");
        break;
      }

      case "session_changed": {
        this._appendSystem(`📁 Sesión: ${msg.session_id || "nueva"}`);
        break;
      }

      case "session_init": {
        this._appendSystem(`🔄 Sesión inicializada (${msg.request_count} requests)`);
        break;
      }

      case "history_message": {
        // Historial: no disparar TTS (debounce lo maneja el servidor)
        this._appendMessage(msg.role, msg.text, msg.timestamp);
        break;
      }

      case "history_complete": {
        this._appendSystem(`✓ Historial cargado (${msg.total_messages} mensajes)`);
        break;
      }

      case "user_message": {
        this._clearUserDraft();
        this._appendMessage("user", msg.text, msg.timestamp);
        break;
      }

      case "user_draft": {
        this._renderUserDraft(msg.text || "", msg.timestamp, !!msg.cleared);
        break;
      }

      case "response_chunk": {
        const idx  = msg.request_index ?? -1;
        const text = msg.text || "";
        const isNew = idx !== this.currentRequestIndex;
        if (isNew) {
          this.currentRequestIndex = idx;
          this._appendMessage("assistant", text, msg.timestamp, true);
        } else {
          this._appendToLastAssistant(text);
        }
        break;
      }

      case "title_changed": {
        if (msg.title) document.title = `Copilot: ${msg.title}`;
        break;
      }

      // TTS responses
      case "tts_status":
        this._setToggle("tts-enabled", msg.enabled);
        break;
      case "tts_rate_set":
        if (msg.rate !== undefined) this._setSlider("tts-rate", msg.rate);
        break;
      case "tts_voice_set":
        if (msg.voice_index !== undefined) this._setSelect("tts-voice", msg.voice_index);
        if (msg.index       !== undefined) this._setSelect("tts-voice", msg.index);
        break;
      case "tts_voices":
        this._populateVoices(msg.voices || []);
        this._populateSettingsVoices(msg.voices || []);
        break;
      case "tts_llm_status":
        this._setToggle("tts-llm-enabled", msg.enabled);
        break;
      case "tts_telegram_status":
        this._setToggle("tts-telegram", msg.enabled);
        this._updateTelegramStatus(msg);
        break;
      case "telegram_input_status":
        this._setToggle("telegram-input", msg.enabled);
        this._updateTelegramInputStatus(msg);
        break;
      case "telegram_input_received":
        this._showToast(`📱 Telegram: ${msg.text}`, "info");
        break;
      case "tts_stopped":
        this._updateAudioStatus("Detenido");
        this._resetAudioButtons();
        break;
      case "tts_paused":
        this._updateAudioStatus("Pausado");
        break;
      case "tts_resumed":
        this._updateAudioStatus("Reproduciendo");
        break;
      case "tts_skipped":
        this._updateAudioStatus("Saltado");
        break;
      case "tts_playback_status":
        this._updateAudioStatus(msg.playing ? `Reproduciendo (${msg.queue_size} en cola)` : "Listo");
        break;
      case "tts_audio":
        // Audio generado en el servidor (modo Docker)
        this._playServerAudio(msg.url);
        break;

      case "ide_changed":
        this._appendSystem(`🔄 IDE: ${msg.name}`);
        this._toast(`IDE cambiado a ${msg.name}`, "info");
        // Actualizar el selector si el cambio vino de otro cliente
        const sel = document.getElementById("ide-selector");
        if (sel && sel.value !== msg.ide) {
          sel.value = msg.ide;
          this._updateIdeHint(msg.ide);
        }
        break;

      case "include_codex_progress_set":
        this.prefs.includeCodexProgress = !!msg.enabled;
        this._savePrefs();
        break;

      case "ides_list":
        // Recibimos la lista de IDEs disponibles (no usado actualmente, IDEs son estáticos)
        break;

      // ═══════════════════════════════════════════════════════════════════════
      // BASE DE DATOS / HISTORIAL
      // ═══════════════════════════════════════════════════════════════════════
      
      case "db_sessions":
        this._renderSessions(msg.sessions);
        break;
      
      case "db_messages":
        this._loadMessagesFromDb(msg.messages);
        break;
      
      case "db_search_results":
        this._renderSearchResults(msg.messages, msg.query);
        break;
      
      case "db_export":
        this._handleExport(msg);
        break;
      
      case "db_stats":
        this._renderDbStats(msg);
        break;
      
      case "db_session_deleted":
        this._toast("Sesión eliminada", "success");
        this._loadDbSessions();
        break;
      
      case "db_setting_saved":
        // Silencioso - no mostrar toast por cada setting
        break;
      
      case "db_settings":
        this._applyDbSettings(msg.settings || {});
        break;

      case "error":
        console.error("[servidor]", msg.message);
        break;
    }
  }

  // ─── Audio del servidor (modo Docker) ─────────────────────────────────────

  _playServerAudio(url) {
    // Encolar audio para reproducción secuencial
    this._audioQueue.push(url);
    if (!this._isPlayingAudio) {
      this._processAudioQueue();
    }
  }

  _processAudioQueue() {
    if (this._audioQueue.length === 0) {
      this._isPlayingAudio = false;
      return;
    }

    this._isPlayingAudio = true;
    const url = this._audioQueue.shift();
    
    // Construir URL completa
    const fullUrl = url.startsWith("http") ? url : `${window.location.origin}${url}`;
    
    this._currentAudio = new Audio(fullUrl);
    this._currentAudio.onended = () => {
      this._currentAudio = null;
      this._processAudioQueue();
    };
    this._currentAudio.onerror = (e) => {
      console.error("[audio] Error reproduciendo:", e);
      this._currentAudio = null;
      this._processAudioQueue();
    };
    this._currentAudio.play().catch(e => {
      console.error("[audio] No se pudo reproducir:", e);
      this._currentAudio = null;
      this._processAudioQueue();
    });
  }

  _stopServerAudio() {
    if (this._currentAudio) {
      this._currentAudio.pause();
      this._currentAudio = null;
    }
    this._audioQueue = [];
    this._isPlayingAudio = false;
  }

  _updateAudioStatus(status) {
    const el = document.getElementById("audio-status");
    if (el) el.textContent = status;
  }

  _resetAudioButtons() {
    document.getElementById("tts-pause")?.classList.remove("active");
    document.getElementById("tts-resume")?.classList.remove("active");
  }

  _updateTelegramStatus(msg) {
    const el = document.getElementById("telegram-status");
    if (!el) return;
    if (!msg.configured) {
      el.textContent = "No configurado (ver .env)";
      el.style.color = "var(--yellow)";
    } else if (msg.enabled) {
      el.textContent = "Enviando audios";
      el.style.color = "var(--green)";
    } else {
      el.textContent = "Desactivado";
      el.style.color = "var(--muted)";
    }
  }

  _updateTelegramInputStatus(msg) {
    const el = document.getElementById("telegram-input-status");
    if (!el) return;
    if (!msg.configured) {
      el.textContent = "No configurado (ver .env)";
      el.style.color = "var(--yellow)";
    } else if (!msg.deps_available) {
      el.textContent = "Instalar: pip install pyautogui pyperclip";
      el.style.color = "var(--red)";
    } else if (msg.enabled) {
      el.textContent = "Escuchando mensajes";
      el.style.color = "var(--green)";
    } else {
      el.textContent = "Desactivado";
      el.style.color = "var(--muted)";
    }
  }

  // ─── Renderizado de mensajes ───────────────────────────────────────────────

  _appendMessage(role, text, timestamp, streaming = false) {
    this._trimDOM();

    const el   = document.createElement("div");
    el.className = `message ${role}${streaming ? " streaming" : ""}`;

    const header = document.createElement("div");
    header.className = "message-header";
    header.textContent = role === "user" ? "Tú" : "Copilot";

    if (this.prefs.showTimestamps && timestamp) {
      const ts = document.createElement("span");
      ts.className = "timestamp";
      ts.textContent = " · " + new Date(timestamp).toLocaleTimeString("es", {
        hour: "2-digit", minute: "2-digit", second: "2-digit"
      });
      header.appendChild(ts);
    }

    const body = document.createElement("div");
    body.className = "message-body";
    body.textContent = text;

    el.appendChild(header);
    el.appendChild(body);
    this.$messages.appendChild(el);

    if (this.prefs.autoScroll) this._scrollBottom();
    return el;
  }

  _renderUserDraft(text, timestamp, cleared = false) {
    const existing = this.$messages.querySelector(".message.user.draft");
    const cleanText = (text || "").trim();

    if (cleared || !cleanText) {
      if (existing) existing.remove();
      return;
    }

    if (existing) {
      existing.querySelector(".message-body").textContent = text;
      if (this.prefs.autoScroll) this._scrollBottom();
      return;
    }

    this._trimDOM();
    const el = this._appendMessage("user", text, timestamp, false);
    el.classList.add("draft");
    const header = el.querySelector(".message-header");
    if (header) header.textContent = "Tú escribiendo…";
  }

  _clearUserDraft() {
    const existing = this.$messages.querySelector(".message.user.draft");
    if (existing) existing.remove();
  }

  _appendToLastAssistant(text) {
    const all  = this.$messages.querySelectorAll(".message.assistant");
    const last = all[all.length - 1];
    if (last) {
      last.querySelector(".message-body").textContent += text;
      if (this.prefs.autoScroll) this._scrollBottom();
    } else {
      this._appendMessage("assistant", text);
    }
  }

  _appendSystem(text) {
    const el = document.createElement("div");
    el.className = "message system";
    el.textContent = text;
    this.$messages.appendChild(el);
    if (this.prefs.autoScroll) this._scrollBottom();
  }

  _trimDOM() {
    while (this.$messages.children.length >= MAX_MESSAGES_DOM) {
      this.$messages.removeChild(this.$messages.firstChild);
    }
  }

  _scrollBottom() {
    requestAnimationFrame(() => {
      this.$messages.scrollTop = this.$messages.scrollHeight;
    });
  }

  // ─── Indicadores de estado ─────────────────────────────────────────────────

  _setStatus(state, text) {
    if (this.$statusDot)  this.$statusDot.className = `status-dot ${state}`;
    if (this.$statusText) this.$statusText.textContent = text;
    console.log(`[estado] ${text}`);
  }

  _toast(text, type = "info") {
    const c = document.getElementById("toast-container");
    if (!c) return;
    const t = document.createElement("div");
    t.className = `toast toast-${type}`;
    t.textContent = text;
    c.appendChild(t);
    requestAnimationFrame(() => t.classList.add("visible"));
    setTimeout(() => {
      t.classList.remove("visible");
      setTimeout(() => t.remove(), 300);
    }, 2500);
  }

  // ─── Controles TTS ─────────────────────────────────────────────────────────

  _populateVoices(voices) {
    const sel = document.getElementById("tts-voice");
    if (!sel) return;
    sel.innerHTML = "";
    voices.forEach((v) => {
      const opt = document.createElement("option");
      opt.value = v.index;
      opt.textContent = v.name || `Voz ${v.index + 1}`;
      sel.appendChild(opt);
    });
    if (this.prefs.ttsVoiceIndex != null) sel.value = this.prefs.ttsVoiceIndex;
  }

  _setToggle(id, value) {
    const el = document.getElementById(id);
    if (el) el.checked = !!value;
  }

  _setSlider(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value;
    const lbl = document.getElementById(`${id}-label`);
    if (lbl) lbl.textContent = `${parseFloat(value).toFixed(1)}x`;
  }

  _setSelect(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value;
  }

  // ─── Binding UI ────────────────────────────────────────────────────────────

  _on(id, event, fn) {
    const el = document.getElementById(id);
    if (el) el.addEventListener(event, fn);
  }

  _bindUI() {
    this._on("tts-enabled", "change", (e) => {
      this.prefs.ttsEnabled = e.target.checked;
      this._savePrefs();
      this._send({ action: "tts_enable", enabled: e.target.checked });
    });

    this._on("tts-rate", "input", (e) => {
      const rate = parseFloat(e.target.value);
      this.prefs.ttsRate = rate;
      this._savePrefs();
      this._send({ action: "tts_set_rate", rate });
      const lbl = document.getElementById("tts-rate-label");
      if (lbl) lbl.textContent = `${rate.toFixed(1)}x`;
    });

    this._on("tts-voice", "change", (e) => {
      const idx = parseInt(e.target.value, 10);
      this.prefs.ttsVoiceIndex = idx;
      this._savePrefs();
      this._send({ action: "tts_set_voice", voice_index: idx });
    });

    this._on("tts-llm-enabled", "change", (e) => {
      this.prefs.ttsLlmEnabled = e.target.checked;
      this._savePrefs();
      this._send({ action: "tts_llm_enable", enabled: e.target.checked });
    });

    this._on("tts-telegram", "change", (e) => {
      this.prefs.ttsTelegram = e.target.checked;
      this._savePrefs();
      this._send({ action: "tts_telegram_enable", enabled: e.target.checked });
    });

    this._on("telegram-input", "change", (e) => {
      this.prefs.telegramInput = e.target.checked;
      this._savePrefs();
      this._send({ action: "telegram_input_enable", enabled: e.target.checked });
    });

    this._on("tts-stop", "click", () => {
      this._send({ action: "tts_stop" });
      this._stopServerAudio();  // También detener audio del cliente (Docker mode)
      this._updateAudioStatus("Detenido");
    });

    this._on("tts-pause", "click", () => {
      this._send({ action: "tts_pause" });
      this._updateAudioStatus("Pausado");
      document.getElementById("tts-pause").classList.add("active");
      document.getElementById("tts-resume").classList.remove("active");
    });

    this._on("tts-resume", "click", () => {
      this._send({ action: "tts_resume" });
      this._updateAudioStatus("Reproduciendo");
      document.getElementById("tts-resume").classList.add("active");
      document.getElementById("tts-pause").classList.remove("active");
    });

    this._on("tts-skip", "click", () => {
      this._send({ action: "tts_skip" });
      this._updateAudioStatus("Saltando...");
    });

    this._on("auto-scroll", "change", (e) => {
      this.prefs.autoScroll = e.target.checked;
      this._savePrefs();
    });

    this._on("show-timestamps", "change", (e) => {
      this.prefs.showTimestamps = e.target.checked;
      this._savePrefs();
    });

    this._on("clear-messages", "click", () => {
      this.$messages.innerHTML = "";
    });

    this._on("ws-url", "change", (e) => {
      this.prefs.wsUrl = e.target.value;
      this._savePrefs();
      if (this.ws) this.ws.close();
    });

    // Selector de IDE
    this._on("ide-selector", "change", (e) => {
      const ide = e.target.value;
      this.prefs.ideFilter = ide;
      this._savePrefs();
      this._send({ action: "set_ide", ide });
      this._updateIdeHint(ide);
    });

    // Toggle incluir razonamiento (Cursor)
    this._on("include-thinking", "change", (e) => {
      this.prefs.includeThinking = e.target.checked;
      this._savePrefs();
      this._send({ action: "set_include_thinking", enabled: e.target.checked });
    });

    this._on("reconnect-btn", "click", () => {
      if (this.reconnectTimer) {
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
      }
      if (this.ws) this.ws.close();
      this.reconnectAttempts = 0;
      this._connect();
    });

    // Toggle de tema
    this._on("theme-toggle", "click", () => this._toggleTheme());

    // Forzar actualización - re-escanea archivos inmediatamente
    this._on("force-refresh-btn", "click", () => {
      if (this.isConnected) {
        this._send({ action: "force_refresh" });
        this._toast("Forzando actualización...", "info");
      } else {
        this._toast("No conectado", "warn");
      }
    });

    // ═══════════════════════════════════════════════════════════════════════════
    // HISTORIAL / BASE DE DATOS
    // ═══════════════════════════════════════════════════════════════════════════

    // Buscar en historial
    this._on("history-search", "keyup", (e) => {
      if (e.key === "Enter") {
        this._searchHistory();
      }
    });
    this._on("search-btn", "click", () => this._searchHistory());

    // Exportar
    this._on("export-md", "click", () => this._exportSession("markdown"));
    this._on("export-json", "click", () => this._exportSession("json"));

    // Cargar sesión seleccionada
    this._on("load-session", "click", () => this._loadSelectedSession());

    // Cargar sesiones al conectar
    this._loadDbSessions();
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // HISTORIAL - Métodos
  // ═══════════════════════════════════════════════════════════════════════════

  _loadDbSessions() {
    if (this.isConnected) {
      this._send({ action: "db_get_sessions", limit: 20 });
      this._send({ action: "db_stats" });
    }
  }

  _searchHistory() {
    const input = document.getElementById("history-search");
    const query = input?.value?.trim();
    if (query) {
      this._send({ action: "db_search", query, limit: 30 });
    }
  }

  _exportSession(format) {
    const sessionId = this._selectedSessionId;
    if (!sessionId) {
      this._toast("Selecciona una sesión primero", "info");
      return;
    }
    this._send({ action: "db_export_session", session_id: sessionId, format });
  }

  _loadSelectedSession() {
    const sessionId = this._selectedSessionId;
    if (!sessionId) {
      this._toast("Selecciona una sesión primero", "info");
      return;
    }
    this._send({ action: "db_get_messages", session_id: sessionId, limit: 200 });
    this._toast("Cargando sesión...", "info");
  }

  _renderSessions(sessions) {
    const list = document.getElementById("sessions-list");
    if (!list) return;
    
    if (!sessions || sessions.length === 0) {
      list.innerHTML = '<p class="hint">Sin sesiones guardadas</p>';
      return;
    }

    list.innerHTML = sessions.map(s => `
      <div class="session-item" data-id="${s.id}">
        <div class="session-name">${this._escapeHtml(s.name || "Sin nombre")}</div>
        <div class="session-meta">
          <span>${s.ide || "?"}</span>
          <span>${s.message_count} msgs</span>
          <span>${this._formatDate(s.updated_at)}</span>
        </div>
      </div>
    `).join("");

    // Bind click events
    list.querySelectorAll(".session-item").forEach(el => {
      el.addEventListener("click", () => {
        list.querySelectorAll(".session-item").forEach(e => e.classList.remove("selected"));
        el.classList.add("selected");
        this._selectedSessionId = parseInt(el.dataset.id);
      });
    });
  }

  _renderDbStats(stats) {
    const el = document.getElementById("db-stats-text");
    if (el) {
      el.textContent = `Sesiones: ${stats.total_sessions || 0} | Mensajes: ${stats.total_messages || 0}`;
    }
  }

  _renderSearchResults(results, query) {
    if (!results || results.length === 0) {
      this._toast(`Sin resultados para "${query}"`, "info");
      return;
    }
    
    // Mostrar resultados en mensajes
    this.$messages.innerHTML = "";
    this._appendSystem(`🔍 Resultados para: "${query}" (${results.length} encontrados)`);
    
    results.forEach(m => {
      this._appendMessage(m.role, m.text, m.created_at);
    });
  }

  _handleExport(data) {
    const { format, content, session_id } = data;
    if (!content) {
      this._toast("Error exportando", "error");
      return;
    }

    // Descargar archivo
    const ext = format === "json" ? "json" : "md";
    const mime = format === "json" ? "application/json" : "text/markdown";
    const filename = `session_${session_id}.${ext}`;
    
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    
    this._toast(`Exportado: ${filename}`, "success");
  }

  _loadMessagesFromDb(messages) {
    if (!messages || messages.length === 0) {
      this._toast("Sesión vacía", "info");
      return;
    }
    
    // Limpiar y cargar mensajes
    this.$messages.innerHTML = "";
    this._appendSystem(`📂 Sesión cargada (${messages.length} mensajes)`);
    
    messages.forEach(m => {
      const el = this._appendMessage(m.role, m.text, m.created_at);
      
      // Si tiene razonamiento, agregarlo
      if (m.has_thinking && m.thinking_text && el) {
        const thinking = document.createElement("details");
        thinking.className = "thinking-block";
        thinking.innerHTML = `
          <summary style="font-size:10px;color:var(--muted);cursor:pointer;">💭 Razonamiento</summary>
          <pre style="font-size:11px;color:var(--muted);margin-top:4px;white-space:pre-wrap;">${this._escapeHtml(m.thinking_text)}</pre>
        `;
        el.appendChild(thinking);
      }
    });
    
    this._scrollBottom();
  }

  _formatDate(isoString) {
    if (!isoString) return "?";
    try {
      const d = new Date(isoString);
      return d.toLocaleDateString("es", { day: "2-digit", month: "short" });
    } catch {
      return "?";
    }
  }

  _escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // MODAL DE CONFIGURACIÓN
  // ═══════════════════════════════════════════════════════════════════════════

  _initSettingsModal() {
    const modal = document.getElementById("settings-modal");
    if (!modal) return;

    // Abrir modal
    this._on("settings-btn", "click", () => this._openSettings());
    
    // Cerrar modal
    this._on("close-settings", "click", () => this._closeSettings());
    this._on("cancel-settings", "click", () => this._closeSettings());
    modal.querySelector(".modal-backdrop")?.addEventListener("click", () => this._closeSettings());
    
    // Guardar
    this._on("save-settings", "click", () => this._saveSettings());
    
    // Tabs
    modal.querySelectorAll(".tab-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        modal.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
        modal.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
        btn.classList.add("active");
        const tabId = `tab-${btn.dataset.tab}`;
        document.getElementById(tabId)?.classList.add("active");
      });
    });

    // Preview de voz
    this._on("preview-voice", "click", () => this._previewVoice());
    
    // Rate slider label
    this._on("settings-tts-rate", "input", (e) => {
      const lbl = document.getElementById("settings-rate-label");
      if (lbl) lbl.textContent = `${parseFloat(e.target.value).toFixed(1)}x`;
    });

    // Mostrar/ocultar grupo de modelo Whisper según proveedor
    this._on("settings-stt-provider", "change", (e) => {
      const whisperGroup = document.getElementById("whisper-model-group");
      if (whisperGroup) {
        whisperGroup.style.display = e.target.value === "whisper_local" ? "block" : "none";
      }
    });
  }

  _openSettings() {
    const modal = document.getElementById("settings-modal");
    if (!modal) return;
    
    // Cargar valores actuales
    this._loadSettingsToModal();
    
    // Cargar voces disponibles
    this._send({ action: "tts_get_voices" });
    
    // Cargar settings de DB
    this._send({ action: "db_get_settings" });
    
    modal.classList.remove("hidden");
  }

  _closeSettings() {
    const modal = document.getElementById("settings-modal");
    if (modal) modal.classList.add("hidden");
  }

  _loadSettingsToModal() {
    const p = this.prefs;
    
    // General
    const setVal = (id, val) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (el.type === "checkbox") el.checked = !!val;
      else el.value = val ?? "";
    };
    
    setVal("settings-ws-url", p.wsUrl || WS_URL_DEFAULT);
    setVal("settings-ide", p.ideFilter || "all");
    setVal("settings-thinking", p.includeThinking);
    setVal("settings-codex-progress", p.includeCodexProgress);
    
    // TTS
    setVal("settings-tts-enabled", p.ttsEnabled);
    setVal("settings-llm-enabled", p.ttsLlmEnabled);
    setVal("settings-tts-rate", p.ttsRate || 1.0);
    const rateLbl = document.getElementById("settings-rate-label");
    if (rateLbl) rateLbl.textContent = `${(p.ttsRate || 1.0).toFixed(1)}x`;
    
    // Telegram
    setVal("settings-telegram-send", p.ttsTelegram);
    setVal("settings-telegram-receive", p.telegramInput);
  }

  _saveSettings() {
    const getVal = (id) => {
      const el = document.getElementById(id);
      if (!el) return undefined;
      if (el.type === "checkbox") return el.checked;
      return el.value;
    };
    
    // Actualizar preferencias locales
    this.prefs.wsUrl = getVal("settings-ws-url");
    this.prefs.ideFilter = getVal("settings-ide");
    this.prefs.includeThinking = getVal("settings-thinking");
    this.prefs.includeCodexProgress = getVal("settings-codex-progress");
    this.prefs.ttsEnabled = getVal("settings-tts-enabled");
    this.prefs.ttsLlmEnabled = getVal("settings-llm-enabled");
    this.prefs.ttsRate = parseFloat(getVal("settings-tts-rate")) || 1.0;
    this.prefs.ttsTelegram = getVal("settings-telegram-send");
    this.prefs.telegramInput = getVal("settings-telegram-receive");
    
    const voiceSelect = document.getElementById("settings-tts-voice");
    if (voiceSelect && voiceSelect.value) {
      this.prefs.ttsVoiceIndex = parseInt(voiceSelect.value);
    }
    
    this._savePrefs();
    
    // Enviar al servidor
    this._send({ action: "tts_enable", enabled: this.prefs.ttsEnabled });
    this._send({ action: "tts_llm_enable", enabled: this.prefs.ttsLlmEnabled });
    this._send({ action: "tts_set_rate", rate: this.prefs.ttsRate });
    this._send({ action: "tts_telegram_enable", enabled: this.prefs.ttsTelegram });
    this._send({ action: "telegram_input_enable", enabled: this.prefs.telegramInput });
    this._send({ action: "set_ide", ide: this.prefs.ideFilter });
    this._send({ action: "set_include_thinking", enabled: this.prefs.includeThinking });
    this._send({ action: "set_include_codex_progress", enabled: this.prefs.includeCodexProgress });
    
    if (this.prefs.ttsVoiceIndex !== undefined) {
      this._send({ action: "tts_set_voice", voice_index: this.prefs.ttsVoiceIndex });
    }
    
    // Guardar API keys en DB (si cambiaron)
    const geminiKey = getVal("settings-gemini-key");
    const groqKey = getVal("settings-groq-key");
    const telegramToken = getVal("settings-telegram-token");
    const telegramChatId = getVal("settings-telegram-chat-id");
    
    if (geminiKey) {
      this._send({ action: "db_set_setting", key: "GEMINI_API_KEY", value: geminiKey, encrypted: true });
    }
    if (groqKey) {
      this._send({ action: "db_set_setting", key: "GROQ_API_KEY", value: groqKey, encrypted: true });
    }
    if (telegramToken) {
      this._send({ action: "db_set_setting", key: "TELEGRAM_BOT_TOKEN", value: telegramToken, encrypted: true });
    }
    if (telegramChatId) {
      this._send({ action: "db_set_setting", key: "TELEGRAM_CHAT_ID", value: telegramChatId });
    }
    
    // Guardar proveedor STT
    const sttProvider = getVal("settings-stt-provider");
    const whisperModel = getVal("settings-whisper-model");
    if (sttProvider) {
      this._send({ action: "db_set_setting", key: "STT_PROVIDER", value: sttProvider });
    }
    if (whisperModel) {
      this._send({ action: "db_set_setting", key: "WHISPER_MODEL", value: whisperModel });
    }
    
    // Actualizar UI principal
    this._setToggle("tts-enabled", this.prefs.ttsEnabled);
    this._setToggle("tts-llm-enabled", this.prefs.ttsLlmEnabled);
    this._setSlider("tts-rate", this.prefs.ttsRate);
    this._setSelect("ide-selector", this.prefs.ideFilter);
    this._setToggle("include-thinking", this.prefs.includeThinking);
    this._setToggle("tts-telegram", this.prefs.ttsTelegram);
    this._setToggle("telegram-input", this.prefs.telegramInput);
    
    this._closeSettings();
    this._toast("Configuración guardada", "success");
  }

  _previewVoice() {
    const voiceSelect = document.getElementById("settings-tts-voice");
    if (!voiceSelect || !voiceSelect.value) return;
    
    const idx = parseInt(voiceSelect.value);
    // Enviar texto de prueba al TTS
    this._send({ action: "tts_set_voice", voice_index: idx });
    this._toast("Reproduciendo preview...", "info");
    
    // El servidor no tiene un endpoint de preview directo, 
    // así que usamos el cambio de voz como confirmación
  }

  _populateSettingsVoices(voices) {
    const sel = document.getElementById("settings-tts-voice");
    if (!sel) return;
    sel.innerHTML = "";
    voices.forEach((v) => {
      const opt = document.createElement("option");
      opt.value = v.index;
      opt.textContent = v.name || `Voz ${v.index + 1}`;
      sel.appendChild(opt);
    });
    if (this.prefs.ttsVoiceIndex != null) sel.value = this.prefs.ttsVoiceIndex;
  }

  _applyDbSettings(settings) {
    // Aplicar settings guardados en DB al modal
    const setVal = (id, val) => {
      const el = document.getElementById(id);
      if (!el || val === undefined) return;
      if (el.type === "checkbox") el.checked = !!val;
      else el.value = val;
    };
    
    setVal("settings-stt-provider", settings.STT_PROVIDER);
    setVal("settings-whisper-model", settings.WHISPER_MODEL);
    
    // Mostrar/ocultar modelo Whisper según proveedor
    const whisperGroup = document.getElementById("whisper-model-group");
    if (whisperGroup) {
      whisperGroup.style.display = settings.STT_PROVIDER === "whisper_local" ? "block" : "none";
    }
    
    // No mostramos las API keys por seguridad (solo indicar si están configuradas)
    if (settings.GEMINI_API_KEY) {
      const el = document.getElementById("settings-gemini-key");
      if (el) el.placeholder = "••••••• (configurada)";
    }
    if (settings.GROQ_API_KEY) {
      const el = document.getElementById("settings-groq-key");
      if (el) el.placeholder = "••••••• (configurada)";
    }
    if (settings.TELEGRAM_BOT_TOKEN) {
      const el = document.getElementById("settings-telegram-token");
      if (el) el.placeholder = "••••••• (configurado)";
    }
    if (settings.TELEGRAM_CHAT_ID) {
      const el = document.getElementById("settings-telegram-chat-id");
      if (el) el.value = settings.TELEGRAM_CHAT_ID;
    }
  }

  // ─── Preferencias ──────────────────────────────────────────────────────────

  _loadPrefs() {
    try { return JSON.parse(localStorage.getItem("vibe_voice_prefs") || "{}"); }
    catch { return {}; }
  }

  _savePrefs() {
    try { localStorage.setItem("vibe_voice_prefs", JSON.stringify(this.prefs)); }
    catch {}
  }

  _applyPrefs() {
    const p = this.prefs;
    
    // DEFAULTS: todo activado por defecto
    if (p.autoScroll === undefined)      p.autoScroll      = true;
    if (p.showTimestamps === undefined)  p.showTimestamps  = true;
    if (p.ttsEnabled === undefined)      p.ttsEnabled      = true;
    if (p.ttsLlmEnabled === undefined)   p.ttsLlmEnabled   = true;
    if (p.includeThinking === undefined) p.includeThinking = true;
    if (p.includeCodexProgress === undefined) p.includeCodexProgress = true;
    if (p.ideFilter === undefined)       p.ideFilter       = "all";

    const setEl = (id, val) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (el.type === "checkbox") el.checked = !!val;
      else el.value = val;
    };

    setEl("auto-scroll",      p.autoScroll);
    setEl("show-timestamps",  p.showTimestamps);
    setEl("tts-enabled",      p.ttsEnabled);
    setEl("tts-llm-enabled",  p.ttsLlmEnabled);
    setEl("include-thinking", p.includeThinking);
    setEl("ws-url",           p.wsUrl || WS_URL_DEFAULT);
    setEl("ide-selector",     p.ideFilter);
    this._updateIdeHint(p.ideFilter);

    if (p.ttsRate !== undefined) {
      setEl("tts-rate", p.ttsRate);
      const lbl = document.getElementById("tts-rate-label");
      if (lbl) lbl.textContent = `${parseFloat(p.ttsRate).toFixed(1)}x`;
    }
    
    // Guardar defaults
    this._savePrefs();
  }

  _updateIdeHint(ide) {
    const hint = document.getElementById("ide-hint");
    if (!hint) return;
    const names = {
      "all": "Monitoreando todos los IDEs",
      "vscode-insiders": "Solo VS Code Insiders",
      "vscode": "Solo VS Code",
      "cursor": "Solo Cursor",
      "codex": "Solo Codex CLI"
    };
    hint.textContent = names[ide] || "Selecciona qué IDE monitorear";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  window.viewer = new VibeVoiceViewer();
});
