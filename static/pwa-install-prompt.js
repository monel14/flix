/* ==========================================================================
   NokaTV — Vue de l'invitation d'installation PWA
   --------------------------------------------------------------------------
   Cette vue ne décide jamais qu'une installation est possible. Elle reçoit
   uniquement le mode validé par PWAInstallManager et gère le dialogue
   accessible (focus, Échap, Tab, fermeture et animation).
   ========================================================================== */
(function (window, document) {
  'use strict';

  if (!window || !document) return;

  function reducedMotionIsPreferred() {
    try {
      return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    } catch (error) {
      return false;
    }
  }

  function isVisible(element) {
    if (!element || element.hidden) return false;
    var box = element.getBoundingClientRect ? element.getBoundingClientRect() : null;
    return !!box && box.width > 0 && box.height > 0;
  }

  function isConnected(element) {
    if (!element) return false;
    if (typeof element.isConnected === 'boolean') return element.isConnected;
    return !!(document.documentElement && document.documentElement.contains &&
      document.documentElement.contains(element));
  }

  /* -----------------------------------------------------------------------
     Vue : ne connaît pas la détection navigateur ; elle rend la modal et
     assure la fermeture, le focus initial, le piège Tab et Échap.
  ----------------------------------------------------------------------- */
  function PWAInstallPrompt(options) {
    options = options || {};
    this.onInstall = options.onInstall || function () {};
    this.onDismiss = options.onDismiss || function () {};
    this.overlay = document.getElementById('pwa-install-modal');
    this.dialog = document.getElementById('pwa-install-dialog');
    this.title = document.getElementById('pwa-install-title');
    this.description = document.getElementById('pwa-install-description');
    this.instructions = document.getElementById('pwa-install-ios-instructions');
    this.closeButton = document.getElementById('pwa-install-close');
    this.laterButton = document.getElementById('pwa-install-later');
    this.actionButton = document.getElementById('pwa-install-action');
    this.actionLabel = document.getElementById('pwa-install-action-label');
    this.isOpen = false;
    this.previousActiveElement = null;
    this.hideTimer = null;
    this.isAvailable = !!(
      this.overlay && this.dialog && this.title && this.description &&
      this.instructions && this.closeButton && this.laterButton && this.actionButton
    );

    if (!this.isAvailable) return;

    this.bindEvents();
  }

  PWAInstallPrompt.prototype.bindEvents = function () {
    var self = this;

    this.actionButton.addEventListener('click', function () {
      if (!self.actionButton.hidden && !self.actionButton.disabled) self.onInstall();
    });
    this.laterButton.addEventListener('click', function () {
      if (!self.laterButton.disabled) self.onDismiss('later');
    });
    this.closeButton.addEventListener('click', function () {
      if (!self.closeButton.disabled) self.onDismiss('close');
    });
    this.overlay.addEventListener('click', function (event) {
      if (event.target === self.overlay && !self.dialog.hasAttribute('aria-busy')) {
        self.onDismiss('backdrop');
      }
    });
    this.dialog.addEventListener('keydown', function (event) { self.handleKeydown(event); });
  };

  PWAInstallPrompt.prototype.render = function (platform) {
    var isIOS = platform === 'ios';

    this.title.textContent = "Installer l'application";
    this.description.textContent = isIOS
      ? "Ajoutez l'application à votre écran d'accueil pour y accéder rapidement."
      : "Installez l'application pour y accéder plus rapidement depuis votre appareil.";

    this.instructions.hidden = !isIOS;
    this.actionButton.hidden = isIOS;
    this.dialog.setAttribute(
      'aria-describedby',
      isIOS ? 'pwa-install-description pwa-install-ios-instructions' : 'pwa-install-description'
    );
    this.setInstalling(false);
  };

  PWAInstallPrompt.prototype.show = function (platform) {
    var self = this;
    if (!this.isAvailable) return false;

    window.clearTimeout(this.hideTimer);
    this.render(platform);

    if (this.isOpen) return true;

    this.isOpen = true;
    this.previousActiveElement = document.activeElement;
    this.overlay.hidden = false;
    this.overlay.setAttribute('aria-hidden', 'false');
    document.body.classList.add('pwa-install-open');

    function activate() {
      if (!self.isOpen) return;
      self.overlay.classList.add('is-open');
      self.focusInitialControl();
    }

    if (window.requestAnimationFrame) {
      window.requestAnimationFrame(activate);
    } else {
      window.setTimeout(activate, 0);
    }
    return true;
  };

  PWAInstallPrompt.prototype.hide = function (immediately) {
    var self = this;
    var restoreFocus = this.isOpen;
    var previous = this.previousActiveElement;

    if (!this.isAvailable) return;

    window.clearTimeout(this.hideTimer);
    this.isOpen = false;
    this.overlay.classList.remove('is-open');
    this.overlay.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('pwa-install-open');

    function completeHide() {
      if (self.isOpen) return;
      self.overlay.hidden = true;
      if (restoreFocus && previous && typeof previous.focus === 'function' && isConnected(previous)) {
        try {
          previous.focus({ preventScroll: true });
        } catch (error) {
          previous.focus();
        }
      }
      self.previousActiveElement = null;
    }

    if (immediately || reducedMotionIsPreferred()) {
      completeHide();
    } else {
      this.hideTimer = window.setTimeout(completeHide, 180);
    }
  };

  PWAInstallPrompt.prototype.setInstalling = function (isInstalling) {
    if (!this.isAvailable) return;

    /* Le prompt natif est lui-même modal. Pendant son ouverture, les actions
       de la boîte personnalisée ne doivent pas sembler actives alors que
       userChoice est encore en cours de résolution. */
    this.closeButton.disabled = !!isInstalling;
    this.laterButton.disabled = !!isInstalling;
    if (isInstalling) {
      this.dialog.setAttribute('aria-busy', 'true');
    } else {
      this.dialog.removeAttribute('aria-busy');
    }

    if (this.actionButton.hidden) return;

    this.actionButton.disabled = !!isInstalling;
    if (isInstalling) {
      this.actionButton.setAttribute('aria-busy', 'true');
      if (this.actionLabel) this.actionLabel.textContent = 'Ouverture…';
    } else {
      this.actionButton.removeAttribute('aria-busy');
      if (this.actionLabel) this.actionLabel.textContent = 'Installer';
    }
  };

  PWAInstallPrompt.prototype.focusInitialControl = function () {
    var target = this.actionButton.hidden ? this.closeButton : this.actionButton;
    if (!isVisible(target)) target = this.closeButton;
    if (!target || typeof target.focus !== 'function') return;

    try {
      target.focus({ preventScroll: true });
    } catch (error) {
      target.focus();
    }
  };

  PWAInstallPrompt.prototype.focusableControls = function () {
    var selector = 'button:not([disabled]):not([hidden]), [href], input:not([disabled]):not([hidden]), select:not([disabled]):not([hidden]), textarea:not([disabled]):not([hidden]), [tabindex]:not([tabindex="-1"]):not([hidden])';
    var nodes = this.dialog.querySelectorAll(selector);
    var controls = [];
    var index;

    for (index = 0; index < nodes.length; index += 1) {
      if (isVisible(nodes[index])) controls.push(nodes[index]);
    }
    return controls;
  };

  PWAInstallPrompt.prototype.handleKeydown = function (event) {
    if (!this.isOpen) return;

    var key = event.key || '';
    if (key === 'Escape' || event.keyCode === 27) {
      event.preventDefault();
      if (!this.dialog.hasAttribute('aria-busy')) this.onDismiss('escape');
      return;
    }

    if (key !== 'Tab' && event.keyCode !== 9) return;

    var controls = this.focusableControls();
    if (!controls.length) return;

    var first = controls[0];
    var last = controls[controls.length - 1];
    var active = document.activeElement;

    if (event.shiftKey && (active === first || active === this.dialog)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };


  var Manager = window.NokaTVPWAInstallManager;
  if (!Manager) return;

  var manager;
  var promptView = new PWAInstallPrompt({
    onInstall: function () { manager.install(); },
    onDismiss: function () { manager.dismiss(); }
  });

  /* Même si le HTML venait à être absent, le manager reste actif afin de
     neutraliser le prompt automatique sans jamais casser la page. */
  manager = new Manager(promptView);
  Manager.exposePublicApi(manager);
  manager.initialise();
})(window, document);
