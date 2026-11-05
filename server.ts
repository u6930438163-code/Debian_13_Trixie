/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import express, { Request, Response } from 'express';
import http from 'http';
import path from 'path';
import fs from 'fs';
import crypto from 'crypto';
import { spawn, ChildProcess } from 'child_process';
import { fileURLToPath } from 'url';
import { GoogleGenAI } from '@google/genai';
import dotenv from 'dotenv';
import { hasBearerCredential, isInternalInferenceAuthorized } from './debian13/security/operator-auth.mjs';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORT = parseInt(process.env.PORT || '3000', 10);
const PYTHON_PORT = parseInt(process.env.DEBIAN13_API_PORT || '8765', 10);

// Set up directories and token
const homeDir = process.env.HOME || '/root';
const stateDir = process.env.DEBIAN13_STATE_DIR || path.join(homeDir, '.local/state/debian13');
const workspaceDir = process.env.WORKSPACE_DIR || path.join(homeDir, '.local/share/debian13/workspace');

fs.mkdirSync(stateDir, { recursive: true, mode: 0o700 });
fs.mkdirSync(workspaceDir, { recursive: true, mode: 0o700 });

const tokenFile = path.join(stateDir, 'operator-token');
const explicitOperatorToken = Boolean(
  process.env.DEBIAN13_API_TOKEN && process.env.DEBIAN13_API_TOKEN.length >= 32
);
let apiToken = process.env.DEBIAN13_API_TOKEN || '';
if (!apiToken) {
  if (fs.existsSync(tokenFile)) {
    apiToken = fs.readFileSync(tokenFile, 'utf-8').trim();
  }
  if (!apiToken || apiToken.length < 32) {
    apiToken = crypto.randomBytes(36).toString('base64url');
    fs.writeFileSync(tokenFile, apiToken + '\n', { mode: 0o600 });
  }
}

// Distinct ephemeral credential used only for backend-to-model inference over loopback.
const modelBridgeToken = crypto.randomBytes(36).toString('base64url');

console.log('[Debian 13] Initialized state and workspace directories.');
console.log(`[Debian 13] Operator token active (${apiToken.length} chars).`);

// Initialize Gemini SDK if API key available
const geminiApiKey = process.env.GEMINI_API_KEY || '';
const ai = geminiApiKey ? new GoogleGenAI({ apiKey: geminiApiKey }) : null;

let ollamaProcess: ChildProcess | null = null;

function ensureOllamaService() {
  if (fs.existsSync('/usr/local/bin/ollama')) {
    const req = http.get('http://127.0.0.1:11434/api/version', (res) => {
      if (res.statusCode === 200) {
        console.log('[Debian 13] Ollama service responding on 127.0.0.1:11434.');
      }
    });
    req.on('error', () => {
      console.log('[Debian 13] Starting background Ollama daemon...');
      try {
        ollamaProcess = spawn('/usr/local/bin/ollama', ['serve'], {
          stdio: 'ignore',
          detached: true,
        });
        ollamaProcess.unref();
      } catch (err) {
        console.warn('[Debian 13] Could not spawn ollama daemon:', err);
      }
    });
  }
}

ensureOllamaService();

// Start the Python backend process
let pythonProcess: ChildProcess | null = null;

