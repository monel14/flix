#!/usr/bin/env node
'use strict';

/*
 * Régression sans dépendance navigateur : exécute le gestionnaire dans un
 * contexte DOM minimal afin de vérifier les courses de l'API PWA que les
 * tests HTTP ne peuvent pas couvrir (beforeinstallprompt, userChoice,
 * appinstalled, onglets et capacités iOS/TV).
 */
const assert = require('assert').strict;
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(
  path.resolve(__dirname, '..', 'static', 'pwa-install-manager.js'),
  'utf8'
);
const baseTemplate = fs.readFileSync(
  path.resolve(__dirname, '..', 'templates', 'base.html'),
  'utf8'
);
const DISMISSAL_KEY = 'nokatv_pwa_install_dismissed_at';

function testEarlyBootstrap() {
  const marker = 'NokaTVPWAInstallBootstrap';
  const markerIndex = baseTemplate.indexOf(marker);
  const start = baseTemplate.lastIndexOf('(function (window)', markerIndex);
  const end = baseTemplate.indexOf('</script>', start);
  const listeners = Object.create(null);
  const window = {
    addEventListener: function (type, handler) {
      (listeners[type] || (listeners[type] = [])).push(handler);
    }
  };

  assert.ok(markerIndex >= 0 && start >= 0 && end > start, 'bootstrap PWA absent du layout');
  vm.runInNewContext(baseTemplate.slice(start, end), { window: window });
  assert.equal(window.NokaTVPWAInstallBootstrap.initialized, true);

  const beforeInstallPrompt = {
    prevented: false,
    preventDefault: function () { this.prevented = true; }
  };
  listeners.beforeinstallprompt.forEach(function (handler) { handler(beforeInstallPrompt); });
  assert.equal(beforeInstallPrompt.prevented, true);
  assert.equal(window.NokaTVPWAInstallBootstrap.deferredPrompt, beforeInstallPrompt);

  listeners.appinstalled.forEach(function (handler) { handler(); });
  assert.equal(window.NokaTVPWAInstallBootstrap.appInstalled, true);
  assert.equal(window.NokaTVPWAInstallBootstrap.deferredPrompt, null);
}

