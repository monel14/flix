/**
 * NokaTV / Flix - Autoplay v3 - ALL SOURCES
 * Fix: ton autoplay est appliqué a une seule source player? -> NON, maintenant explicite multi-sources
 * - Hook direct sur TOUS les .server-pill-btn
 * - Boutons en HAUT
 * - Support vidzy, vidmoly, voe, filemoon, streamtape, etc.
 */

(function () {
  if (window.__flixAutoplay) {
    try { window.__flixAutoplayForceReboot && window.__flixAutoplayForceReboot(); } catch {}
    return;
  }
  window.__flixAutoplay = true;

  const KEY_AUTO = 'auto_next_episode_enabled';
  const KEY_FS = 'fss_auto_fullscreen';
  const KEY_PROGRESS_PREFIX = 'video_progress_';
  const log = (...a) => { try { console.log('[FlixAutoplay v3]', ...a); } catch {} };

  function isAutoOn() { try { return localStorage.getItem(KEY_AUTO) !== 'false'; } catch { return true; } }
  function isFsOn() { try { return localStorage.getItem(KEY_FS) === '1'; } catch { return false; } }
  function setAuto(v) {
    try { localStorage.setItem(KEY_AUTO, v ? 'true' : 'false'); } catch {}
    renderPills();
    log('Lecture Auto', v ? 'ON' : 'OFF');
    if (v) attemptPlaySequence('toggle ON');
    else armAutoBlock();
  }
  function setFs(v) {
    try { localStorage.setItem(KEY_FS, v ? '1' : '0'); } catch {}
    renderPills();
    log('Plein écran auto', v ? 'ON' : 'OFF');
  }

  function getPlayerFrame() {
    return document.getElementById('player-frame') || document.getElementById('seriePlayer') || document.getElementById('video-iframe');
  }
  function postToPlayer(msg) {
    const f = getPlayerFrame();
    if (!f || !f.contentWindow) return false;
    try { f.contentWindow.postMessage(msg, '*'); return true; } catch { return false; }
  }

  function postPlay() {
    const variants = [
      { action: 'play' }, { action: 'fss_play' }, { action: 'player:play' },
      { event: 'play' }, { type: 'play' }, { method: 'play' }, { cmd: 'play' },
      'play', { action: 'resume' }, { action: 'start' },
      { action: 'fss_mini', on: false }, // force exit mini comme fs16
    ];
    variants.forEach(v => postToPlayer(v));
    // tente aussi via player JS global si exposé par certains embeds
    try {
      const f = getPlayerFrame();
      if (f && f.contentWindow && f.contentWindow.player && typeof f.contentWindow.player.play === 'function') {
        f.contentWindow.player.play();
      }
    } catch {}
  }
  function postPause() {
    postToPlayer({ action: 'pause' });
    postToPlayer({ event: 'pause' });
    postToPlayer({ method: 'pause' });
    postToPlayer('pause');
  }
  function postSeek(pos) {
    postToPlayer({ action: 'seek', value: pos, time: pos, currentTime: pos });
    postToPlayer({ action: 'setCurrentTime', value: pos });
    postToPlayer({ action: 'fss_seek', value: pos });
    postToPlayer({ seek: pos });
  }

  // --- UI Pills en HAUT ---
  function createPill(text, getState, onToggle, icon) {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'fss-auto-pill';
    el.innerHTML = `<span class="fss-fs-dot"></span><i class="fas ${icon}" style="font-size:.75rem"></i><span class="fss-fs-text">${text}</span><span class="fss-src-count" style="opacity:.6;font-size:.65rem;margin-left:2px"></span>`;
    function render() {
      const on = getState();
      const dot = el.querySelector('.fss-fs-dot');
      dot.classList.toggle('on', on);
      el.classList.toggle('active', on);
      const count = document.querySelectorAll('.server-pill-btn').length;
      const countEl = el.querySelector('.fss-src-count');
      if (countEl) countEl.textContent = count ? `(${count} sources)` : '';
      el.title = on ? `${text} : activé sur ${count} sources` : `${text} : désactivé`;
    }
    el._render = render;
    el.addEventListener('click', (e) => { e.preventDefault(); onToggle(); render(); });
    render();
    return el;
  }

  let pillAuto, pillFs;
  function renderPills() {
    pillAuto && pillAuto._render && pillAuto._render();
    pillFs && pillFs._render && pillFs._render();
  }

  function injectPillsTop() {
    document.querySelectorAll('.fss-auto-pills').forEach(el => el.remove());
    const serversBar = document.querySelector('.servers-selector-bar');
    const headerBar = document.querySelector('.player-header-bar');
    const theaterBox = document.getElementById('theater-box');
    let anchor = serversBar || headerBar;
    if (!anchor) return;
    if (document.querySelector('#fss-auto-top')) {
      renderPills();
      return;
    }
    pillAuto = createPill('Lecture Auto', isAutoOn, () => setAuto(!isAutoOn()), 'fa-play');
    pillFs = createPill('Plein écran auto', isFsOn, () => setFs(!isFsOn()), 'fa-expand');
    const topContainer = document.createElement('div');
    topContainer.id = 'fss-auto-top';
    topContainer.className = 'fss-auto-top';
    topContainer.append(pillAuto, pillFs);
    if (serversBar) serversBar.parentNode.insertBefore(topContainer, serversBar);
    else if (headerBar && theaterBox) headerBar.parentNode.insertBefore(topContainer, theaterBox);
    else anchor.appendChild(topContainer);

    if (!document.getElementById('fss-auto-style-v3')) {
      const st = document.createElement('style');
      st.id = 'fss-auto-style-v3';
      st.textContent = `
        #fss-auto-top.fss-auto-top{
          display:flex;gap:8px;align-items:center;flex-wrap:wrap;
          padding:8px 10px;margin:6px 0 8px;
          background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.06);
          border-radius:8px;backdrop-filter:blur(6px);
        }
        .fss-auto-pill{
          display:inline-flex;align-items:center;gap:6px;
          background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08);
          color:#94a3b8;padding:6px 12px;border-radius:999px;
          font-size:.78rem;font-weight:600;cursor:pointer;transition:all .15s ease;
          user-select:none;touch-action:manipulation;
        }
        .fss-auto-pill:hover{background:rgba(255,255,255,.10);color:#fff;border-color:rgba(255,255,255,.14)}
        .fss-auto-pill.active{background:rgba(72,124,145,.18);border-color:rgba(72,124,145,.35);color:#e2e8f0}
        .fss-auto-pill.active:hover{background:rgba(72,124,145,.26)}
        .fss-fs-dot{width:8px;height:8px;border-radius:50%;background:#555;display:inline-block;transition:all .15s;flex-shrink:0}
        .fss-fs-dot.on{background:#22c55e;box-shadow:0 0 8px rgba(34,197,94,.6)}
        .theater-frame-box .fss-auto-pills{display:none !important}
      `;
      document.head.appendChild(st);
    }
    log('Pills injectées en HAUT pour', document.querySelectorAll('.server-pill-btn').length, 'sources');
  }

  // --- Progress ---
  function getVideoId() {
    const slug = document.querySelector('[data-slug]')?.getAttribute('data-slug') || location.pathname;
    return KEY_PROGRESS_PREFIX + slug;
  }
  function loadProgress() { try { const raw = localStorage.getItem(getVideoId()); return raw ? JSON.parse(raw) : null; } catch { return null; } }
  function saveProgress(time, dur) {
    if (!time || !dur || time < 1) return;
    try { localStorage.setItem(getVideoId(), JSON.stringify({ time, duration: dur, timestamp: Date.now() })); } catch {}
  }

  // --- Autoplay ---
  let lastSrc = '';
  let pausedForArm = -1;
  let armId = 0;
  let armTime = 0;
  let userInteracted = false;

  function armAutoBlock() {
    if (isAutoOn()) return;
    armId++; armTime = Date.now();
    const myId = armId; const t0 = Date.now();
    log('Arm block OFF');
    const iv = setInterval(() => {
      if (myId !== armId || isAutoOn() || Date.now() - t0 > 1400) { clearInterval(iv); return; }
      postPause();
    }, 300);
  }

  function attemptPlaySequence(reason = 'src change') {
    if (!isAutoOn()) { log('Skip play, OFF'); return; }
    const f = getPlayerFrame();
    if (!f) { log('No frame'); return; }
    if (!f.src || f.src.includes('about:blank')) { log('Src blank, skip'); return; }
    const saved = loadProgress();
    const pos = saved && saved.time > 5 ? saved.time : 0;
    log(`Attempt play [${reason}] pos=${pos} src=${f.src.slice(0,80)} sources=${document.querySelectorAll('.server-pill-btn').length}`);

    // Pour TOUS les lecteurs: force autoplay=1 dans l'URL si pas présent
    try {
      if (!f.src.includes('autoplay=')) {
        const sep = f.src.includes('?') ? '&' : '?';
        // ne pas recharger si déjà autoplay, juste log
        log('Src missing autoplay param, but we keep it (template already adds it)');
      }
    } catch {}

    setTimeout(() => { if (pos > 5) postSeek(pos); postPlay(); }, 500);
    setTimeout(() => { postPlay(); }, 1200);
    setTimeout(() => { postPlay(); }, 2500);
    setTimeout(() => { postPlay(); }, 4500);
  }

  // Sur changement src iframe (toutes sources)
  setInterval(() => {
    const f = getPlayerFrame();
    if (!f || !f.src || f.src === lastSrc) return;
    if (f.src.includes('about:blank')) return;
    lastSrc = f.src;
    log('New src detected (ALL SOURCES):', f.src.slice(0,100));
    if (isAutoOn()) attemptPlaySequence('new src');
    else armAutoBlock();
  }, 600);

  // HOOK EXPLICITE sur TOUS les boutons serveurs (fix: appliqué à une seule source?)
  function hookServerButtons() {
    const btns = document.querySelectorAll('.server-pill-btn');
    if (!btns.length) return;
    btns.forEach(btn => {
      if (btn._flixHooked) return;
      btn._flixHooked = true;
      btn.addEventListener('click', () => {
        const link = btn.dataset.link || btn.getAttribute('data-link') || '';
        const name = btn.textContent.trim();
        log(`Server clicked: ${name} -> ${link.slice(0,80)} - will autoplay for ALL sources`);
        // le template change déjà frame.src, on attend 600ms que src change puis play
        setTimeout(() => attemptPlaySequence(`server click ${name}`), 700);
        setTimeout(() => attemptPlaySequence(`server click ${name} retry`), 2000);
      });
    });
    log(`Hooked ${btns.length} server buttons`);
  }

  // Plein écran auto uniquement sur geste
  function tryFullscreen() {
    if (!isFsOn() || !isAutoOn() || document.fullscreenElement) return;
    const f = getPlayerFrame();
    const box = document.getElementById('theater-box');
    const target = box || f;
    if (!target) return;
    try {
      const req = target.requestFullscreen || target.webkitRequestFullscreen || box?.requestFullscreen;
      if (req) req.call(target).then(() => log('Fullscreen auto OK')).catch(e => log('Fullscreen blocked:', e.message));
      postToPlayer({ action: 'enter_fullscreen' });
    } catch (e) { log('FS error', e); }
  }

  ['click','touchend','keydown'].forEach(ev => {
    document.addEventListener(ev, () => {
      if (!userInteracted) {
        userInteracted = true;
        log('User interacted - retry play');
        if (isAutoOn()) {
          setTimeout(() => attemptPlaySequence('user gesture'), 100);
          if (isFsOn()) setTimeout(tryFullscreen, 400);
        }
      } else {
        if (isAutoOn()) {
          const f = getPlayerFrame();
          if (f && f.src && !f.src.includes('about:blank')) {
            const p = loadProgress();
            if (!p || p.time < 2) setTimeout(postPlay, 100);
          }
        }
      }
    }, { passive: true });
  });

  function bindFsTrigger() {
    const box = document.getElementById('theater-box');
    if (!box) return;
    if (box._fsHooked) return;
    box._fsHooked = true;
    box.addEventListener('click', () => { if (isFsOn() && isAutoOn()) setTimeout(tryFullscreen, 200); });
    window.addEventListener('message', (ev) => {
      const d = ev.data;
      if (!d || typeof d !== 'object') return;
      const ct = d.currentTime || d.time || 0;
      const isPlaying = d.action === 'play' || d.action === 'playing' || d.paused === false || ct > 1;
      if (isPlaying && isFsOn() && userInteracted && !document.fullscreenElement && ct < 5) {
        log('Auto FS on play', ct);
        setTimeout(tryFullscreen, 500);
      }
    });
  }

  window.addEventListener('message', (ev) => {
    const d = ev.data;
    if (!d || typeof d !== 'object') return;
    const ct = typeof d.currentTime === 'number' ? d.currentTime : (typeof d.time === 'number' ? d.time : null);
    const dur = typeof d.duration === 'number' ? d.duration : null;
    if (ct != null && dur) saveProgress(ct, dur);
    if (!isAutoOn()) {
      if (armId === pausedForArm) return;
      if (!armTime || Date.now() - armTime > 12000) return;
      const playing = d.action === 'play' || (d.action === 'updatePlayPauseButton' && d.isPlaying === true) || (d.action === 'timeupdate' && ct > 0.1 && ct < 5);
      if (playing) {
        pausedForArm = armId;
        postPause();
        setTimeout(postPause, 180);
        setTimeout(postPause, 500);
      }
    }
    if (d.action === 'ended' || d.event === 'ended' || (dur && ct && dur - ct < 1.5)) {
      if (!isAutoOn()) return;
      const nextLink = document.querySelector('a.player-ctrl-btn--next');
      if (nextLink && nextLink.href) {
        log('Ended -> next', nextLink.href);
        setTimeout(() => { location.href = nextLink.href; }, 1500);
      }
    }
  });

  function observeFrames() {
    const theater = document.getElementById('theater-box');
    const cont = theater || document.querySelector('.iframe-container') || document.getElementById('main-player');
    if (!cont) return;
    try {
      new MutationObserver((muts) => {
        for (const m of muts) {
          if (m.type === 'attributes' && m.attributeName === 'src') {
            if (isAutoOn()) attemptPlaySequence('mutation src'); else armAutoBlock();
          }
          if (m.addedNodes && m.addedNodes.length) {
            hookServerButtons(); // re-hook si nouveaux boutons
            if (isAutoOn()) attemptPlaySequence('mutation add');
          }
        }
      }).observe(cont, { attributes: true, attributeFilter: ['src'], subtree: true, childList: true });
      // observe aussi servers bar pour nouveaux serveurs ajoutés dynamiquement
      const srvBar = document.querySelector('.servers-selector-bar');
      if (srvBar) {
        new MutationObserver(() => { hookServerButtons(); injectPillsTop(); }).observe(srvBar, { childList: true, subtree: true });
      }
    } catch {}
  }

  function boot() {
    injectPillsTop();
    hookServerButtons();
    observeFrames();
    bindFsTrigger();
    if (isAutoOn()) attemptPlaySequence('boot');
    else armAutoBlock();
  }

  // Expose pour debug
  window.FlixAutoplay = {
    isAutoOn, isFsOn, setAuto, setFs,
    play: () => attemptPlaySequence('manual'),
    pause: () => postPause(),
    getFrame: getPlayerFrame,
  };
  window.__flixAutoplayForceReboot = () => { injectPillsTop(); hookServerButtons(); bindFsTrigger(); };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
  setTimeout(boot, 800);
  setTimeout(() => { hookServerButtons(); boot(); }, 2000);
})();
