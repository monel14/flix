#!/usr/bin/env node
'use strict';

/* Régression de la vue accessible de l'invitation PWA, sans jsdom ni build. */
const assert = require('assert').strict;
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(
  path.resolve(__dirname, '..', 'static', 'pwa-install-prompt.js'),
  'utf8'
);

function createClassList() {
  const values = new Set();
  return {
    add: function () {
      Array.prototype.forEach.call(arguments, function (value) { values.add(value); });
    },
    remove: function () {
      Array.prototype.forEach.call(arguments, function (value) { values.delete(value); });
    },
    contains: function (value) { return values.has(value); }
  };
}

function createEnvironment() {
  const nodes = Object.create(null);
  const document = {
    activeElement: null,
    body: { classList: createClassList() },
    documentElement: {
      contains: function (element) { return !!(element && element.connected !== false); }
    },
    getElementById: function (id) { return nodes[id] || null; }
  };

  function createElement(id) {
    const listeners = Object.create(null);
    const attributes = Object.create(null);
    return {
      id: id,
      hidden: false,
      disabled: false,
      connected: true,
      classList: createClassList(),
      textContent: '',
      focusCalls: 0,
      setAttribute: function (name, value) { attributes[name] = String(value); },
      getAttribute: function (name) {
        return Object.prototype.hasOwnProperty.call(attributes, name) ? attributes[name] : null;
      },
      hasAttribute: function (name) {
        return Object.prototype.hasOwnProperty.call(attributes, name);
      },
      removeAttribute: function (name) { delete attributes[name]; },
      addEventListener: function (type, handler) {
        (listeners[type] || (listeners[type] = [])).push(handler);
      },
      fire: function (type, event) {
        event = event || { target: this };
        (listeners[type] || []).slice().forEach(function (handler) { handler(event); });
      },
      getBoundingClientRect: function () {
        return this.hidden ? { width: 0, height: 0 } : { width: 20, height: 20 };
      },
      focus: function () {
        this.focusCalls += 1;
        document.activeElement = this;
      },
      querySelectorAll: function () { return []; }
    };
  }

  [
    'pwa-install-modal',
    'pwa-install-dialog',
    'pwa-install-title',
    'pwa-install-description',
    'pwa-install-ios-instructions',
    'pwa-install-close',
    'pwa-install-later',
    'pwa-install-action',
    'pwa-install-action-label'
  ].forEach(function (id) { nodes[id] = createElement(id); });

  const trigger = createElement('trigger');
  document.activeElement = trigger;
  nodes['pwa-install-dialog'].querySelectorAll = function () {
    return [
      nodes['pwa-install-close'],
      nodes['pwa-install-later'],
      nodes['pwa-install-action']
    ].filter(function (node) { return !node.hidden && !node.disabled; });
  };

  let capturedManager;
  function FakeManager(promptView) {
    this.promptView = promptView;
    this.installCalls = 0;
    this.dismissCalls = 0;
    capturedManager = this;
  }
  FakeManager.prototype.install = function () { this.installCalls += 1; };
  FakeManager.prototype.dismiss = function () { this.dismissCalls += 1; };
  FakeManager.prototype.initialise = function () {};
  FakeManager.exposePublicApi = function () {};

  const window = {
    NokaTVPWAInstallManager: FakeManager,
    matchMedia: function () { return { matches: false }; },
    requestAnimationFrame: function (callback) { callback(); },
    clearTimeout: function () {},
    setTimeout: function (callback) { callback(); return 1; }
  };

  vm.runInNewContext(source, { window: window, document: document });
  return {
    manager: capturedManager,
    nodes: nodes,
    prompt: capturedManager.promptView,
    trigger: trigger,
    document: document
  };
}

function keyboardEvent(key, shiftKey) {
  return {
    key: key,
    keyCode: key === 'Escape' ? 27 : 9,
    shiftKey: shiftKey === true,
    prevented: false,
    preventDefault: function () { this.prevented = true; }
  };
}

