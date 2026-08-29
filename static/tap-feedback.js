/* Feedback tactile : onde "ripple" au point de contact sur les éléments
   interactifs. Aucune dépendance ; respecte prefers-reduced-motion et ne
   bloque jamais le défilement (écouteur passif uniquement). */
(function () {
    "use strict";

    // Éléments mappés : boutons, cartes, filtres, navigation, contrôles.
    var SELECTOR = [
        ".btn-cta",
        ".btn-share",
        ".stream-card",
        ".history-card",
        ".top-rail-card",
        ".episode-btn-card",
        ".server-pill-btn",
        ".playlist-ep-chip",
        ".player-ctrl-btn",
        ".player-back-btn",
        ".genre-chip",
        ".detail-genre-badge",
        ".detail-genre-more",
        ".synopsis-read-more",
        ".dd-item",
        ".list-alias-link",
        ".see-all-link",
        ".top-tab-btn",
        ".version-segment-btn",
        ".sort-segment-btn",
        ".nav-link",
        ".mobile-tab",
        ".hero-nav",
        ".hero-dot",
        ".rail-nav"
    ].join(",");

    var reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

    document.addEventListener(
        "pointerdown",
        function (event) {
            // Clic droit ou molette : aucun retour visuel attendu.
            if (event.pointerType === "mouse" && event.button !== 0) return;
            if (reducedMotion.matches) return;

            var host = event.target.closest && event.target.closest(SELECTOR);
            if (!host) return;

            var rect = host.getBoundingClientRect();
            if (!rect.width || !rect.height) return;

            // Diamètre couvrant tout l'élément depuis le point de contact.
            var size = Math.max(rect.width, rect.height) * 1.2;
            var x = (event.clientX || rect.left + rect.width / 2) - rect.left;
            var y = (event.clientY || rect.top + rect.height / 2) - rect.top;

            var ripple = document.createElement("span");
            ripple.className = "ripple";
            ripple.style.width = size + "px";
            ripple.style.height = size + "px";
            ripple.style.left = x - size / 2 + "px";
            ripple.style.top = y - size / 2 + "px";

            // overflow:hidden n'est posé que le temps de l'animation, pour ne
            // pas rogner durablement ombres ou badges qui débordent des cartes.
            host.classList.add("ripple-host");
            host.appendChild(ripple);

            ripple.addEventListener("animationend", function () {
                ripple.remove();
                host.classList.remove("ripple-host");
            });
            // Filet de sécurité si l'animation est interrompue (onglet caché).
            setTimeout(function () {
                if (ripple.parentNode) {
                    ripple.remove();
                    host.classList.remove("ripple-host");
                }
            }, 700);
        },
        { passive: true }
    );
})();
