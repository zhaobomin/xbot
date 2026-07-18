#!/usr/bin/env node
/**
 * xbot WhatsApp Bridge
 * 
 * This bridge connects WhatsApp Web to xbot's Python backend
 * via WebSocket. It handles authentication, message forwarding,
 * and reconnection logic.
 * 
 * Usage:
 *   npm run build && npm start
 *   
 * Or with custom settings:
 *   BRIDGE_PORT=3001 AUTH_DIR=~/.xbot/whatsapp npm start
 */

// Polyfill crypto for Baileys in ESM
import { webcrypto } from 'crypto';
if (!globalThis.crypto) {
  Object.defineProperty(globalThis, 'crypto', {
    value: webcrypto,
    writable: true,
    configurable: true,
  });
}

import { BridgeServer } from './server.js';
import { homedir } from 'os';
import { join } from 'path';

const PORT = parseInt(process.env.BRIDGE_PORT || '3001', 10);
const AUTH_DIR = process.env.AUTH_DIR || join(homedir(), '.xbot', 'whatsapp-auth');
const TOKEN = process.env.BRIDGE_TOKEN || undefined;

if (!Number.isInteger(PORT) || PORT < 1 || PORT > 65535) {
  console.error(`Invalid BRIDGE_PORT: ${process.env.BRIDGE_PORT}`);
  process.exit(1);
}

console.log('🐈 xbot WhatsApp Bridge');
console.log('========================\n');

const server = new BridgeServer(PORT, AUTH_DIR, TOKEN);

// Handle graceful shutdown
process.on('SIGINT', async () => {
  console.log('\n\nShutting down...');
  await server.stop();
  process.exit(0);
});

process.on('SIGTERM', async () => {
  await server.stop();
  process.exit(0);
});

// Start the server
server.start().catch((error) => {
  console.error('Failed to start bridge:', error);
  process.exit(1);
});