function startPythonBackend() {
  const pythonScript = path.join(__dirname, 'debian13/system/server.py');
  if (!fs.existsSync(pythonScript)) {
    console.error(`[Debian 13] Python server script not found at ${pythonScript}`);
    return;
  }

  const env: NodeJS.ProcessEnv = {
    ...process.env,
    HOST: '127.0.0.1',
    PORT: String(PYTHON_PORT),
    DEBIAN13_API_PORT: String(PYTHON_PORT),
    DEBIAN13_API_TOKEN: apiToken,
    WORKSPACE_DIR: workspaceDir,
    DEBIAN13_STATE_DIR: stateDir,
  };

  // Wire loopback chat completion (Ollama llama3.2-vision:11b or Gemini) unless MODEL_CHAT_URL is explicitly set
  if (process.env.MODEL_CHAT_URL === undefined && (geminiApiKey || fs.existsSync('/usr/local/bin/ollama'))) {
    env.MODEL_CHAT_URL = `http://127.0.0.1:${PORT}/v1/chat/completions`;
    env.MODEL_API_KEY = modelBridgeToken;
    env.MODEL_NAME = process.env.MODEL_NAME || 'llama3.2-vision:11b';
    env.VISION_MODEL_NAME = process.env.VISION_MODEL_NAME || 'llama3.2-vision:11b';
  }

  console.log(`[Debian 13] Spawning Python backend on 127.0.0.1:${PYTHON_PORT}...`);
  pythonProcess = spawn('python3', ['-u', pythonScript], {
    env,
    stdio: ['ignore', 'inherit', 'inherit'],
  });

  pythonProcess.on('error', (err) => {
    console.error('[Debian 13] Failed to start Python backend:', err);
  });

  pythonProcess.on('exit', (code, signal) => {
    console.warn(`[Debian 13] Python backend exited (code=${code}, signal=${signal})`);
  });
}

startPythonBackend();

function cleanup() {
  if (pythonProcess && !pythonProcess.killed) {
    console.log('[Debian 13] Terminating Python backend...');
    pythonProcess.kill('SIGTERM');
  }
  if (ollamaProcess && !ollamaProcess.killed) {
    console.log('[Debian 13] Terminating Ollama daemon...');
    ollamaProcess.kill('SIGTERM');
  }
}
process.on('SIGINT', () => { cleanup(); process.exit(0); });
process.on('SIGTERM', () => { cleanup(); process.exit(0); });
process.on('exit', () => cleanup());

const app = express();

// Proxy /api/v1/* and /healthz to Python backend BEFORE parsing body
function proxyToPython(req: Request, res: Response) {
  const urlPath = req.originalUrl.split('?')[0];
  const queryString = req.originalUrl.includes('?') ? req.originalUrl.substring(req.originalUrl.indexOf('?')) : '';
  const mappedUrl = (urlPath === '/api/v1/integrations'
    ? '/v1/integrations'
    : (urlPath === '/api/v1/vision' ? '/v1/vision' : urlPath)) + queryString;

  const proxyReq = http.request(
    {
      hostname: '127.0.0.1',
      port: PYTHON_PORT,
      path: mappedUrl,
      method: req.method,
      headers: {
        ...req.headers,
        host: `127.0.0.1:${PYTHON_PORT}`,
        authorization: `Bearer ${apiToken}`,
      },
      timeout: 120000,
    },
    (proxyRes) => {
      // Set security & proxy headers
      res.writeHead(proxyRes.statusCode || 500, proxyRes.headers);
      proxyRes.pipe(res);
    }
  );

  proxyReq.on('error', (err) => {
    if (!res.headersSent) {
      res.status(503).json({ error: 'Runtime nieosiągalny — backend w trakcie uruchamiania.', details: err.message });
    }
  });

  req.pipe(proxyReq);
}

app.use('/healthz', (req, res) => proxyToPython(req, res));
// When DEBIAN13_API_TOKEN is explicitly configured in the environment, require the
// caller to present that Bearer token. In local AI Studio preview mode (where an
// ephemeral loopback token is generated automatically), act as the local UI proxy.
app.use('/api/v1', (req, res, next) => {
  if (explicitOperatorToken && !hasBearerCredential(req.header('authorization'), apiToken)) {
    res.status(401).json({ error: { code: 'unauthorized', message: 'Operator Bearer token required.' } });
    return;
  }
  next();
}, proxyToPython);

// GPT Actions and remote MCP use authenticated API routes.
// These routes require explicit operator Bearer token credential.
function proxyAuthenticated(req: Request, res: Response) {
  if (!hasBearerCredential(req.header('authorization'), apiToken)) {
    res.status(401).json({ error: { code: 'unauthorized', message: 'Bearer token required.' } });
    return;
  }
  proxyToPython(req, res);
}

