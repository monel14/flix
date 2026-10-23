/* ==========================================================================
   NokaTV — Lecteur Direct Sécurisé (Shadow DOM fermé + Anti-Inspection)
   ========================================================================== */
(() => {
  const container = document.getElementById('ss-player');
  const button = document.getElementById('ss-load-player');
  const close = document.getElementById('ss-unload-player');
  const togglePlay = document.getElementById('ss-toggle-play');
  const toggleMute = document.getElementById('ss-toggle-mute');
  const toggleFullscreen = document.getElementById('ss-toggle-fullscreen');
  const status = document.getElementById('ss-player-status');

  if (!container || !button || !close) return;
  const placeholder = document.getElementById('ss-placeholder') || container.firstElementChild;

  let isPlaying = true;
  let isMuted = false;
  let activeFrame = null;

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

  // 1. Démarrage de la diffusion avec injection dans un Shadow DOM fermé
  button.addEventListener('click', () => {
    const streamUrl = decodeToken(container.dataset.token);
    if (!streamUrl) return;

    // Hôte isolé du Shadow DOM fermé
    const shadowHost = document.createElement('div');
    shadowHost.style.cssText = 'width:100%;height:100%;position:relative;display:block;';

    // mode: 'closed' empêche tout script externe ou console d'accéder au shadowRoot
    const shadowRoot = shadowHost.attachShadow({ mode: 'closed' });

    const frame = document.createElement('iframe');
    frame.src = streamUrl;
    frame.title = 'Diffusion en direct';
    frame.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen';
    frame.allowFullscreen = true;
    frame.referrerPolicy = 'strict-origin-when-cross-origin';
    frame.style.cssText = 'width:100%;height:100%;border:0;display:block;';

    // Bouclier transparent supérieur : neutralise tout clic sur le titre éphémère
    const shield = document.createElement('div');
    shield.style.cssText = 'position:absolute;top:0;left:0;right:0;height:55px;z-index:5;background:transparent;pointer-events:auto;';

    shadowRoot.appendChild(frame);
    shadowRoot.appendChild(shield);

    activeFrame = frame;
    container.replaceChildren(shadowHost);

    // Activer les contrôles personnalisés
    if (togglePlay) { togglePlay.hidden = false; togglePlay.textContent = 'Pause'; isPlaying = true; }
    if (toggleMute) { toggleMute.hidden = false; toggleMute.textContent = 'Couper le son'; isMuted = false; }
    if (toggleFullscreen) { toggleFullscreen.hidden = false; }
    close.hidden = false;
    close.focus();

    if (status) status.textContent = 'Diffusion en cours.';
  });

  // 2. Contrôles vidéo natifs
  if (togglePlay) {
    togglePlay.addEventListener('click', () => {
      isPlaying = !isPlaying;
      postCommand(isPlaying ? 'playVideo' : 'pauseVideo');
      togglePlay.textContent = isPlaying ? 'Pause' : 'Reprendre';
    });
  }

  if (toggleMute) {
    toggleMute.addEventListener('click', () => {
      isMuted = !isMuted;
      postCommand(isMuted ? 'mute' : 'unMute');
      toggleMute.textContent = isMuted ? 'Activer le son' : 'Couper le son';
    });
  }

  if (toggleFullscreen) {
    toggleFullscreen.addEventListener('click', () => {
      if (document.fullscreenElement) {
        document.exitFullscreen?.();
      } else {
        container.requestFullscreen?.();
      }
    });
  }

  // 3. Fermeture du lecteur
  close.addEventListener('click', () => {
    activeFrame = null;
    container.replaceChildren(placeholder);
    if (togglePlay) togglePlay.hidden = true;
    if (toggleMute) toggleMute.hidden = true;
    if (toggleFullscreen) toggleFullscreen.hidden = true;
    close.hidden = true;
    if (status) status.textContent = 'Diffusion arrêtée.';
    button.focus();
  });

  // 4. Boucliers anti-inspection de surface
  // Désactivation du clic droit sur l'ensemble du lecteur
  container.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    return false;
  });

  // Interception des raccourcis clavier d'inspection (F12, Ctrl+U, Ctrl+Shift+I/J/C)
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
