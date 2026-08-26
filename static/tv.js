/* =========================================================================
   NokaTV — Mode TV & Télécommande (interface « 10-foot »)

   Déclenché automatiquement sur Android TV / Google TV / Chromecast, Fire TV,
   Samsung Tizen, LG webOS, Apple TV, Roku et navigateurs Smart TV génériques.
   Aucun effet sur téléphone, tablette ou ordinateur.

   Responsabilités :
   1. Détection TV + classes CSS (html.tv-mode, html.tv-<plateforme>).
   2. Navigation flèches (D-Pad) de carte en carte + auto-scroll des rails.
   3. Mémorisation de la dernière carte focusée (retour sur la page).
   4. Touches TV sur les pages lecteur : Retour / OK / Plein écran.
   5. Media Session API : titre, affiche et épisode précédent/suivant.
   ========================================================================= */
(function () {
  'use strict';

  /* ---------------------------------------------------------------------
     1. DÉTECTION TV
  --------------------------------------------------------------------- */
  var ua = navigator.userAgent || '';
  var platforms = [];

  if (/Android.*(TV|AOSP|SMART-TV)|Google ?TV|Chromecast|CrKey/i.test(ua)) platforms.push('android');
  if (/AFT[A-Z0-9]|Fire ?TV|Amazon ?Fire/i.test(ua)) platforms.push('fire');
  if (/Tizen|SamsungBrowser|SMART-TV.*Samsung/i.test(ua)) platforms.push('tizen');
  if (/Web0S|webOS|NetCast|LG ?Browser/i.test(ua)) platforms.push('webos');
  if (/AppleTV|tvOS/i.test(ua)) platforms.push('apple');
  if (/\bRoku\b|Roku ?TV/i.test(ua)) platforms.push('roku');

  var isSmartTv = platforms.length > 0 || /Smart-?TV|HbbTV/i.test(ua);
  var root = document.documentElement;

  if (isSmartTv) {
    root.classList.add('tv-mode');
    platforms.forEach(function (p) { root.classList.add('tv-' + p); });
  }

  var isTvMode = root.classList.contains('tv-mode');

  /* ---------------------------------------------------------------------
     2. NAVIGATION D-PAD (flèches)
  --------------------------------------------------------------------- */
  var FOCUSABLES = 'a[href], button, [tabindex]:not([tabindex="-1"]), input:not([type="hidden"]), select, textarea';
  var REDUCED_MOTION = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function isVisible(el) {
    if (!el || !el.isConnected) return false;
    if (el.closest('[hidden]')) return false;
    var r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  }

  function isTextEntry(el) {
    if (!el) return false;
    var tag = el.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable === true;
  }

  function focusables() {
    var all = document.querySelectorAll(FOCUSABLES);
    var out = [];
    for (var i = 0; i < all.length; i++) {
      if (isVisible(all[i])) out.push(all[i]);
    }
    return out;
  }

  function center(el) {
    var r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }

  function scrollToEl(el) {
    try {
      el.scrollIntoView({ behavior: REDUCED_MOTION ? 'auto' : 'smooth', block: 'nearest', inline: 'nearest' });
    } catch (e) {
      el.scrollIntoView();
    }
  }

  function nav(dx, dy) {
    var list = focusables();
    if (!list.length) return;

    var current = document.activeElement;
    var from = null;
    if (current && current !== document.body && list.indexOf(current) !== -1) {
      from = center(current);
    }

    if (!from) {
      // Pas encore de sélection : on prend le premier élément visible à l'écran.
      var first = null;
      for (var i = 0; i < list.length; i++) {
        var r = list[i].getBoundingClientRect();
        if (r.top < window.innerHeight && r.left < window.innerWidth) { first = list[i]; break; }
      }
      if (first) {
        first.focus({ preventScroll: true });
        scrollToEl(first);
      }
      return;
    }

    var best = null;
    var bestScore = Infinity;
    for (var j = 0; j < list.length; j++) {
      if (list[j] === current) continue;
      var c = center(list[j]);
      var vx = c.x - from.x;
      var vy = c.y - from.y;

      if (dx > 0 && vx < 6) continue;
      if (dx < 0 && vx > -6) continue;
      if (dy > 0 && vy < 6) continue;
      if (dy < 0 && vy > -6) continue;

      var primary = dx !== 0 ? Math.abs(vx) : Math.abs(vy);
      var secondary = dx !== 0 ? Math.abs(vy) : Math.abs(vx);
      var score = primary * 4 + secondary;
      if (score < bestScore) {
        bestScore = score;
        best = list[j];
      }
    }

    if (best) {
      best.focus({ preventScroll: true });
      scrollToEl(best);
    }
  }

  /* ---------------------------------------------------------------------
     3. MÉMORISATION DU FOCUS (retour sur une page déjà visitée)
  --------------------------------------------------------------------- */
  var FOCUS_KEY = 'nokatv_tv_last_focus';
  function rememberFocus(el) {
    try {
      var all = document.querySelectorAll(FOCUSABLES);
      var index = Array.prototype.indexOf.call(all, el);
      sessionStorage.setItem(FOCUS_KEY, JSON.stringify({
        path: location.pathname,
        href: el.getAttribute('href') || '',
        index: index
      }));
    } catch (e) { /* sessionStorage indisponible : fonctionnalité optionnelle */ }
  }

  function restoreFocus() {
    if (!isTvMode) return;
    try {
      var raw = sessionStorage.getItem(FOCUS_KEY);
      if (!raw) return;
      var data = JSON.parse(raw);
      if (!data || data.path !== location.pathname || !data.href) return;
      var matches = [];
      var all = document.querySelectorAll(FOCUSABLES);
      for (var i = 0; i < all.length; i++) {
        if (all[i].getAttribute('href') === data.href && isVisible(all[i])) matches.push(all[i]);
      }
      var target = matches[data.index || 0] || matches[0];
      if (target) {
        target.focus({ preventScroll: true });
        scrollToEl(target);
      }
    } catch (e) { /* mauvais JSON ou stockage bloqué : on ignore */ }
  }

  document.addEventListener('focusin', function (e) {
    var el = e.target;
    if (el && el.getAttribute && !isTextEntry(el)) rememberFocus(el);
  });

  /* ---------------------------------------------------------------------
     4 + 5. PAGE LECTEUR : touches TV, plein écran, Media Session
  --------------------------------------------------------------------- */
  var frame = document.getElementById('player-frame');
  var theater = document.getElementById('theater-box');
  var fsBtn = document.getElementById('player-fullscreen-btn');

  function fullscreenActive() {
    return !!document.fullscreenElement || !!document.webkitFullscreenElement;
  }

  function toggleFullscreen() {
    if (fullscreenActive()) {
      var exit = document.exitFullscreen || document.webkitExitFullscreen;
      if (exit) exit.call(document).catch(function () {});
      return;
    }
    var req = theater && (theater.requestFullscreen || theater.webkitRequestFullscreen);
    if (req) {
      var p = req.call(theater);
      if (p && p.catch) {
        p.catch(function () {
          if (frame && (frame.requestFullscreen || frame.webkitRequestFullscreen)) {
            (frame.requestFullscreen || frame.webkitRequestFullscreen).call(frame).catch(function () {});
          }
        });
      }
    }
  }

  function setFsIcon(on) {
    if (!fsBtn) return;
    var icon = on ? 'fa-compress' : 'fa-expand';
    var label = on ? 'Quitter' : 'Plein';
    fsBtn.innerHTML = '<i class="fas ' + icon + '" aria-hidden="true"></i> ' + label + ' écran';
  }

  if (fsBtn && theater) {
    fsBtn.addEventListener('click', toggleFullscreen);
    document.addEventListener('fullscreenchange', function () { setFsIcon(fullscreenActive()); });
    document.addEventListener('webkitfullscreenchange', function () { setFsIcon(fullscreenActive()); });
  }

  function isBackKey(e) {
    var keys = ['Backspace', 'GoBack', 'BrowserBack', 'Back'];
    if (keys.indexOf(e.key) !== -1) return true;
    if (e.keyCode === 4 || e.keyCode === 461) return true; /* Android TV / webOS */
    return e.key === 'Unidentified' && e.keyCode === 10009; /* Samsung Tizen */
  }

  function isOkKey(e) {
    return e.key === 'Enter' || e.key === 'Select' || e.key === 'Accept' || e.keyCode === 13;
  }

  function directionKey(e) {
    var key = e.key;
    var code = e.keyCode;
    if (key === 'ArrowLeft' || key === 'Left' || code === 37) return 'left';
    if (key === 'ArrowRight' || key === 'Right' || code === 39) return 'right';
    if (key === 'ArrowUp' || key === 'Up' || code === 38) return 'up';
    if (key === 'ArrowDown' || key === 'Down' || code === 40) return 'down';
    return '';
  }

  function isNeutralFocus(el) {
    return !el || el === document.body ||
      el === frame || el === fsBtn ||
      (el.classList && el.classList.contains('server-pill-btn') && el.classList.contains('active'));
  }

  document.addEventListener('keydown', function (e) {
    if (e.defaultPrevented) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (isTextEntry(document.activeElement) && !isBackKey(e)) return;

    // Flèches : uniquement en mode TV (le clavier desktop garde son comportement natif).
    if (isTvMode && !isBackKey(e)) {
      var direction = directionKey(e);
      if (direction === 'left') { nav(-1, 0); e.preventDefault(); return; }
      if (direction === 'right') { nav(1, 0); e.preventDefault(); return; }
      if (direction === 'up') { nav(0, -1); e.preventDefault(); return; }
      if (direction === 'down') { nav(0, 1); e.preventDefault(); return; }
    }

    // Touche Retour : sortir du plein écran d'abord, sinon laisser le
    // navigateur / la TV gérer le retour (comportement natif).
    if (isBackKey(e)) {
      if (fullscreenActive()) {
        e.preventDefault();
        toggleFullscreen();
      }
      return;
    }

    // OK / Entrée : plein écran quand aucune action précise n'est ciblée
    // (corps de page, iframe ou lecteur déjà actif).
    if (isOkKey(e) && frame && theater && isNeutralFocus(document.activeElement)) {
      e.preventDefault();
      toggleFullscreen();
    }
  });

  // Media Session : titre + affiche + épisode suivant/précédent visibles
  // sur l'écran d'accueil / la barre système de la TV (comme Netflix).
  var metaNode = document.getElementById('media-session-data');
  if (metaNode && navigator.mediaSession && window.MediaMetadata) {
    try {
      var data = JSON.parse(metaNode.textContent || '{}');
      var artwork = (data.artwork || []).map(function (item) {
        return { src: item.src, sizes: item.sizes || '512x512', type: item.type || 'image/jpeg' };
      });
      artwork.push({ src: '/static/icons/icon-512.png', sizes: '512x512', type: 'image/png' });

      navigator.mediaSession.metadata = new MediaMetadata({
        title: data.title || document.title,
        artist: 'NokaTV',
        album: data.album || 'NokaTV',
        artwork: artwork
      });

      if (data.next) {
        navigator.mediaSession.setActionHandler('nexttrack', function () { location.href = data.next; });
      }
      if (data.prev) {
        navigator.mediaSession.setActionHandler('previoustrack', function () { location.href = data.prev; });
      }
      // Play/Pause ne sont pas exposés volontairement : l'iframe est
      // cross-origin, on ne peut pas piloter la vidéo d'un lecteur tiers.
    } catch (e) { /* métadonnées invalides : on ignore */ }
  }

  /* ---------------------------------------------------------------------
     Démarrage
  --------------------------------------------------------------------- */
  if (isTvMode) restoreFocus();
})();