function testNativeDialog() {
  const environment = createEnvironment();
  const nodes = environment.nodes;
  const prompt = environment.prompt;

  assert.equal(prompt.show('native'), true);
  assert.equal(nodes['pwa-install-modal'].hidden, false);
  assert.equal(nodes['pwa-install-modal'].getAttribute('aria-hidden'), 'false');
  assert.equal(nodes['pwa-install-action'].hidden, false);
  assert.equal(nodes['pwa-install-ios-instructions'].hidden, true);
  assert.equal(environment.document.body.classList.contains('pwa-install-open'), true);
  assert.equal(environment.document.activeElement, nodes['pwa-install-action']);

  nodes['pwa-install-action'].fire('click');
  assert.equal(environment.manager.installCalls, 1);

  // Le dialogue custom ne laisse pas déclencher une seconde action pendant la
  // résolution du prompt natif : tous les contrôles et le backdrop sont sûrs.
  prompt.setInstalling(true);
  assert.equal(nodes['pwa-install-action'].disabled, true);
  assert.equal(nodes['pwa-install-close'].disabled, true);
  assert.equal(nodes['pwa-install-later'].disabled, true);
  assert.equal(nodes['pwa-install-dialog'].hasAttribute('aria-busy'), true);
  assert.equal(nodes['pwa-install-action-label'].textContent, 'Ouverture…');
  nodes['pwa-install-action'].fire('click');
  nodes['pwa-install-close'].fire('click');
  nodes['pwa-install-later'].fire('click');
  nodes['pwa-install-modal'].fire('click', { target: nodes['pwa-install-modal'] });
  const busyEscape = keyboardEvent('Escape');
  nodes['pwa-install-dialog'].fire('keydown', busyEscape);
  assert.equal(environment.manager.installCalls, 1);
  assert.equal(environment.manager.dismissCalls, 0);
  assert.equal(busyEscape.prevented, true);

  prompt.setInstalling(false);
  assert.equal(nodes['pwa-install-action'].disabled, false);
  assert.equal(nodes['pwa-install-close'].disabled, false);
  assert.equal(nodes['pwa-install-later'].disabled, false);
  assert.equal(nodes['pwa-install-dialog'].hasAttribute('aria-busy'), false);
  assert.equal(nodes['pwa-install-action-label'].textContent, 'Installer');

  // Piège de focus Tab et Shift+Tab, puis fermeture Échap / backdrop.
  environment.document.activeElement = nodes['pwa-install-action'];
  const tab = keyboardEvent('Tab');
  nodes['pwa-install-dialog'].fire('keydown', tab);
  assert.equal(tab.prevented, true);
  assert.equal(environment.document.activeElement, nodes['pwa-install-close']);

  const shiftTab = keyboardEvent('Tab', true);
  nodes['pwa-install-dialog'].fire('keydown', shiftTab);
  assert.equal(shiftTab.prevented, true);
  assert.equal(environment.document.activeElement, nodes['pwa-install-action']);

  const escape = keyboardEvent('Escape');
  nodes['pwa-install-dialog'].fire('keydown', escape);
  assert.equal(escape.prevented, true);
  assert.equal(environment.manager.dismissCalls, 1);

  nodes['pwa-install-modal'].fire('click', { target: nodes['pwa-install-modal'] });
  assert.equal(environment.manager.dismissCalls, 2);

  prompt.hide(true);
  assert.equal(nodes['pwa-install-modal'].hidden, true);
  assert.equal(nodes['pwa-install-modal'].getAttribute('aria-hidden'), 'true');
  assert.equal(environment.document.body.classList.contains('pwa-install-open'), false);
  assert.equal(environment.document.activeElement, environment.trigger);
  assert.equal(environment.trigger.focusCalls, 1);
}

function testIOSInstructions() {
  const environment = createEnvironment();
  const nodes = environment.nodes;
  const prompt = environment.prompt;

  assert.equal(prompt.show('ios'), true);
  assert.equal(nodes['pwa-install-action'].hidden, true);
  assert.equal(nodes['pwa-install-ios-instructions'].hidden, false);
  assert.equal(environment.document.activeElement, nodes['pwa-install-close']);
  nodes['pwa-install-action'].fire('click');
  assert.equal(environment.manager.installCalls, 0);

  // Même sans bouton natif, les contrôles de fermeture restent cohérents avec
  // l'état d'occupation du dialogue.
  prompt.setInstalling(true);
  assert.equal(nodes['pwa-install-close'].disabled, true);
  assert.equal(nodes['pwa-install-later'].disabled, true);
}

testNativeDialog();
testIOSInstructions();
process.stdout.write('PWA install prompt accessibility regression checks: OK\n');
