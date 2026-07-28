/**
 * WebSocket server for Python-Node.js bridge communication.
 * Security: binds to 127.0.0.1 only; optional BRIDGE_TOKEN auth.
 */

import { WebSocketServer, WebSocket } from 'ws';
import { WhatsAppClient } from './whatsapp.js';
import logger from './logger.js';

interface SendCommand {
  type: 'send';
  to: string;
  text: string;
}

interface BridgeMessage {
  type: 'message' | 'status' | 'qr' | 'error' | 'sent';
  [key: string]: unknown;
}

export function validateSendCommand(command: unknown): SendCommand {
  if (!command || typeof command !== 'object') {
    throw new Error('Invalid send command');
  }
  const candidate = command as Record<string, unknown>;
  if (candidate.type !== 'send') {
    throw new Error(`Unsupported command type: ${String(candidate.type)}`);
  }
  if (typeof candidate.to !== 'string' || candidate.to.length === 0) {
    throw new Error('Send command requires a non-empty "to" field');
  }
  if (typeof candidate.text !== 'string') {
    throw new Error('Send command requires a string "text" field');
  }
  return {
    type: 'send',
    to: candidate.to,
    text: candidate.text,
  };
}

export class BridgeServer {
  private wss: WebSocketServer | null = null;
  private wa: WhatsAppClient | null = null;
  private clients: Set<WebSocket> = new Set();

  constructor(private port: number, private authDir: string, private token?: string) {}

  async start(): Promise<void> {
    // Bind to localhost only — never expose to external network
    this.wss = new WebSocketServer({ host: '127.0.0.1', port: this.port });
    logger.info({ port: this.port }, 'Bridge server listening');
    if (this.token) logger.info('Token authentication enabled');

    // Initialize WhatsApp client
    this.wa = new WhatsAppClient({
      authDir: this.authDir,
      onMessage: (msg) => this.broadcast({ type: 'message', ...msg }),
      onQR: (qr) => this.broadcast({ type: 'qr', qr }),
      onStatus: (status) => this.broadcast({ type: 'status', status }),
    });

    // Handle WebSocket connections
    this.wss.on('connection', (ws) => {
      if (this.token) {
        // Require auth handshake as first message
        const timeout = setTimeout(() => ws.close(4001, 'Auth timeout'), 5000);
        ws.once('message', (data) => {
          clearTimeout(timeout);
          try {
            const msg = JSON.parse(data.toString());
            if (msg.type === 'auth' && msg.token === this.token) {
              logger.info('Python client authenticated');
              this.setupClient(ws);
            } else {
              ws.close(4003, 'Invalid token');
            }
          } catch {
            ws.close(4003, 'Invalid auth message');
          }
        });
      } else {
        logger.info('Python client connected');
        this.setupClient(ws);
      }
    });

    // Connect to WhatsApp
    await this.wa.connect();
  }

  private setupClient(ws: WebSocket): void {
    this.clients.add(ws);

    ws.on('message', async (data) => {
      try {
        const cmd = await this.handleCommand(JSON.parse(data.toString()));
        this.sendToClient(ws, { type: 'sent', to: cmd.to });
      } catch (error) {
        logger.error({ err: error }, 'Error handling command');
        this.sendToClient(ws, { type: 'error', error: String(error) });
      }
    });

    ws.on('close', () => {
      logger.info('Python client disconnected');
      this.clients.delete(ws);
    });

    ws.on('error', (error) => {
      logger.error({ err: error }, 'WebSocket error');
      this.clients.delete(ws);
    });
  }

  private async handleCommand(command: unknown): Promise<SendCommand> {
    const cmd = validateSendCommand(command);
    if (!this.wa) {
      throw new Error('WhatsApp client is not initialized');
    }
    await this.wa.sendMessage(cmd.to, cmd.text);
    return cmd;
  }

  private broadcast(msg: BridgeMessage): void {
    const data = JSON.stringify(msg);
    for (const client of this.clients) {
      this.sendSerializedToClient(client, data);
    }
  }

  private sendToClient(client: WebSocket, msg: BridgeMessage): void {
    this.sendSerializedToClient(client, JSON.stringify(msg));
  }

  private sendSerializedToClient(client: WebSocket, data: string): void {
    if (client.readyState !== WebSocket.OPEN) {
      return;
    }
    try {
      client.send(data);
    } catch (error) {
      logger.error({ err: error }, 'WebSocket send failed');
      this.clients.delete(client);
    }
  }

  async stop(): Promise<void> {
    const clients = [...this.clients];

    // Close all client connections
    for (const client of clients) {
      client.close();
    }
    this.clients.clear();

    // Close WebSocket server
    if (this.wss) {
      const server = this.wss;
      await new Promise<void>((resolve, reject) => {
        let settled = false;
        const timeout = setTimeout(() => {
          if (settled) return;
          settled = true;
          logger.warn('Bridge server close timed out; terminating clients');
          for (const client of clients) {
            client.terminate();
          }
          resolve();
        }, 500);

        server.close((error) => {
          if (settled) return;
          settled = true;
          clearTimeout(timeout);
          if (error) reject(error);
          else resolve();
        });
      });
      this.wss = null;
    }

    // Disconnect WhatsApp
    if (this.wa) {
      await this.wa.disconnect();
      this.wa = null;
    }
  }
}
