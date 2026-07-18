/** Structured logger for the WhatsApp bridge.
 *
 * Wraps pino with a sensible default config so server.ts and whatsapp.ts
 * share one log destination and level. Override the level via the
 * ``BRIDGE_LOG_LEVEL`` env var (default: ``info``).
 */
import pino from 'pino';

const logger = pino({
  level: process.env.BRIDGE_LOG_LEVEL || 'info',
  base: { component: 'bridge' },
});

export default logger;