for (const route of ['/v1/profiles', '/v1/integrations', '/v1/diagnostics']) {
  app.get(route, proxyAuthenticated);
}
app.post('/v1/chat', proxyAuthenticated);
app.all('/mcp', proxyAuthenticated);

function describeDataUriImage(dataUri: string): string {
  const commaIdx = dataUri.indexOf(',');
  if (commaIdx === -1) return 'Obraz Vision';
  const header = dataUri.substring(0, commaIdx);
  const mimeMatch = header.match(/data:(.*?);base64/);
  const mime = mimeMatch ? mimeMatch[1] : 'image/unknown';
  const buf = Buffer.from(dataUri.substring(commaIdx + 1), 'base64');
  let dims = '';
  if (mime === 'image/png' && buf.length >= 24 && buf.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) {
    const width = buf.readUInt32BE(16);
    const height = buf.readUInt32BE(20);
    dims = `, wymiary: ${width}x${height}px`;
  }
  return `[Załączony obraz Vision (${mime}, ${buf.length} bajtów${dims})]`;
}

async function callLocalOllama(messages: Array<any>, model: string): Promise<{ content: string; model: string } | null> {
  const ollamaModel = model && !model.startsWith('gemini') ? model : 'llama3.2-vision:11b';
  const normalizedMessages = messages.map((msg) => {
    if (msg.role === 'system') {
      return {
        role: 'system',
        content: `${msg.content}\nOdpowiadaj zwięźle, rzeczowo i naturalnie w języku użytkownika (domyślnie po polsku) w ramach konsoli Debian 13.`,
      };
    }
    return msg;
  });

  const postToOllama = async (msgs: Array<any>) => {
    const response = await fetch('http://127.0.0.1:11434/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: ollamaModel,
        messages: msgs,
        temperature: 0.2,
      }),
      signal: AbortSignal.timeout(90000),
    });
    const payload: any = await response.json().catch(() => null);
    return { ok: response.ok, status: response.status, payload };
  };

  try {
    let result = await postToOllama(normalizedMessages);
    if (!result.ok && JSON.stringify(result.payload || '').includes('does not support multimodal')) {
      const textFallbackMessages = normalizedMessages.map((msg) => {
        if (msg.role === 'user' && Array.isArray(msg.content)) {
          const parts: string[] = [];
          for (const item of msg.content) {
            if (item.type === 'text' && item.text) parts.push(item.text);
            if (item.type === 'image_url') {
              const url = typeof item.image_url === 'string' ? item.image_url : item.image_url?.url;
              if (typeof url === 'string') parts.push(describeDataUriImage(url));
            }
          }
          return { role: 'user', content: parts.join('\n') };
        }
        return msg;
      });
      result = await postToOllama(textFallbackMessages);
    }
    const content = result.payload?.choices?.[0]?.message?.content;
    if (result.ok && typeof content === 'string' && content.trim()) {
      return { content: content.trim(), model: result.payload?.model || ollamaModel };
    }
  } catch {
    // Local Ollama unavailable; fall through to Gemini if configured.
  }
  return null;
}

