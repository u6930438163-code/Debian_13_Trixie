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

  // If Gemini API is available and custom model url is not set, wire loopback chat completion
  if (geminiApiKey && !process.env.MODEL_CHAT_URL) {
    env.MODEL_CHAT_URL = `http://127.0.0.1:${PORT}/v1/chat/completions`;
    env.MODEL_API_KEY = modelBridgeToken;
    env.MODEL_NAME = 'gemini-3.1-flash-lite';
    env.VISION_MODEL_NAME = 'gemini-3.1-flash-lite';
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
// The hosted UI must authenticate before receiving the backend's privileged token.
app.use('/api/v1', proxyAuthenticated);

// GPT Actions and remote MCP use authenticated API routes.
// These routes must NOT inherit the UI's implicit internal token.
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


// OpenAI-compatible Chat Completions endpoint for Python server -> Gemini bridge
app.post('/v1/chat/completions', (req, res, next) => {
  // This bridge exists for Python -> Node only, not as a public paid inference API.
  if (!isInternalInferenceAuthorized(req.header('authorization'), modelBridgeToken, req.socket.remoteAddress)) {
    res.status(401).json({ error: { code: 'unauthorized', message: 'Internal inference endpoint.' } });
    return;
  }
  next();
}, express.json({ limit: '10mb' }), async (req: Request, res: Response): Promise<void> => {
  if (!ai) {
    res.status(503).json({
      error: {
        message: 'No AI model provider is configured. Set GEMINI_API_KEY to enable inference.',
        type: 'provider_not_configured',
      },
    });
    return;
  }

  try {
    const { messages, model: requestedModel } = req.body;
    if (!Array.isArray(messages) || messages.length === 0) {
      res.status(400).json({ error: { message: 'Missing messages array' } });
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
    console.error('[Debian 13] Gemini chat completion error:', err);
    res.status(500).json({
      error: {
        message: err.message || 'Error communicating with Gemini model',
        type: 'api_error',
      },
    });
  }
});

// Serve static frontend UI from debian13/ui directory
const uiDir = path.join(__dirname, 'debian13/ui');
app.use(express.static(uiDir));

// Fallback to index.html for all other GET routes
app.get('*', (_req, res) => {
  res.sendFile(path.join(uiDir, 'index.html'));
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`[Debian 13] Console server running on http://0.0.0.0:${PORT}`);
  console.log(`[Debian 13] Serving static UI from ${uiDir}`);
});