function createEnvironment(options) {
  options = options || {};

  const windowListeners = Object.create(null);
  const documentListeners = Object.create(null);
  const timers = [];
  const storage = new Map(Object.entries(options.storage || {}));
  const channels = [];
  const documentElement = {
    classList: {
      contains: function (name) {
        return name === 'tv-mode' && options.tv === true;
      }
    }
  };
  const document = {
    readyState: options.readyState || 'complete',
    visibilityState: options.visibilityState || 'visible',
    documentElement: documentElement,
    addEventListener: function (type, handler) {
      (documentListeners[type] || (documentListeners[type] = [])).push(handler);
    },
    getElementById: function (id) {
      if (id === 'player-frame' && options.player === true) return {};
      if (id === 'share-modal' && options.shareOpen === true) {
        return { classList: { contains: function (name) { return name === 'open'; } } };
      }
      return null;
    }
  };

  function FakeBroadcastChannel(name) {
    this.name = name;
    this.messages = [];
    this.onmessage = null;
    channels.push(this);
  }
  FakeBroadcastChannel.prototype.postMessage = function (message) {
    this.messages.push(message);
  };

  const window = {
    location: {
      search: '',
      protocol: options.protocol || 'https:',
      hostname: options.hostname || 'example.test'
    },
    navigator: {
      standalone: options.navigatorStandalone,
      maxTouchPoints: options.maxTouchPoints
    },
    isSecureContext: options.secure !== false,
    console: { info: function () {} },
    localStorage: {
      getItem: function (key) { return storage.has(key) ? storage.get(key) : null; },
      setItem: function (key, value) { storage.set(key, String(value)); },
      removeItem: function (key) { storage.delete(key); }
    },
    addEventListener: function (type, handler) {
      (windowListeners[type] || (windowListeners[type] = [])).push(handler);
    },
    matchMedia: function (query) {
      const displayMode = options.displayMode || '';
      return {
        matches: query === '(display-mode: ' + displayMode + ')' ||
          (query === '(pointer: coarse)' && options.coarsePointer === true),
        addEventListener: function () {},
        addListener: function () {}
      };
    },
    setTimeout: function (callback, delay) {
      const timer = { callback: callback, delay: delay, cancelled: false };
      timers.push(timer);
      return timer;
    },
    clearTimeout: function (timer) {
      if (timer) timer.cancelled = true;
    },
    BroadcastChannel: options.channels === false ? undefined : FakeBroadcastChannel
  };
  window.top = options.embedded === true ? {} : window;
  if (options.bootstrap) window.NokaTVPWAInstallBootstrap = options.bootstrap;

  vm.runInNewContext(source, {
    window: window,
    document: document,
    Error: Error,
    parseFloat: parseFloat,
    parseInt: parseInt,
    isNaN: isNaN,
    Date: Date,
    String: String,
    Object: Object
  });

  function createView() {
    return {
      isAvailable: true,
      isOpen: false,
      shown: [],
      hidden: [],
      installing: [],
      show: function (platform) {
        this.isOpen = true;
        this.shown.push(platform);
        return true;
      },
      hide: function (immediately) {
        this.isOpen = false;
        this.hidden.push(immediately);
      },
      setInstalling: function (isInstalling) {
        this.installing.push(isInstalling);
      }
    };
  }

  return {
    Manager: window.NokaTVPWAInstallManager,
    channels: channels,
    createView: createView,
    fire: function (type, event) {
      (windowListeners[type] || []).slice().forEach(function (handler) { handler(event); });
    },
    listenerCount: function (type) { return (windowListeners[type] || []).length; },
    runTimers: function () {
      while (timers.length) {
        const timer = timers.shift();
        if (!timer.cancelled) timer.callback();
      }
    },
    storage: storage,
    window: window
  };
}

function createBeforeInstallPrompt() {
  let resolveChoice;
  let promptCalls = 0;
  const event = {
    prevented: false,
    preventDefault: function () { this.prevented = true; },
    prompt: function () {
      promptCalls += 1;
      return { catch: function () {} };
    },
    userChoice: new Promise(function (resolve) { resolveChoice = resolve; })
  };
  return {
    event: event,
    promptCalls: function () { return promptCalls; },
    resolveChoice: function (result) { resolveChoice(result); }
  };
}

function createManager(environment) {
  const view = environment.createView();
  const manager = new environment.Manager(view);
  manager.initialise();
  return { manager: manager, view: view };
}

function settlePromise() {
  return new Promise(function (resolve) { setImmediate(resolve); });
}

