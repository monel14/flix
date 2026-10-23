/* Connexion au lecteur uniquement sur consentement explicite ; aucun flux extrait. */
(() => {
  const container = document.getElementById('ss-player');
  const button = document.getElementById('ss-load-player');
  const close = document.getElementById('ss-unload-player');
  const status = document.getElementById('ss-player-status');
  if (!container || !button || !close || !status) return;
  const placeholder = container.firstElementChild;
  button.addEventListener('click', () => {
    if (container.querySelector('iframe')) return;
    let url;
    try { url = new URL(container.dataset.embed); } catch (_) { return; }
    if (url.origin !== 'https://www.youtube-nocookie.com' ||
        !/^\/embed\/[A-Za-z0-9_-]{11}$/.test(url.pathname)) return;
    url.searchParams.set('autoplay', '1');
    const frame = document.createElement('iframe');
    frame.src = url.href;
    frame.title = 'Secret Story Afrique — lecteur YouTube';
    frame.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen';
    frame.allowFullscreen = true;
    frame.referrerPolicy = 'strict-origin-when-cross-origin';
    container.replaceChildren(frame);
    close.hidden = false;
    close.focus();
    status.textContent = 'Lecteur chargé. Si YouTube refuse la lecture, utilisez les liens de secours ci-dessous.';
  });
  close.addEventListener('click', () => {
    container.replaceChildren(placeholder);
    close.hidden = true;
    status.textContent = 'Lecteur fermé. Vous pouvez le charger à nouveau.';
    button.focus();
  });
})();
