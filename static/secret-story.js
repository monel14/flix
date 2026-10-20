/* Pas de connexion YouTube avant le clic ; aucune extraction de flux. */
(() => {
  const button = document.getElementById('ss-load-player');
  const container = document.getElementById('ss-player');
  if (!button || !container) return;
  button.addEventListener('click', () => {
    const url = new URL(container.dataset.embed);
    if (url.origin !== 'https://www.youtube-nocookie.com' ||
        !/^\/embed\/[A-Za-z0-9_-]{11}$/.test(url.pathname)) return;
    const frame = document.createElement('iframe');
    frame.src = url.href;
    frame.title = 'Secret Story Afrique — lecteur YouTube';
    frame.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen';
    frame.allowFullscreen = true;
    frame.referrerPolicy = 'strict-origin-when-cross-origin';
    container.replaceChildren(frame);
  }, {once: true});
})();