async function main() {
  testEarlyBootstrap();

  let environment;
  let instance;
  let event;
  let lateEvent;

  // Une fenêtre lancée comme PWA ne montre jamais l'invitation, même si un
  // événement inattendu est ensuite reçu.
  environment = createEnvironment({ displayMode: 'standalone' });
  instance = createManager(environment);
  const beforeInstallPromptListeners = environment.listenerCount('beforeinstallprompt');
  instance.manager.initialise();
  assert.equal(environment.listenerCount('beforeinstallprompt'), beforeInstallPromptListeners);
  event = createBeforeInstallPrompt();
  environment.fire('beforeinstallprompt', event.event);
  environment.runTimers();
  assert.equal(instance.manager.getState().isInstalled, true);
  assert.equal(instance.view.shown.length, 0);
  assert.equal(instance.manager.deferredPrompt, null);
  assert.equal(event.event.prevented, true);

  // Le bootstrap dans le head conserve l'événement émis avant les scripts
  // defer, que le manager consomme ensuite sans le perdre.
  event = createBeforeInstallPrompt();
  const bootstrap = { initialized: true, appInstalled: false, deferredPrompt: event.event };
  environment = createEnvironment({ bootstrap: bootstrap });
  instance = createManager(environment);
  environment.runTimers();
  assert.equal(event.event.prevented, true);
  assert.equal(bootstrap.deferredPrompt, null);
  assert.equal(instance.manager.deferredPrompt, event.event);
  assert.deepEqual(instance.view.shown, ['native']);

  // Un doublon avant le clic ne remplace pas la première capacité valide.
  lateEvent = createBeforeInstallPrompt();
  environment.fire('beforeinstallprompt', lateEvent.event);
  assert.equal(lateEvent.event.prevented, true);
  assert.equal(instance.manager.deferredPrompt, event.event);

  // Deux clics, ou un événement tardif pendant le prompt natif, ne peuvent
  // appeler prompt() qu'une seule fois.
  instance.manager.install();
  instance.manager.install();
  assert.equal(event.promptCalls(), 1);
  lateEvent = createBeforeInstallPrompt();
  environment.fire('beforeinstallprompt', lateEvent.event);
  assert.equal(lateEvent.event.prevented, true);
  assert.equal(instance.manager.deferredPrompt, null);
  assert.equal(event.promptCalls(), 1);

  // Le refus userChoice ferme la vue, supprime la capacité pour cette session
  // et inscrit uniquement un cooldown (jamais un état « installé »).
  event.resolveChoice({ outcome: 'dismissed' });
  await settlePromise();
  assert.equal(instance.manager.getState().isInstalled, false);
  assert.equal(instance.manager.sessionSuppressed, true);
  assert.equal(instance.view.isOpen, false);
  assert.ok(environment.storage.has(DISMISSAL_KEY));

  // `accepted` ferme la proposition et bloque cette session, mais ne prétend
  // pas que l'app est installée tant que le navigateur n'a pas émis
  // appinstalled. Aucun cooldown de refus n'est écrit dans ce cas.
  environment = createEnvironment();
  instance = createManager(environment);
  event = createBeforeInstallPrompt();
  environment.fire('beforeinstallprompt', event.event);
  environment.runTimers();
  instance.manager.install();
  event.resolveChoice({ outcome: 'accepted' });
  await settlePromise();
  assert.equal(instance.manager.getState().isInstalled, false);
  assert.equal(instance.manager.sessionSuppressed, true);
  assert.equal(instance.view.isOpen, false);
  assert.equal(environment.storage.has(DISMISSAL_KEY), false);
  environment.fire('appinstalled');
  assert.equal(instance.manager.getState().isInstalled, true);

  // Un appinstalled précoce gagne sur un beforeinstallprompt mémorisé : c'est
  // le cas de course où l'installation se termine avant les scripts defer.
  event = createBeforeInstallPrompt();
  environment = createEnvironment({
    bootstrap: { initialized: true, appInstalled: true, deferredPrompt: event.event }
  });
  instance = createManager(environment);
  environment.runTimers();
  assert.equal(instance.manager.getState().isInstalled, true);
  assert.equal(instance.manager.deferredPrompt, null);
  assert.equal(instance.view.shown.length, 0);

  // appinstalled ferme immédiatement une proposition déjà visible et relaie
  // le seul signal navigateur réel aux autres onglets si l'API est présente.
  environment = createEnvironment();
  instance = createManager(environment);
  event = createBeforeInstallPrompt();
  environment.fire('beforeinstallprompt', event.event);
  environment.runTimers();
  assert.equal(instance.view.isOpen, true);
  environment.fire('appinstalled');
  assert.equal(instance.manager.getState().isInstalled, true);
  assert.equal(instance.manager.deferredPrompt, null);
  assert.equal(instance.view.isOpen, false);
  assert.equal(instance.view.hidden[instance.view.hidden.length - 1], true);
  assert.equal(environment.channels[0].messages.length, 1);
  assert.equal(environment.channels[0].messages[0].type, 'appinstalled');
  lateEvent = createBeforeInstallPrompt();
  environment.fire('beforeinstallprompt', lateEvent.event);
  assert.equal(lateEvent.event.prevented, true);
  assert.equal(instance.manager.deferredPrompt, null);
  assert.equal(instance.view.shown.length, 1);

  // Un signal appinstalled reçu d'un autre onglet ferme lui aussi la vue, sans
  // écrire un marqueur d'installation dans localStorage.
  environment = createEnvironment();
  instance = createManager(environment);
  event = createBeforeInstallPrompt();
  environment.fire('beforeinstallprompt', event.event);
  environment.runTimers();
  environment.channels[0].onmessage({ data: { type: 'appinstalled' } });
  assert.equal(instance.manager.getState().isInstalled, true);
  assert.equal(instance.view.isOpen, false);
  assert.equal(environment.storage.has(DISMISSAL_KEY), false);
  assert.equal(environment.storage.size, 0);

  // Sans beforeinstallprompt ni capacité iOS validée, desktop Chrome et tout
  // navigateur non pris en charge restent silencieux.
  environment = createEnvironment({ channels: false });
  instance = createManager(environment);
  environment.runTimers();
  assert.equal(instance.view.shown.length, 0);
  assert.equal(instance.manager.getState().platform, 'unavailable');

  // Safari iPhone/iPad : standalone Apple + tactile donne seulement le tutoriel
  // système, y compris via le fallback pointer: coarse des anciens moteurs.
  environment = createEnvironment({ navigatorStandalone: false, maxTouchPoints: 5 });
  instance = createManager(environment);
  environment.runTimers();
  assert.deepEqual(instance.view.shown, ['ios']);

  environment = createEnvironment({ navigatorStandalone: false, coarsePointer: true });
  instance = createManager(environment);
  environment.runTimers();
  assert.deepEqual(instance.view.shown, ['ios']);

  // Safari desktop (standalone présent mais aucun tactile) est exclu : aucune
  // heuristique « mobile » ou user-agent ne produit un faux CTA.
  environment = createEnvironment({ navigatorStandalone: false, maxTouchPoints: 0 });
  instance = createManager(environment);
  environment.runTimers();
  assert.equal(instance.view.shown.length, 0);

  // TV, contexte non sécurisé et cooldown existant ne changent jamais le
  // statut installé ; ils empêchent seulement la proposition pour ce contexte.
  environment = createEnvironment({ tv: true, navigatorStandalone: false, maxTouchPoints: 5 });
  instance = createManager(environment);
  environment.runTimers();
  assert.equal(instance.view.shown.length, 0);

  environment = createEnvironment({ secure: false, navigatorStandalone: false, maxTouchPoints: 5 });
  instance = createManager(environment);
  environment.runTimers();
  assert.equal(instance.view.shown.length, 0);

  environment = createEnvironment({ storage: { [DISMISSAL_KEY]: String(Date.now()) } });
  instance = createManager(environment);
  event = createBeforeInstallPrompt();
  environment.fire('beforeinstallprompt', event.event);
  environment.runTimers();
  assert.equal(instance.manager.getState().isInstalled, false);
  assert.equal(instance.manager.getState().canInstall, false);
  instance.manager.install();
  assert.equal(event.promptCalls(), 0);
  assert.equal(instance.view.shown.length, 0);

  // Un « Plus tard » dans un autre onglet propage le cooldown et ferme la
  // proposition locale ; il n'est jamais confondu avec appinstalled.
  environment = createEnvironment();
  instance = createManager(environment);
  event = createBeforeInstallPrompt();
  environment.fire('beforeinstallprompt', event.event);
  environment.runTimers();
  environment.storage.set(DISMISSAL_KEY, String(Date.now()));
  environment.fire('storage', { key: DISMISSAL_KEY, newValue: String(Date.now()) });
  assert.equal(instance.manager.getState().isInstalled, false);
  assert.equal(instance.manager.sessionSuppressed, true);
  assert.equal(instance.manager.deferredPrompt, null);
  assert.equal(instance.view.isOpen, false);

  process.stdout.write('PWA install manager behavioral regression checks: OK\n');
}

main().catch(function (error) {
  process.stderr.write((error && error.stack) || String(error));
  process.stderr.write('\n');
  process.exitCode = 1;
});
