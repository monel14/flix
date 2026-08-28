/* ==========================================================================
   NokaTV — Gestionnaire d'installation PWA
   --------------------------------------------------------------------------
   Principes :
   - `beforeinstallprompt` est la seule source d'un bouton d'installation
     natif : aucun bouton inopérant n'est rendu ailleurs.
   - `navigator.standalone` est uniquement utilisé comme signal WebKit iOS/
     iPadOS pour afficher les consignes « Ajouter à l'écran d'accueil ».
   - Aucun user-agent n'est utilisé ici. Le marqueur `html.tv-mode` vient du
     mode TV déjà centralisé dans tv.js et bloque toute invitation sur TV.
   - localStorage règle seulement la fréquence après un refus ; il ne sert
     jamais à déduire qu'une PWA est installée.

   Configuration optionnelle, à définir AVANT le chargement de ce fichier :
   window.NokaTVPWAInstallConfig = {
     delay: 3000,
     cooldownDays: 3,
     debug: false
   };

   En développement, ajoutez aussi `?pwa-install-debug=1` à l'URL pour obtenir
   les journaux [PWA Install] dans la console sans les activer en production.
   ========================================================================== */
(function (window, document) {
  'use strict';

  if (!window || !document) return;

  /* Ces deux constantes constituent les valeurs simples à modifier pour un
     déploiement. La configuration globale ci-dessus permet une surcharge sans
     modifier le fichier. */
  var INSTALL_PROMPT_DELAY = 3000;
  var INSTALL_PROMPT_COOLDOWN_DAYS = 3;
  var DISMISSAL_STORAGE_KEY = 'nokatv_pwa_install_dismissed_at';
  var INSTALLATION_CHANNEL_NAME = 'nokatv-pwa-install';
  var DISPLAY_MODES = [
    { name: 'standalone', query: '(display-mode: standalone)' },
    { name: 'fullscreen', query: '(display-mode: fullscreen)' },
    { name: 'minimal-ui', query: '(display-mode: minimal-ui)' },
    { name: 'window-controls-overlay', query: '(display-mode: window-controls-overlay)' }
  ];
  var IOS_INSTALLATION_INSTRUCTIONS = [
    'Appuyez sur le bouton Partager.',
    "Choisissez « Ajouter à l'écran d'accueil ».",
    'Confirmez avec « Ajouter ».'
  ];

  var configuration = window.NokaTVPWAInstallConfig || {};
  var promptDelay = normaliseNumber(configuration.delay, INSTALL_PROMPT_DELAY, 0, 60000);
  var cooldownDays = normaliseNumber(
    configuration.cooldownDays,
    INSTALL_PROMPT_COOLDOWN_DAYS,
    0,
    365
  );
  var cooldownMs = cooldownDays * 24 * 60 * 60 * 1000;
  var debugEnabled = configuration.debug === true || hasDebugQueryFlag();

  function normaliseNumber(value, fallback, min, max) {
    var parsed = typeof value === 'number' ? value : parseFloat(value);
    if (isNaN(parsed) || parsed < min || parsed > max) return fallback;
    return parsed;
  }

  function hasDebugQueryFlag() {
    var search = window.location && window.location.search ? window.location.search : '';
    return /(?:^|[?&])pwa-install-debug=1(?:&|$)/.test(search);
  }

  function debug(message, detail) {
    if (!debugEnabled || !window.console || typeof window.console.info !== 'function') return;
    if (typeof detail === 'undefined') {
      window.console.info('[PWA Install] ' + message);
    } else {
      window.console.info('[PWA Install] ' + message, detail);
    }
  }

  function now() {
    return new Date().getTime();
  }

  function readStorage(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (error) {
      return null;
    }
  }

  function writeStorage(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (error) {
      /* Stockage désactivé / privé : l'installation reste totalement usable. */
    }
  }

  function removeStorage(key) {
    try {
      window.localStorage.removeItem(key);
    } catch (error) {
      /* Voir writeStorage. */
    }
  }

  /* -----------------------------------------------------------------------
     Service : centralise les capacités PWA, le cooldown et les événements.
  ----------------------------------------------------------------------- */
  function PWAInstallManager(promptView) {
    this.promptView = promptView;
    this.deferredPrompt = null;
    this.installed = false;
    this.installationConfirmedThisSession = false;
    this.displayMode = 'browser';
    this.platform = 'unavailable';
    this.canInstall = false;
    this.shouldShowPrompt = false;
    this.promptInProgress = false;
    this.sessionSuppressed = false;
    this.readyForPresentation = false;
    this.presentationTimer = null;
    this.lastUnavailableReason = '';
    this.installationChannel = null;
    this.initialised = false;
  }

  PWAInstallManager.prototype.initialise = function () {
    var self = this;

    /* Garde les intégrations partielles / hydratations accidentelles d'un
       double abonnement aux événements navigateur sur une même instance. */
    if (this.initialised) return;
    this.initialised = true;

    this.bindBrowserEvents();
    this.syncInstalledState();
    this.consumeEarlyBrowserState();

    if (this.installed) return;

    function afterPageLoad() {
      self.schedulePresentation();
    }

    if (document.readyState === 'complete') {
      afterPageLoad();
    } else {
      window.addEventListener('load', afterPageLoad, { once: true });
    }
  };

  PWAInstallManager.prototype.bindBrowserEvents = function () {
    var self = this;

    window.addEventListener('beforeinstallprompt', function (event) {
      self.handleBeforeInstallPrompt(event);
    });
    window.addEventListener('appinstalled', function () {
      self.handleAppInstalled();
    });
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible') self.maybeShowPrompt();
    });
    window.addEventListener('storage', function (event) {
      self.handleStorageChange(event);
    });
    this.bindDisplayModeListeners();
  };

  PWAInstallManager.prototype.getBootstrapState = function () {
    return window.NokaTVPWAInstallBootstrap || null;
  };

  PWAInstallManager.prototype.clearBootstrapDeferredPrompt = function (event) {
    var state = this.getBootstrapState();
    if (!state) return;
    if (!event || state.deferredPrompt === event) state.deferredPrompt = null;
  };

  PWAInstallManager.prototype.consumeEarlyBrowserState = function () {
    var state = this.getBootstrapState();
    var earlyPrompt;

    if (!state) return;

    if (state.appInstalled) {
      state.deferredPrompt = null;
      this.handleAppInstalled();
      return;
    }

    earlyPrompt = state.deferredPrompt;
    if (!earlyPrompt) return;

    state.deferredPrompt = null;
    this.handleBeforeInstallPrompt(earlyPrompt);
  };

  PWAInstallManager.prototype.openInstallationChannel = function () {
    var self = this;

    if (this.installationChannel) return;

    try {
      if (!window.BroadcastChannel) return;
      this.installationChannel = new window.BroadcastChannel(INSTALLATION_CHANNEL_NAME);
      this.installationChannel.onmessage = function (event) {
        if (!event || !event.data || event.data.type !== 'appinstalled') return;
        self.handleAppInstalled(true);
      };
    } catch (error) {
      /* Fonction additive : BroadcastChannel n'est pas requis pour installer. */
      this.installationChannel = null;
    }
  };

  PWAInstallManager.prototype.broadcastAppInstalled = function () {
    if (!this.installationChannel) return;

    try {
      this.installationChannel.postMessage({ type: 'appinstalled' });
    } catch (error) {
      /* Canal fermé / indisponible : l'onglet courant reste correctement traité. */
    }
  };

  PWAInstallManager.prototype.bindDisplayModeListeners = function () {
    var self = this;
    var index;

    if (!window.matchMedia) return;

    for (index = 0; index < DISPLAY_MODES.length; index += 1) {
      try {
        (function (query) {
          var listener = function () { self.handleDisplayModeChange(); };
          if (query.addEventListener) {
            query.addEventListener('change', listener);
          } else if (query.addListener) {
            query.addListener(listener);
          }
        })(window.matchMedia(DISPLAY_MODES[index].query));
      } catch (error) {
        /* Un moteur ancien peut ignorer ce media query : aucun effet de bord. */
      }
    }
  };

  PWAInstallManager.prototype.getInstalledDisplayMode = function () {
    var index;
    var query;

    if (window.matchMedia) {
      for (index = 0; index < DISPLAY_MODES.length; index += 1) {
        try {
          query = window.matchMedia(DISPLAY_MODES[index].query);
          if (query && query.matches) return DISPLAY_MODES[index].name;
        } catch (error) {
          /* Continue avec le signal iOS ci-dessous. */
        }
      }
    }

    /* Signal WebKit iOS/iPadOS : true uniquement lorsque l'app est ouverte
       depuis l'écran d'accueil. La valeur false sert plus loin à choisir les
       instructions iOS, jamais à conclure « non installé » par elle-même. */
    if (window.navigator && window.navigator.standalone === true) return 'standalone';

    return '';
  };

  PWAInstallManager.prototype.syncInstalledState = function () {
    var installedMode = this.getInstalledDisplayMode();
    var wasInstalled = this.installed;

    this.displayMode = installedMode || 'browser';
    this.installed = !!installedMode || this.installationConfirmedThisSession;

    if (this.installed) {
      this.deferredPrompt = null;
      this.sessionSuppressed = true;
      this.shouldShowPrompt = false;
      this.promptInProgress = false;
      if (!wasInstalled) debug('App already installed', this.displayMode);
      if (this.promptView) {
        this.promptView.setInstalling(false);
        this.promptView.hide(true);
      }
    }

    this.updateAvailability();
    return this.installed;
  };

  PWAInstallManager.prototype.isSecureInstallContext = function () {
    var location = window.location || {};

    if (typeof window.isSecureContext === 'boolean') return window.isSecureContext;

    /* Fallback pour les navigateurs plus anciens : localhost est un contexte
       sécurisé de développement reconnu par les navigateurs modernes. */
    return location.protocol === 'https:' || location.hostname === 'localhost' ||
      location.hostname === '127.0.0.1' || location.hostname === '[::1]';
  };

  PWAInstallManager.prototype.isTVMode = function () {
    var root = document.documentElement;
    return !!(root && root.classList && root.classList.contains('tv-mode'));
  };

  PWAInstallManager.prototype.supportsIOSHomeScreenInstructions = function () {
    var navigator = window.navigator;
    var coarsePointer;

    /* `navigator.standalone` est la capacité Apple disponible dans Safari
       iOS/iPadOS. On exige aussi une capacité tactile réelle afin de ne pas
       présenter les consignes iPhone/iPad à Safari desktop si celui-ci
       exposait la même propriété. Aucun modèle, OS ou user-agent n'est déduit. */
    if (!navigator || typeof navigator.standalone !== 'boolean') return false;
    if (typeof navigator.maxTouchPoints === 'number') return navigator.maxTouchPoints > 0;

    try {
      coarsePointer = window.matchMedia && window.matchMedia('(pointer: coarse)');
      return !!(coarsePointer && coarsePointer.matches);
    } catch (error) {
      return false;
    }
  };

  PWAInstallManager.prototype.getPlatform = function () {
    if (this.installed || this.isTVMode() || !this.isSecureInstallContext()) return 'unavailable';
    if (this.deferredPrompt) return 'native';
    if (this.supportsIOSHomeScreenInstructions()) return 'ios';
    return 'unavailable';
  };

  PWAInstallManager.prototype.updateAvailability = function () {
    this.platform = this.getPlatform();
    this.canInstall = !!(
      this.promptView && this.promptView.isAvailable && !this.installed &&
      !this.sessionSuppressed && !this.isInCooldown() && this.platform !== 'unavailable'
    );
  };

  PWAInstallManager.prototype.schedulePresentation = function () {
    var self = this;
    if (this.presentationTimer !== null || this.readyForPresentation) return;

    this.presentationTimer = window.setTimeout(function () {
      self.presentationTimer = null;
      self.readyForPresentation = true;
      self.maybeShowPrompt();
    }, promptDelay);
  };

  PWAInstallManager.prototype.handleBeforeInstallPrompt = function (event) {
    debug('beforeinstallprompt received');

    /* Le navigateur ne doit pas afficher son UI automatique : notre CTA ne
       devient visible qu'après le délai, la vérification d'état et le cooldown. */
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    this.clearBootstrapDeferredPrompt(event);

    this.syncInstalledState();

    /* Un second événement reçu pendant le prompt natif en cours est ignoré :
       l'instance active garde seule le droit de résoudre userChoice. */
    if (this.installed || this.sessionSuppressed || this.promptInProgress ||
      this.isTVMode() || !this.isSecureInstallContext()) {
      this.deferredPrompt = null;
      this.updateAvailability();
      return;
    }

    /* Un navigateur ne devrait émettre cet événement qu'une fois par page,
       mais conserver le premier objet évite qu'un doublon ne remplace une
       capacité déjà validée alors que notre boîte est visible. */
    if (this.deferredPrompt) {
      debug('Duplicate beforeinstallprompt ignored');
      return;
    }

    /* La référence est volontairement conservée jusqu'au clic. Elle est
       effacée AVANT prompt() dans install(), ce qui interdit un second appel. */
    this.deferredPrompt = event;
    this.openInstallationChannel();
    this.updateAvailability();

    /* Cas rare (navigateur Apple/Chromium hybride) : si les consignes iOS
       étaient déjà visibles et qu'une vraie capacité native apparaît, on
       remplace les consignes par le bouton réellement actionnable. */
    if (this.shouldShowPrompt && this.promptView && this.promptView.isOpen) {
      this.shouldShowPrompt = this.promptView.show('native');
      if (this.shouldShowPrompt) debug('Prompt displayed');
      return;
    }

    this.maybeShowPrompt();
  };

  PWAInstallManager.prototype.handleAppInstalled = function (fromAnotherContext) {
    var bootstrapState = this.getBootstrapState();
    var wasConfirmed = this.installationConfirmedThisSession;

    /* appinstalled est un signal navigateur réel. Il peut être relayé à un
       autre onglet via BroadcastChannel, sans jamais persister un état
       « installé » dans localStorage. */
    if (bootstrapState) {
      bootstrapState.appInstalled = true;
      bootstrapState.deferredPrompt = null;
    }
    this.installationConfirmedThisSession = true;
    this.installed = true;
    this.displayMode = 'appinstalled';
    this.deferredPrompt = null;
    this.promptInProgress = false;
    this.shouldShowPrompt = false;
    this.sessionSuppressed = true;
    this.clearDismissalCooldown();
    this.updateAvailability();

    if (this.promptView) {
      this.promptView.setInstalling(false);
      this.promptView.hide(true);
    }
    if (!fromAnotherContext && !wasConfirmed) {
      this.openInstallationChannel();
      this.broadcastAppInstalled();
    }
    debug(fromAnotherContext ? 'App installed in another tab' : 'App installed');
  };

  PWAInstallManager.prototype.handleDisplayModeChange = function () {
    var wasInstalled = this.installed;
    this.syncInstalledState();

    if (!this.installed && !wasInstalled) this.maybeShowPrompt();
  };

  PWAInstallManager.prototype.isInCooldown = function () {
    var raw = readStorage(DISMISSAL_STORAGE_KEY);
    var dismissedAt = parseInt(raw, 10);
    var elapsed;

    if (!raw || isNaN(dismissedAt) || dismissedAt <= 0 || dismissedAt > now()) {
      if (raw) removeStorage(DISMISSAL_STORAGE_KEY);
      return false;
    }

    elapsed = now() - dismissedAt;
    if (elapsed >= cooldownMs) {
      removeStorage(DISMISSAL_STORAGE_KEY);
      return false;
    }
    return true;
  };

  PWAInstallManager.prototype.recordDismissalCooldown = function () {
    writeStorage(DISMISSAL_STORAGE_KEY, String(now()));
  };

  PWAInstallManager.prototype.clearDismissalCooldown = function () {
    removeStorage(DISMISSAL_STORAGE_KEY);
  };

  PWAInstallManager.prototype.handleStorageChange = function (event) {
    if (!event || event.key !== DISMISSAL_STORAGE_KEY || !event.newValue ||
      this.installed || this.promptInProgress || this.sessionSuppressed) return;

    /* Le refus dans un autre onglet doit aussi fermer cette proposition. La
       clé reste uniquement un cooldown : elle ne signale jamais l'installation. */
    if (!this.isInCooldown()) return;

    this.deferredPrompt = null;
    this.clearBootstrapDeferredPrompt();
    this.sessionSuppressed = true;
    this.shouldShowPrompt = false;
    this.updateAvailability();
    if (this.promptView) {
      this.promptView.setInstalling(false);
      this.promptView.hide(true);
    }
    debug('User dismissed', 'another tab');
  };

  PWAInstallManager.prototype.getUnavailableReason = function () {
    if (this.installed) return 'app already installed';
    if (this.isInCooldown()) return 'cooldown active';
    if (this.isTVMode()) return 'TV mode';
    if (!this.isSecureInstallContext()) return 'secure context required (HTTPS)';
    return 'no reliable browser installation mechanism';
  };

  PWAInstallManager.prototype.logUnavailable = function (reason) {
    if (this.lastUnavailableReason === reason) return;
    this.lastUnavailableReason = reason;
    debug('Installation unavailable', reason);
  };

  PWAInstallManager.prototype.getPresentationBlockReason = function () {
    var shareModal = document.getElementById('share-modal');

    if (document.visibilityState && document.visibilityState !== 'visible') return 'document hidden';
    if (document.getElementById('player-frame')) return 'video player page';
    if (shareModal && shareModal.classList && shareModal.classList.contains('open')) return 'another dialog open';

    try {
      if (window.top !== window) return 'embedded document';
    } catch (error) {
      return 'embedded document';
    }

    return '';
  };

  PWAInstallManager.prototype.maybeShowPrompt = function () {
    var platform;
    var blockedReason;

    if (!this.readyForPresentation || this.shouldShowPrompt ||
      (this.promptView && this.promptView.isOpen)) return false;

    this.syncInstalledState();
    if (this.installed || this.sessionSuppressed) return false;

    if (!this.promptView || !this.promptView.isAvailable) {
      this.logUnavailable('install prompt view unavailable');
      return false;
    }

    if (this.isInCooldown()) {
      debug('Installation unavailable', 'cooldown active');
      return false;
    }

    platform = this.platform;
    if (platform === 'unavailable') {
      this.logUnavailable(this.getUnavailableReason());
      return false;
    }

    blockedReason = this.getPresentationBlockReason();
    if (blockedReason) {
      debug('Installation deferred', blockedReason);
      return false;
    }

    /* Le canal inter-onglets reste strictement optionnel et n'est ouvert que
       lorsqu'une expérience d'installation réellement présentable existe. */
    this.openInstallationChannel();
    this.shouldShowPrompt = this.promptView.show(platform);
    if (!this.shouldShowPrompt) return false;

    if (platform === 'ios') {
      debug('iOS installation instructions');
    } else {
      debug('Prompt displayed');
    }
    return true;
  };

  PWAInstallManager.prototype.dismiss = function () {
    /* Pendant le dialogue natif, userChoice reste la seule source de résultat.
       Un clic sur l'arrière-plan ne doit pas court-circuiter ce cycle. */
    if (this.installed || this.promptInProgress) return;

    this.recordDismissalCooldown();
    this.deferredPrompt = null;
    this.clearBootstrapDeferredPrompt();
    this.sessionSuppressed = true;
    this.shouldShowPrompt = false;
    this.updateAvailability();
    if (this.promptView) this.promptView.hide();
    debug('User dismissed');
  };

  PWAInstallManager.prototype.install = function () {
    var installEvent;
    var promptResult;
    var choice;
    var self = this;

    this.syncInstalledState();
    if (this.installed || this.sessionSuppressed || this.isInCooldown() ||
      !this.deferredPrompt || this.promptInProgress || this.platform !== 'native' || !this.canInstall) {
      this.logUnavailable(this.getUnavailableReason());
      return null;
    }

    this.openInstallationChannel();

    /* Une instance de BeforeInstallPromptEvent ne peut être utilisée qu'une
       seule fois. La référence disparaît donc avant l'appel à prompt(). */
    installEvent = this.deferredPrompt;
    this.deferredPrompt = null;
    this.promptInProgress = true;
    this.updateAvailability();
    if (this.promptView) this.promptView.setInstalling(true);

    try {
      promptResult = installEvent.prompt();
    } catch (error) {
      this.handlePromptError(error);
      return null;
    }

    /* Certaines implémentations retournent aussi une Promise de prompt(). On
       absorbe une éventuelle erreur sans attendre celle-ci : userChoice reste
       la source de vérité demandée par l'API. */
    if (promptResult && typeof promptResult.catch === 'function') {
      promptResult.catch(function (error) {
        self.handlePromptError(error);
      });
    }

    choice = installEvent.userChoice;
    if (!choice || typeof choice.then !== 'function') {
      this.handlePromptError(new Error('beforeinstallprompt.userChoice unavailable'));
      return null;
    }

    return choice.then(function (result) {
      self.handlePromptChoice(result);
      return result;
    }, function (error) {
      self.handlePromptError(error);
      return null;
    });
  };

  PWAInstallManager.prototype.handlePromptChoice = function (choice) {
    var accepted;

    /* appinstalled peut exceptionnellement arriver avant userChoice. Dans ce
       cas, son traitement a déjà fermé proprement la modal ; on conserve tout
       de même la trace de l'acceptation lorsque la Promise se résout. */
    if (!this.promptInProgress && this.sessionSuppressed) {
      if (choice && choice.outcome === 'accepted') debug('User accepted');
      return;
    }

    this.promptInProgress = false;
    accepted = !!(choice && choice.outcome === 'accepted');
    this.sessionSuppressed = true;
    this.shouldShowPrompt = false;

    if (accepted) {
      debug('User accepted');
    } else {
      this.recordDismissalCooldown();
      debug('User dismissed');
    }

    this.updateAvailability();
    if (this.promptView) {
      this.promptView.setInstalling(false);
      this.promptView.hide();
    }
  };

  PWAInstallManager.prototype.handlePromptError = function (error) {
    if (!this.promptInProgress) return;

    this.promptInProgress = false;
    this.sessionSuppressed = true;
    this.shouldShowPrompt = false;
    this.recordDismissalCooldown();
    this.updateAvailability();
    if (this.promptView) {
      this.promptView.setInstalling(false);
      this.promptView.hide();
    }
    debug('Installation prompt failed', error);
  };

  PWAInstallManager.prototype.getState = function () {
    this.syncInstalledState();
    return {
      canInstall: this.canInstall,
      isInstalled: this.installed,
      shouldShowPrompt: this.shouldShowPrompt,
      platform: this.platform,
      installationInstructions: this.platform === 'ios'
        ? IOS_INSTALLATION_INSTRUCTIONS.slice(0)
        : [],
      displayMode: this.displayMode,
      cooldownDays: cooldownDays,
      delay: promptDelay
    };
  };

  /* API publique volontairement petite : utile au débogage et à une future
     intégration dans un bouton explicite, sans exposer l'événement navigateur
     à d'autres scripts. */
  function exposePublicApi(manager) {
    var api = {
      getState: function () { return manager.getState(); },
      install: function () { return manager.install(); },
      dismiss: function () { return manager.dismiss(); },
      refresh: function () {
        manager.maybeShowPrompt();
        return manager.getState();
      },
      setDebug: function (enabled) {
        debugEnabled = enabled === true;
        if (debugEnabled) debug('Debug enabled');
        return debugEnabled;
      },
      config: {
        INSTALL_PROMPT_DELAY: promptDelay,
        INSTALL_PROMPT_COOLDOWN_DAYS: cooldownDays
      }
    };

    function stateProperty(name) {
      try {
        Object.defineProperty(api, name, {
          enumerable: true,
          get: function () { return manager.getState()[name]; }
        });
      } catch (error) {
        /* Fallback non réactif pour un moteur vraiment ancien (aucun impact
           sur le fonctionnement de la PWA ou de l'interface TV). */
        api[name] = manager.getState()[name];
      }
    }

    stateProperty('canInstall');
    stateProperty('isInstalled');
    stateProperty('shouldShowPrompt');
    stateProperty('platform');
    stateProperty('installationInstructions');

    window.NokaTVPWAInstall = api;
  }


  PWAInstallManager.exposePublicApi = exposePublicApi;
  window.NokaTVPWAInstallManager = PWAInstallManager;
})(window, document);
