# Ollama for the standalone Debian 13 prototype

Ollama is optional. The default Compose stack remains unchanged.
The additional `compose.ollama.yaml` runs Ollama as a private service on the
Docker Compose network and connects the existing Debian 13 API to its
OpenAI-compatible `/v1/chat/completions` endpoint. It does not grant root to
agents or expose Ollama on a public host port.

## Start

From the repository root:

```sh
bash debian13/start-ai-session.sh
```

The default is `qwen2.5-coder:1.5b`, a text-only model downloaded on first
start. Docker, outbound access, sufficient disk and memory are required.
CPU-only inference may be slow. `OLLAMA_MODEL` selects another model; the
20 GB container memory limit does not increase the host's available memory.
The selected model serves the local chat profiles; it does not establish
that independent original-agent workers are running.

## Vision

Vision is not configured by default. To enable it later, use a compatible
downloaded model and set `OLLAMA_VISION_MODEL`; test image inference on the
actual host. Pulling a model alone does not verify the complete UI flow.

## Security and availability

- Ollama is reachable only inside the Compose network as `ollama:11434`.
- The internal HTTP URL is accepted only with
  `DEBIAN13_ALLOW_INTERNAL_OLLAMA=1` and the exact `ollama:11434` hostname.
- The dummy `MODEL_API_KEY` is only for local Ollama compatibility; the
  Debian 13 API still requires a separate secret `DEBIAN13_API_TOKEN`.
- This setup does not create autonomous background agents or a bootable OS.
- GitHub-hosted Actions runners are temporary and generally unsuitable for
  persistent local 14B inference; deploy on a persistent host for 24/7 use.
- Review and pin the Ollama container image to a tested digest before
  production deployment.

## Llama 3.2 Vision 11B

The official Ollama tag is `llama3.2-vision:11b` (approximately 7.8 GB model download).
It accepts image and text input, and returns text. Ollama recommends at least
8 GB VRAM for GPU inference; check actual host resources before downloading.

To use it for text and image requests, start the session with:

```sh
export OLLAMA_MODEL=llama3.2-vision:11b
export OLLAMA_VISION_MODEL=llama3.2-vision:11b
bash debian13/start-ai-session.sh
```

Image inference still needs to be verified on the actual host.

