# Original AI Studio agents: GitHub Issue handoff

**Scope:** `u6930438163-code/Debian_13_Trixie`. This integration calls the **existing** model pipeline for Alex, Aria, Astyria and Aurelius in `u6930438163-code/AI_Studio_BastillaOS`. It is not GitHub Copilot, not a fake bot, and does not automatically make code edits.

## One-time private setup

1. Deploy the original AI Studio backend including its merged PR #18. On that deployment configure a working LLM provider, persistent task storage, `DEBIAN13_ORIGINAL_WORKERS_ENABLED=1`, and `DEBIAN13_ORIGINAL_AGENTS_TOKEN` (at least 32 random characters).
2. In this GitHub repository open **Settings → Secrets and variables → Actions** and set **two repository secrets**: `ORIGINAL_AGENT_API_URL` (HTTPS source origin, optionally ending in `/api`) and `ORIGINAL_AGENT_API_TOKEN` (same value as the source `DEBIAN13_ORIGINAL_AGENTS_TOKEN`). Never paste tokens in GitHub Issues, PRs or code.
3. Merge the reviewed integration PR, then write an owner comment such as `/agent Alex`, `/agent Aria`, `/agent Astyria` or `/agent Aurelius` on an **open Issue**. Or choose **Actions → Original AI Studio agents - GitHub Issues review → Run workflow**, provide issue number and agent.

The workflow checks source identity and `debian13_review_task_v1`, submits the actual GitHub Issue description with an idempotency key, polls the returned job ID, and comments with evidence from the returned provider/model or an explicit blocker. Each run records its result in GitHub Actions. It does not treat `queued` as `running`, and it does not treat `completed` as a code commit.

## Critical limitation and next task

Source service currently advertises `model_review_only`: its result is a **real AI model review**, not a code change. A full Issue → commit → tests → PR pipeline still requires a **separately audited GitHub write-worker** authorized to edit a limited set of files and publish reviewable PRs. No automatic merges, purchases, privilege grants or deployment. No substitution of the original four agents by Copilot.

If the source has not been deployed or the provider/secrets are absent, GitHub Actions will fail visibly and report that the original AI task has **not** been verified. A GitHub agent profile file is not proof of a live worker.