// OpenAI-compatible Chat Completions endpoint for Python server -> Ollama / Gemini bridge
app.post('/v1/chat/completions', (req, res, next) => {
  // This bridge exists for Python -> Node only, not as a public paid inference API.
  if (!isInternalInferenceAuthorized(req.header('authorization'), modelBridgeToken, req.socket.remoteAddress)) {
    res.status(401).json({ error: { code: 'unauthorized', message: 'Internal inference endpoint.' } });
    return;
  }
  next();
}, express.json({ limit: '10mb' }), async (req: Request, res: Response): Promise<void> => {
  try {
    const { messages, model: requestedModel } = req.body;
    if (!Array.isArray(messages) || messages.length === 0) {
      res.status(400).json({ error: { message: 'Missing messages array' } });
      return;
    }

    // Prefer local Ollama when requestedModel is an Ollama model (e.g. llama3.2-vision:11b) or when Gemini is not configured
    if (!ai || (typeof requestedModel === 'string' && !requestedModel.startsWith('gemini'))) {
      const ollamaReply = await callLocalOllama(messages, requestedModel || 'llama3.2-vision:11b');
      if (ollamaReply) {
        res.json({
          id: 'chatcmpl-' + crypto.randomUUID(),
          object: 'chat.completion',
          created: Math.floor(Date.now() / 1000),
          model: ollamaReply.model,
          choices: [
            {
              index: 0,
              message: {
                role: 'assistant',
                content: ollamaReply.content,
              },
              finish_reason: 'stop',
            },
          ],
        });
        return;
      }
    }

    if (!ai) {
      res.status(503).json({
        error: {
          message: 'No AI model provider is configured or reachable. Ensure Ollama or GEMINI_API_KEY is active.',
          type: 'provider_not_configured',
        },
      });
      return;
    }

    // Extract system instructions and conversation contents
    let systemInstruction = '';
    const contents: Array<any> = [];

    for (const msg of messages) {
      if (msg.role === 'system') {
        systemInstruction += (systemInstruction ? '\n\n' : '') + String(msg.content || '');
      } else if (msg.role === 'user') {
        if (typeof msg.content === 'string') {
          contents.push({ role: 'user', parts: [{ text: msg.content }] });
        } else if (Array.isArray(msg.content)) {
          const parts: Array<any> = [];
          for (const part of msg.content) {
            if (part.type === 'text') {
              parts.push({ text: part.text });
            } else if (part.type === 'image_url') {
              const url = typeof part.image_url === 'string' ? part.image_url : part.image_url?.url;
              if (typeof url === 'string' && url.startsWith('data:')) {
                const commaIdx = url.indexOf(',');
                const mimeMatch = url.substring(0, commaIdx).match(/data:(.*?);base64/);
                const mimeType = mimeMatch ? mimeMatch[1] : 'image/png';
                const base64Data = url.substring(commaIdx + 1);
                parts.push({ inlineData: { mimeType, data: base64Data } });
              }
            }
          }
          contents.push({ role: 'user', parts });
        }
      }
    }

    const modelName = requestedModel && requestedModel.startsWith('gemini') ? requestedModel : 'gemini-3.1-flash-lite';
    const response = await ai.models.generateContent({
      model: modelName,
      contents,
      config: systemInstruction ? { systemInstruction } : undefined,
    });

    const replyText = response.text || '';

    res.json({
      id: 'chatcmpl-' + crypto.randomUUID(),
      object: 'chat.completion',
      created: Math.floor(Date.now() / 1000),
      model: modelName,
      choices: [
        {
          index: 0,
          message: {
            role: 'assistant',
            content: replyText,
          },
          finish_reason: 'stop',
        },
      ],
    });
  } catch (err: any) {
    console.error('[Debian 13] Model chat completion error:', err);
    res.status(500).json({
      error: {
        message: err.message || 'Error communicating with model provider',
        type: 'api_error',
      },
    });
  }
});

// Serve static frontend UI from debian13/ui directory
const uiDir = path.join(__dirname, 'debian13/ui');
app.use(express.static(uiDir, {
  setHeaders(res, filePath) {
    // Prevent old HTML/CSS/JS from masking a new design after GitHub sync.
    if (/\.(?:html|css|js|mjs)$/.test(filePath)) {
      res.setHeader('Cache-Control', 'no-store');
    }
  },
}));

// Fallback to index.html for all other GET routes
app.get('*', (_req, res) => {
  res.sendFile(path.join(uiDir, 'index.html'));
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`[Debian 13] Console server running on http://0.0.0.0:${PORT}`);
  console.log(`[Debian 13] Serving static UI from ${uiDir}`);
});
