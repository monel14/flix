/* ==========================================================================
   NokaTV — Lecteur Direct Sécurisé (Shadow DOM fermé + Anti-Inspection + DVR)
   ========================================================================== */
(() => {
  const container = document.getElementById('ss-player');
  const button = document.getElementById('ss-load-player');
  const controls = document.getElementById('ssPlayerControls');
  const underPlayer = document.getElementById('ssUnderPlayer');

  const playBtn = document.getElementById('ssPlayBtn');
  const back30Btn = document.getElementById('ssBack30Btn');
  const forward30Btn = document.getElementById('ssForward30Btn');
  const liveBtn = document.getElementById('ssLiveBtn');
  const dvrLabel = document.getElementById('ssDvrLabel');
  const currentLabel = document.getElementById('ssCurrentLabel');
  const edgeLabel = document.getElementById('ssEdgeLabel');
  const timeline = document.getElementById('ssTimeline');
  const seekPreview = document.getElementById('ssSeekPreview');
  const muteBtn = document.getElementById('ssMuteBtn');
  const pipBtn = document.getElementById('ssPipBtn');
  const fullscreenBtn = document.getElementById('ssPlayerFullBtn');
  const close = document.getElementById('ss-unload-player');
  const playCircle = document.getElementById('ss-play-circle');

  if (!container || !button) return;
  const placeholder = document.getElementById('ss-placeholder') || container.firstElementChild;

  if (playCircle) {
    playCircle.addEventListener('click', () => button.click());
  }

  let isPlaying = true;
  let isMuted = false;
  let currentTime = 0;
  let maxDuration = 3600; // Baseline DVR
  let isAtLiveEdge = true;
  let isDragging = false;
  let activeFrame = null;
  let tickInterval = null;
  let tapFeedback = null;

  // Déchiffrement du token de flux (aucun lien ni identifiant en clair dans le HTML)
  function decodeToken(token) {
    if (!token) return '';
    try {
      const b64 = token.split('').reverse().join('');
      return atob(b64);
    } catch (_) {
      return '';
    }
  }

  function postCommand(func, args) {
    if (!activeFrame || !activeFrame.contentWindow) return;
    activeFrame.contentWindow.postMessage(JSON.stringify({
      event: 'command',
      func: func,
      args: args || []
    }), '*');
  }

  function formatTime(seconds) {
    if (!seconds || isNaN(seconds) || seconds < 0) return '00:00';
    const s = Math.floor(seconds);
    const hrs = Math.floor(s / 3600);
    const mins = Math.floor((s % 3600) / 60);
    const secs = s % 60;
    const mm = String(mins).padStart(2, '0');
    const ss = String(secs).padStart(2, '0');
    if (hrs > 0) {
      return `${hrs}:${mm}:${ss}`;
    }
    return `${mm}:${ss}`;
  }

  function showTapFeedback(icon) {
    if (!tapFeedback) return;
    tapFeedback.textContent = icon;
    tapFeedback.classList.add('is-visible');
    setTimeout(() => {
      if (tapFeedback) tapFeedback.classList.remove('is-visible');
    }, 450);
  }

  const SVG_PAUSE = `<svg class="ss-icon ss-icon-pause" viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>`;
  const SVG_PLAY = `<svg class="ss-icon ss-icon-play" viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>`;
  const SVG_VOL = `<svg class="ss-icon ss-icon-vol" viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>`;
  const SVG_MUTE = `<svg class="ss-icon ss-icon-mute" viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/></svg>`;

  function updatePlayButton() {
    if (playBtn) {
      playBtn.innerHTML = isPlaying ? SVG_PAUSE : SVG_PLAY;
      playBtn.title = isPlaying ? 'Mettre en pause' : 'Reprendre la lecture';
    }
  }

  function updateMuteButton() {
    if (muteBtn) {
      muteBtn.innerHTML = isMuted ? SVG_MUTE : SVG_VOL;
      muteBtn.title = isMuted ? 'Activer le son' : 'Couper le son';
    }
  }

  function togglePlayback() {
    isPlaying = !isPlaying;
    postCommand(isPlaying ? 'playVideo' : 'pauseVideo');
    updatePlayButton();
    showTapFeedback(isPlaying ? '▶' : '⏸');
  }

  function updateTimelineUI() {
    if (currentLabel) currentLabel.textContent = formatTime(currentTime);
    if (timeline) {
      const max = Math.max(maxDuration, currentTime, 100);
      if (!isDragging) {
        timeline.max = max;
        timeline.value = currentTime;
      }
      const pct = Math.min(100, Math.max(0, (timeline.value / max) * 100));
      timeline.style.background = `linear-gradient(to right, #ef4444 0%, #ef4444 ${pct}%, rgba(255,255,255,0.14) ${pct}%, rgba(255,255,255,0.14) 100%)`;
    }
    const diff = Math.max(0, maxDuration - currentTime);
    if (diff <= 15 || isAtLiveEdge) {
      if (liveBtn) liveBtn.classList.add('is-live');
      if (dvrLabel) dvrLabel.textContent = 'En direct';
      if (edgeLabel) {
        edgeLabel.textContent = 'DIRECT';
        edgeLabel.style.color = '#ef4444';
      }
    } else {
      if (liveBtn) liveBtn.classList.remove('is-live');
      const diffStr = `−${formatTime(diff)}`;
      if (dvrLabel) dvrLabel.textContent = diffStr;
      if (edgeLabel) {
        edgeLabel.textContent = diffStr;
        edgeLabel.style.color = 'var(--ss-text-muted, #94a3b8)';
      }
    }
  }

  // 1. Démarrage de la diffusion avec injection dans un Shadow DOM fermé
  button.addEventListener('click', () => {
    const streamUrl = decodeToken(container.dataset.token);
    if (!streamUrl) return;

    // Hôte isolé du Shadow DOM fermé
    const shadowHost = document.createElement('div');
    shadowHost.style.cssText = 'width:100%;height:100%;position:relative;display:block;';

    // mode: 'closed' empêche tout script externe ou console d'accéder au shadowRoot
    const shadowRoot = shadowHost.attachShadow({ mode: 'closed' });

    // Iframe YouTube sans cookie
    const frame = document.createElement('iframe');
    frame.src = streamUrl;
    frame.title = 'Diffusion en direct';
    frame.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen';
    frame.allowFullscreen = true;
    frame.referrerPolicy = 'strict-origin-when-cross-origin';
    frame.style.cssText = 'width:100%;height:100%;border:0;display:block;';

    // Bouclier supérieur : neutralise le titre et le bouton de partage YouTube
    const shieldTop = document.createElement('div');
    shieldTop.className = 'ss-player-shield-top';

    // Bouclier coin inférieur droit : neutralise le logo cliquable YouTube
    const shieldCorner = document.createElement('div');
    shieldCorner.className = 'ss-player-shield-corner';

    // Calque d'interaction tactile / clic sur la vidéo (Tap to Play/Pause)
    const tapLayer = document.createElement('button');
    tapLayer.type = 'button';
    tapLayer.className = 'ss-player-tap-layer';
    tapLayer.setAttribute('aria-label', 'Lecture ou pause');
    tapLayer.addEventListener('click', togglePlayback);

    // Retour visuel animé au centre
    const feedback = document.createElement('div');
    feedback.className = 'ss-tap-feedback';
    feedback.setAttribute('aria-hidden', 'true');
    feedback.textContent = '▶';
    tapFeedback = feedback;

    // Filigrane NokaTV Live
    const watermark = document.createElement('div');
    watermark.className = 'ss-watermark';
    watermark.innerHTML = '<span aria-hidden="true">🛡</span><b>NokaTV</b> Live';

    // Feuille de style interne pour le Shadow DOM
    const shadowStyle = document.createElement('style');
    shadowStyle.textContent = `
      .ss-player-shield-top {
        position: absolute; top: 0; left: 0; right: 0; height: 58px; z-index: 5;
        background: transparent; pointer-events: auto;
      }
      .ss-player-shield-corner {
        position: absolute; bottom: 0; right: 0; width: 140px; height: 50px; z-index: 5;
        background: transparent; pointer-events: auto;
      }
      .ss-player-tap-layer {
        position: absolute; inset: 0; width: 100%; height: 100%; background: transparent;
        border: 0; cursor: pointer; z-index: 4; outline: none;
      }
      .ss-watermark {
        position: absolute; top: 14px; left: 14px; z-index: 6; pointer-events: none;
        display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px;
        border-radius: 20px; background: rgba(8, 9, 12, 0.7);
        backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
        border: 1px solid rgba(255, 255, 255, 0.12); color: #f1f5f9;
        font-family: inherit; font-size: 0.72rem; font-weight: 600;
      }
      .ss-watermark b { color: #ffffff; }
      .ss-tap-feedback {
        position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%) scale(0.6);
        width: 64px; height: 64px; border-radius: 50%; background: rgba(0, 0, 0, 0.75);
        backdrop-filter: blur(6px); color: #ffffff; display: grid; place-items: center;
        font-size: 1.6rem; opacity: 0; pointer-events: none; z-index: 10;
        transition: transform 0.22s ease, opacity 0.22s ease;
      }
      .ss-tap-feedback.is-visible {
        opacity: 1; transform: translate(-50%, -50%) scale(1);
      }
    `;

    shadowRoot.appendChild(shadowStyle);
    shadowRoot.appendChild(frame);
    shadowRoot.appendChild(shieldTop);
    shadowRoot.appendChild(shieldCorner);
    shadowRoot.appendChild(tapLayer);
    shadowRoot.appendChild(feedback);
    shadowRoot.appendChild(watermark);

    activeFrame = frame;
    container.replaceChildren(shadowHost);

    // Initialisation de l'API YouTube
    frame.addEventListener('load', () => {
      postCommand('addEventListener', ['onStateChange']);
      postCommand('listening', []);
    });

    // Afficher la barre de contrôles et les infos
    if (controls) controls.hidden = false;
    if (underPlayer) underPlayer.hidden = false;
    if (close) close.hidden = false;

    isPlaying = true;
    isAtLiveEdge = true;
    currentTime = 0;
    maxDuration = 3600;
    updatePlayButton();
    updateTimelineUI();

    // Timer d'horloge de direct
    clearInterval(tickInterval);
    tickInterval = setInterval(() => {
      if (!isPlaying || isDragging) return;
      currentTime += 1;
      if (isAtLiveEdge) {
        maxDuration = Math.max(maxDuration, currentTime);
      }
      updateTimelineUI();
    }, 1000);
  });

  // 2. Contrôles vidéo personnalisés
  if (playBtn) {
    playBtn.addEventListener('click', togglePlayback);
  }

  if (back30Btn) {
    back30Btn.addEventListener('click', () => {
      const target = Math.max(0, currentTime - 30);
      currentTime = target;
      isAtLiveEdge = false;
      postCommand('seekTo', [target, true]);
      updateTimelineUI();
      showTapFeedback('−30s');
    });
  }

  if (forward30Btn) {
    forward30Btn.addEventListener('click', () => {
      const target = Math.min(maxDuration, currentTime + 30);
      currentTime = target;
      if (maxDuration - target <= 15) {
        isAtLiveEdge = true;
      }
      postCommand('seekTo', [target, true]);
      updateTimelineUI();
      showTapFeedback('+30s');
    });
  }

  if (liveBtn) {
    liveBtn.addEventListener('click', () => {
      isAtLiveEdge = true;
      currentTime = maxDuration;
      postCommand('seekTo', [maxDuration > 0 ? maxDuration : 9999999, true]);
      updateTimelineUI();
      showTapFeedback('● Direct');
    });
  }

  if (muteBtn) {
    muteBtn.addEventListener('click', () => {
      isMuted = !isMuted;
      postCommand(isMuted ? 'mute' : 'unMute');
      updateMuteButton();
      showTapFeedback(isMuted ? '🔇' : '🔊');
    });
  }

  if (pipBtn) {
    pipBtn.addEventListener('click', async () => {
      try {
        if (document.pictureInPictureElement) {
          await document.exitPictureInPicture();
        } else if (activeFrame && activeFrame.requestPictureInPicture) {
          await activeFrame.requestPictureInPicture();
        } else {
          container.classList.toggle('is-mini-player');
        }
      } catch (_) {
        container.classList.toggle('is-mini-player');
      }
    });
  }

  if (fullscreenBtn) {
    fullscreenBtn.addEventListener('click', () => {
      if (document.fullscreenElement) {
        document.exitFullscreen?.();
      } else {
        container.requestFullscreen?.();
      }
    });
  }

  // Curseur de la timeline (DVR)
  if (timeline) {
    timeline.addEventListener('input', () => {
      isDragging = true;
      const val = parseFloat(timeline.value);
      if (currentLabel) currentLabel.textContent = formatTime(val);
      const diff = maxDuration - val;
      if (seekPreview) {
        seekPreview.hidden = false;
        seekPreview.textContent = diff <= 15 ? 'DIRECT' : `-${formatTime(diff)}`;
      }
    });

    timeline.addEventListener('change', () => {
      isDragging = false;
      if (seekPreview) seekPreview.hidden = true;
      const target = parseFloat(timeline.value);
      currentTime = target;
      isAtLiveEdge = (maxDuration - target <= 15);
      postCommand('seekTo', [target, true]);
      updateTimelineUI();
    });
  }

  // 3. Réception des événements de statut YouTube
  window.addEventListener('message', (event) => {
    if (!event.data) return;
    let data = event.data;
    if (typeof data === 'string') {
      try { data = JSON.parse(data); } catch (_) { return; }
    }
    if (data && data.event === 'infoDelivery' && data.info) {
      if (typeof data.info.currentTime === 'number' && !isDragging) {
        currentTime = data.info.currentTime;
      }
      if (typeof data.info.duration === 'number' && data.info.duration > 0) {
        maxDuration = Math.max(maxDuration, data.info.duration);
      }
      if (typeof data.info.playerState === 'number') {
        if (data.info.playerState === 1 && !isPlaying) {
          isPlaying = true;
          updatePlayButton();
        } else if (data.info.playerState === 2 && isPlaying) {
          isPlaying = false;
          updatePlayButton();
        }
      }
      if (!isDragging) updateTimelineUI();
    }
  });

  // 4. Fermeture du lecteur
  if (close) {
    close.addEventListener('click', () => {
      clearInterval(tickInterval);
      activeFrame = null;
      tapFeedback = null;
      container.replaceChildren(placeholder);
      if (controls) controls.hidden = true;
      if (underPlayer) underPlayer.hidden = true;
      button.focus();
    });
  }

  // 5. Boucliers anti-inspection de surface
  container.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    return false;
  });

  window.addEventListener('keydown', (e) => {
    const isCtrlOrMeta = e.ctrlKey || e.metaKey;
    const isF12 = e.key === 'F12' || e.keyCode === 123;
    const isInspectCombo = isCtrlOrMeta && e.shiftKey && ['I', 'i', 'J', 'j', 'C', 'c'].includes(e.key);
    const isViewSource = isCtrlOrMeta && (e.key === 'u' || e.key === 'U');

    if (isF12 || isInspectCombo || isViewSource) {
      e.preventDefault();
      e.stopPropagation();
      return false;
    }
  }, true);
})();

