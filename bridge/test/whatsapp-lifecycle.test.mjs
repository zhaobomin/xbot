import assert from 'node:assert/strict';
import { test } from 'node:test';

import { Boom } from '@hapi/boom';
import { DisconnectReason } from '@whiskeysockets/baileys';

import * as serverModule from '../dist/server.js';
import * as whatsappModule from '../dist/whatsapp.js';

const { BridgeServer } = serverModule;
const { WhatsAppClient } = whatsappModule;

function makeClient(overrides = {}) {
  const received = [];
  const statuses = [];
  const client = new WhatsAppClient({
    authDir: '/tmp/xbot-whatsapp-test/auth',
    onMessage: (message) => received.push(message),
    onQR: () => {},
    onStatus: (status) => statuses.push(status),
    ...overrides,
  });
  return { client, received, statuses };
}

test('only known transient disconnect reasons are retryable', () => {
  const retryable = [
    DisconnectReason.connectionClosed,
    DisconnectReason.connectionLost,
    DisconnectReason.timedOut,
    DisconnectReason.restartRequired,
    DisconnectReason.unavailableService,
  ];
  const terminal = [
    DisconnectReason.loggedOut,
    DisconnectReason.connectionReplaced,
    DisconnectReason.badSession,
    DisconnectReason.multideviceMismatch,
    DisconnectReason.forbidden,
    undefined,
    599,
  ];

  for (const reason of retryable) {
    assert.equal(whatsappModule.isRetryableDisconnect?.(reason), true);
  }
  for (const reason of terminal) {
    assert.equal(whatsappModule.isRetryableDisconnect?.(reason), false);
  }
});

test('a second connect rejects before replacing the live socket', async () => {
  const { client } = makeClient();
  const socket = {};
  client.sock = socket;

  await assert.rejects(
    Promise.race([
      client.connect(),
      new Promise((_, reject) => {
        setTimeout(() => reject(new Error('connect did not reject promptly')), 100);
      }),
    ]),
    /already connected/i,
  );
  assert.equal(client.sock, socket);
});

test('disconnect awaits socket end, cancels reconnect, and marks manual close', async () => {
  const { client } = makeClient();
  let finishEnd;
  const ended = new Promise((resolve) => {
    finishEnd = resolve;
  });
  const socket = { end: () => ended };
  client.sock = socket;
  client.reconnecting = true;
  client.reconnectTimer = setTimeout(() => {}, 60_000);

  const disconnecting = client.disconnect();
  await Promise.resolve();

  assert.equal(client.sock, socket);
  finishEnd();
  await disconnecting;

  assert.equal(client.sock, null);
  assert.equal(client.reconnectTimer, null);
  assert.equal(client.reconnecting, false);
  assert.equal(client.manualDisconnect, true);
});

test('manual and terminal closes do not schedule reconnect timers', async () => {
  for (const { manual, statusCode } of [
    { manual: true, statusCode: DisconnectReason.connectionLost },
    { manual: false, statusCode: DisconnectReason.loggedOut },
  ]) {
    const { client } = makeClient();
    client.sock = {};
    client.manualDisconnect = manual;

    await client.handleConnectionUpdate({
      connection: 'close',
      lastDisconnect: {
        error: new Boom('closed', { statusCode }),
        date: new Date(),
      },
    });

    assert.equal(client.sock, null);
    assert.equal(client.reconnectTimer, null);
  }
});

test('one malformed message does not abort the rest of an inbound batch', async () => {
  const { client, received } = makeClient();
  const malformed = {};
  Object.defineProperty(malformed, 'key', {
    get() {
      throw new Error('malformed message');
    },
  });

  await client.handleMessagesUpsert({
    type: 'notify',
    messages: [
      malformed,
      {
        key: {
          fromMe: false,
          remoteJid: '123@s.whatsapp.net',
          id: 'good',
        },
        message: { conversation: 'hello' },
        messageTimestamp: 123,
      },
    ],
  });

  assert.equal(received.length, 1);
  assert.equal(received[0].id, 'good');
  assert.equal(received[0].content, 'hello');
});

test('send command validation rejects missing or invalid fields before sending', async () => {
  const valid = { type: 'send', to: '123@s.whatsapp.net', text: 'hello' };
  assert.deepEqual(serverModule.validateSendCommand?.(valid), valid);

  let sent = 0;
  const server = new BridgeServer(0, '/tmp/xbot-whatsapp-test/auth');
  server.wa = {
    sendMessage: async () => {
      sent += 1;
    },
  };

  for (const command of [
    null,
    {},
    { type: 'other', to: valid.to, text: valid.text },
    { type: 'send', to: '', text: valid.text },
    { type: 'send', to: valid.to, text: 42 },
  ]) {
    assert.throws(() => serverModule.validateSendCommand?.(command));
    await assert.rejects(server.handleCommand(command));
  }
  assert.equal(sent, 0);
});

test('server stop is bounded when the close callback never arrives', async () => {
  const server = new BridgeServer(0, '/tmp/xbot-whatsapp-test/auth');
  let closed = 0;
  let terminated = 0;
  const client = {
    close: () => {
      closed += 1;
    },
    terminate: () => {
      terminated += 1;
    },
  };
  server.clients = new Set([client]);
  server.wss = { close: () => {} };

  const result = await Promise.race([
    server.stop().then(() => 'stopped'),
    new Promise((resolve) => setTimeout(() => resolve('timeout'), 1_000)),
  ]);

  assert.equal(result, 'stopped');
  assert.equal(closed, 1);
  assert.equal(terminated, 1);
  assert.equal(server.wss, null);
});
