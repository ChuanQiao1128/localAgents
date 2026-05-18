#!/usr/bin/env python3
"""RC-5C automation: add and verify a Codex CLI rewrite provider.

This script is intentionally narrow:
- one product only: AI Writing Naturalizer
- one real rewrite provider only: Codex CLI
- default model: gpt-5.5
- no OpenAI API key
- no detector API integration
- no deploy / git push / package dependency changes

It enforces an 8-hour wall-clock budget by default. If the workflow
finishes earlier, it exits successfully instead of starting an
unapproved next phase.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


CHANGE_REQUEST = """# Add real Codex CLI rewrite provider
## Goal
Add a real Codex CLI-backed rewrite provider to AI Writing Naturalizer while keeping the existing deterministic rewrite as a mock fallback.
The Naturalizer should call Codex CLI server-side when available, use `gpt-5.5` by default, and clearly show whether the rewrite came from real mode or mock mode.
Scope: app/**, components/**, lib/**
## Non-goals
- Do not add authentication.
- Do not add Stripe or billing.
- Do not add a database.
- Do not add document upload.
- Do not add RAG.
- Do not add detector API integration in this change.
- Do not add multiple LLM providers.
- Do not add OpenAI API key support in this change.
- Do not require `OPENAI_API_KEY`.
- Do not add npm dependencies.
- Do not modify package.json or package-lock.json.
- Do not implement repeated rewrite loops.
- Do not claim detector bypass.
- Do not persist secrets.
- Do not expose local environment details in logs, UI, browser bundle, or artifacts.
## Acceptance
- A server-side rewrite API exists, for example `POST /api/rewrite`.
- The API accepts original text and tone.
- The API uses Codex CLI only from server-side code.
- The API reads `CODEX_MODEL` when present and defaults to `gpt-5.5`.
- The API reads `CODEX_BIN` when present and defaults to `codex`.
- The API invokes Codex CLI with a bounded timeout and a bounded prompt.
- The API must use Codex CLI flags supported by `codex exec` v0.128.0: `-m`, `--sandbox read-only`, `--skip-git-repo-check`, `--ephemeral`, `--color never`, and `--output-last-message`.
- The API must not use unsupported approval flags such as `--ask-for-approval`.
- The API reads `CODEX_REWRITE_TIMEOUT_MS` when present and defaults to at least 120000ms.
- The Codex CLI prompt asks for rewritten text only, not commentary.
- The API does not run Codex from client-side/browser code.
- If Codex CLI is unavailable, times out, or fails, the existing deterministic rewrite fallback is used.
- The response includes:
  - `mode`: `real` or `mock`
  - `provider`
  - `model`
  - `rewrittenText`
  - `changeSummary`
  - `warnings`
- The frontend displays a clear mode badge: `real` or `mock`.
- The frontend does not expose shell commands, local paths, stack traces, or environment values.
- The existing Naturalize button still works.
- The existing detector-style score section still works.
- The existing history still works.
- npm run build passes.
- npm run typecheck passes.
"""


SAMPLE_TEXT = (
    "In today's fast-paced digital world, it is important to note that teams "
    "must leverage cutting-edge workflows in order to create a seamless and "
    "robust writing process."
)


class StepError(RuntimeError):
    """A controlled workflow failure."""


class Runner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.root = Path(args.root).expanduser().resolve()
        self.agent_studio = self.root / "agent-studio"
        self.studio_project_id = args.studio_project
        self.studio_url = args.studio_url.rstrip("/")
        self.codex_model = args.model or os.environ.get("CODEX_MODEL") or "gpt-5.5"
        self.codex_bin = args.codex_bin or os.environ.get("CODEX_BIN") or "codex"
        self.started = time.monotonic()
        self.deadline = self.started + args.duration_hours * 3600
        self.timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        self.run_dir = (
            self.root
            / ".studio-console"
            / "projects"
            / self.studio_project_id
            / "runs"
            / f"rc5c_codex_rewrite_{self.timestamp}"
        )
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.run_dir / "runner.log"
        self.state: dict[str, Any] = {
            "startedAt": now_iso(),
            "studioProjectId": self.studio_project_id,
            "codexModel": self.codex_model,
            "codexBin": self.codex_bin,
            "runDir": str(self.run_dir),
            "steps": [],
        }
        self.runtime_project_id = ""
        self.runtime_path = Path()
        self.change_request_path = self.run_dir / "change-request-codex-rewrite.md"
        self.report_path = self.root / args.report

    def log(self, message: str) -> None:
        line = f"[{dt.datetime.now().strftime('%H:%M:%S')}] {message}"
        print(line, flush=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def remaining(self) -> int:
        remaining = int(self.deadline - time.monotonic())
        if remaining <= 0:
            raise StepError("8-hour budget exhausted")
        return remaining

    def save_state(self) -> None:
        self.state["updatedAt"] = now_iso()
        (self.run_dir / "runner-state.json").write_text(
            json.dumps(redact_obj(self.state), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def record_step(
        self,
        name: str,
        status: str,
        *,
        detail: str | None = None,
        artifact: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        step: dict[str, Any] = {
            "name": name,
            "status": status,
            "time": now_iso(),
        }
        if detail:
            step["detail"] = detail
        if artifact:
            step["artifact"] = artifact
        if data:
            step["data"] = redact_obj(data)
        self.state["steps"].append(step)
        self.save_state()

    def run(self) -> int:
        try:
            self.log("RC-5C Codex rewrite automation started.")
            self.preflight()
            self.codex_smoke_test()
            self.write_runtime_env()
            self.create_change_request()
            self.run_change_request()
            self.validate_runtime()
            preview_url = self.restart_preview()
            self.verify_rewrite_api(preview_url, expect_mode="real")
            self.verify_mock_fallback(preview_url)
            self.write_report(preview_url)
            self.state["finishedAt"] = now_iso()
            self.state["status"] = "completed"
            self.save_state()
            self.log("RC-5C automation completed.")
            self.log(f"Report: {self.report_path}")
            return 0
        except StepError as exc:
            self.state["finishedAt"] = now_iso()
            self.state["status"] = "failed"
            self.state["error"] = str(exc)
            self.save_state()
            self.log(f"FAILED: {exc}")
            self.log(f"Run artifacts: {self.run_dir}")
            return 1

    def preflight(self) -> None:
        self.log("Preflight: checking project mapping, Codex CLI, git state, and build.")
        if not self.agent_studio.exists():
            raise StepError(f"agent-studio not found at {self.agent_studio}")

        self.run_cmd([self.codex_bin, "--version"], cwd=self.root, capture_name="codex-version.txt")

        project_json = (
            self.root / ".studio-console" / "projects" / self.studio_project_id / "project.json"
        )
        if not project_json.exists():
            raise StepError(f"Studio project JSON not found: {project_json}")
        project = json.loads(project_json.read_text(encoding="utf-8"))
        self.runtime_project_id = project.get("agentProjectId") or ""
        runtime_path_raw = project.get("agentProjectPath") or ""
        if not self.runtime_project_id or not runtime_path_raw:
            raise StepError("Runtime project is not linked yet.")
        self.runtime_path = (
            Path(runtime_path_raw)
            if Path(runtime_path_raw).is_absolute()
            else self.root / runtime_path_raw
        ).resolve()
        if not self.runtime_path.exists():
            raise StepError(f"Runtime project path does not exist: {self.runtime_path}")

        gitignore = self.runtime_path / ".gitignore"
        if not gitignore.exists():
            raise StepError("Runtime .gitignore missing; refusing to write .env.local")
        if not env_local_is_ignored(gitignore.read_text(encoding="utf-8")):
            raise StepError(".env.local is not ignored in runtime project")

        self.run_cmd(["git", "status", "--short"], cwd=self.runtime_path, capture_name="git-status-pre.txt")
        status = (self.run_dir / "git-status-pre.txt").read_text(encoding="utf-8").strip()
        if status:
            raise StepError(f"Runtime worktree is dirty before change:\n{status}")

        self.run_cmd(["npm", "run", "build"], cwd=self.runtime_path, capture_name="runtime-build-pre.log")
        self.record_step(
            "preflight",
            "passed",
            data={
                "runtimeProjectId": self.runtime_project_id,
                "runtimePath": str(self.runtime_path),
                "codexBin": self.codex_bin,
                "codexModel": self.codex_model,
            },
        )

    def codex_smoke_test(self) -> None:
        self.log("Codex smoke test: calling Codex CLI once before changing code.")
        output_path = self.run_dir / "codex-smoke-last-message.txt"
        prompt = (
            "Rewrite the following text to sound natural, clear, and human-written. "
            "Return only the rewritten text, with no explanation.\n\n"
            f"Text:\n{SAMPLE_TEXT}"
        )
        self.run_cmd(
            [
                self.codex_bin,
                "exec",
                "-C",
                str(self.runtime_path),
                "-m",
                self.codex_model,
                "--sandbox",
                "read-only",
                "--output-last-message",
                str(output_path),
                "--",
                prompt,
            ],
            cwd=self.root,
            capture_name="codex-smoke.log",
            timeout=min(900, self.remaining()),
        )
        output = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else ""
        if not output:
            raise StepError("Codex smoke test produced no final message")
        self.record_step(
            "codex-smoke-test",
            "passed",
            artifact=str(output_path),
            data={"outputPreview": output[:240]},
        )

    def write_runtime_env(self) -> None:
        self.log("Writing ignored runtime .env.local for Codex CLI provider config.")
        env_path = self.runtime_path / ".env.local"
        original = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        updated = upsert_env(
            original,
            {
                "CODEX_BIN": self.codex_bin,
                "CODEX_MODEL": self.codex_model,
                "CODEX_REWRITE_TIMEOUT_MS": str(self.args.codex_timeout_ms),
            },
        )
        env_path.write_text(updated, encoding="utf-8")
        self.run_cmd(["git", "status", "--short"], cwd=self.runtime_path, capture_name="git-status-after-env.txt")
        status = (self.run_dir / "git-status-after-env.txt").read_text(encoding="utf-8").strip()
        if ".env" in status:
            raise StepError(".env.local appeared in git status; refusing to continue")
        self.record_step("runtime-env", "written", detail=".env.local updated and ignored")

    def create_change_request(self) -> None:
        self.log("Creating Codex rewrite Change Request artifact.")
        self.change_request_path.write_text(CHANGE_REQUEST, encoding="utf-8")
        self.run_cmd(
            [
                str(self.agent_studio),
                "--root",
                str(self.root),
                "change",
                "new",
                "--from",
                str(self.change_request_path),
                "--project",
                self.runtime_project_id,
                "--json",
            ],
            cwd=self.root,
            capture_name="change-new.json",
        )
        change_new = read_json_file(self.run_dir / "change-new.json")
        self.record_step(
            "change-new",
            "completed",
            artifact=str(self.change_request_path),
            data=summarize_change_json(change_new),
        )
        self.run_cmd(
            [
                str(self.agent_studio),
                "--root",
                str(self.root),
                "change",
                "validate",
                "latest",
                "--project",
                self.runtime_project_id,
                "--json",
            ],
            cwd=self.root,
            capture_name="change-validate-before.json",
        )
        validate_before = read_json_file(self.run_dir / "change-validate-before.json")
        if validate_before and validate_before.get("ok") is False:
            raise StepError("change validate before run failed")
        self.record_step("change-validate-before", "passed", data=validate_before or {})

    def run_change_request(self) -> None:
        self.log("Running Change Request through agent-studio. This may take a while.")
        self.run_cmd(
            [
                str(self.agent_studio),
                "--root",
                str(self.root),
                "change",
                "run",
                "latest",
                "--project",
                self.runtime_project_id,
                "--json",
            ],
            cwd=self.root,
            capture_name="change-run.json",
            stream=True,
            timeout=self.remaining(),
        )
        run_result = read_json_file(self.run_dir / "change-run.json")
        self.record_step("change-run", "completed", data=summarize_change_json(run_result))

        self.run_cmd(
            [
                str(self.agent_studio),
                "--root",
                str(self.root),
                "change",
                "status",
                "latest",
                "--project",
                self.runtime_project_id,
                "--json",
            ],
            cwd=self.root,
            capture_name="change-status-after.json",
        )
        status = read_json_file(self.run_dir / "change-status-after.json")
        self.record_step("change-status-after", "captured", data=summarize_change_json(status))

        self.run_cmd(
            [
                str(self.agent_studio),
                "--root",
                str(self.root),
                "autonomous",
                "reviews",
                "list",
                "--project",
                self.runtime_project_id,
                "--json",
            ],
            cwd=self.root,
            capture_name="reviews-open.json",
        )
        reviews = read_json_file(self.run_dir / "reviews-open.json")
        if reviews_has_open_items(reviews):
            raise StepError("Change run left open review items; inspect reviews-open.json")

        self.run_cmd(["git", "status", "--short"], cwd=self.runtime_path, capture_name="git-status-after-change.txt")
        dirty = (self.run_dir / "git-status-after-change.txt").read_text(encoding="utf-8").strip()
        if dirty:
            raise StepError(f"Runtime worktree dirty after change:\n{dirty}")

    def validate_runtime(self) -> None:
        self.log("Validating runtime typecheck, build, and change artifacts.")
        self.run_cmd(["npm", "run", "typecheck"], cwd=self.runtime_path, capture_name="runtime-typecheck-after.log")
        self.run_cmd(["npm", "run", "build"], cwd=self.runtime_path, capture_name="runtime-build-after.log")
        self.run_cmd(
            [
                str(self.agent_studio),
                "--root",
                str(self.root),
                "change",
                "validate",
                "latest",
                "--project",
                self.runtime_project_id,
                "--json",
            ],
            cwd=self.root,
            capture_name="change-validate-after.json",
        )
        validation = read_json_file(self.run_dir / "change-validate-after.json")
        if validation and validation.get("ok") is False:
            raise StepError("change validate after run failed")
        self.record_step("runtime-validation", "passed", data=validation or {})

    def restart_preview(self) -> str:
        self.log("Restarting Studio preview.")
        if not self.args.preview:
            self.record_step("preview", "skipped")
            return self.args.preview_url or ""
        start_url = f"{self.studio_url}/api/studio-projects/{self.studio_project_id}/preview?restart=1"
        response = http_json(start_url, {}, method="POST", timeout=min(120, self.remaining()))
        (self.run_dir / "preview-start.json").write_text(
            json.dumps(redact_obj(response), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        status = response.get("status") if isinstance(response, dict) else None
        preview_url = status.get("url") if isinstance(status, dict) else None
        if not preview_url:
            raise StepError("Preview did not return a URL")
        self.wait_for_http_200(preview_url, timeout_sec=60)
        self.record_step("preview", "running", data={"url": preview_url})
        return str(preview_url)

    def wait_for_http_200(self, url: str, timeout_sec: int) -> None:
        deadline = time.monotonic() + min(timeout_sec, self.remaining())
        last_error = ""
        while time.monotonic() < deadline:
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status < 500:
                        return
                    last_error = f"HTTP {resp.status}"
            except Exception as exc:  # noqa: BLE001 - surfaced as StepError below
                last_error = str(exc)
            time.sleep(1)
        raise StepError(f"Preview did not become healthy: {last_error}")

    def verify_rewrite_api(self, preview_url: str, *, expect_mode: str) -> dict[str, Any]:
        if not preview_url:
            raise StepError("Cannot verify rewrite API without preview URL")
        self.log(f"Verifying /api/rewrite expects mode={expect_mode}.")
        payload = {
            "text": SAMPLE_TEXT,
            "originalText": SAMPLE_TEXT,
            "tone": "direct",
        }
        response = http_json(
            f"{preview_url.rstrip('/')}/api/rewrite",
            payload,
            timeout=min(max(120, int(self.args.codex_timeout_ms / 1000) + 60), self.remaining()),
        )
        artifact = self.run_dir / f"rewrite-api-{expect_mode}.json"
        artifact.write_text(
            json.dumps(redact_obj(response), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        mode = response.get("mode") if isinstance(response, dict) else None
        rewritten = extract_rewritten_text(response)
        if mode != expect_mode:
            raise StepError(f"/api/rewrite returned mode={mode!r}, expected {expect_mode!r}")
        if not rewritten:
            raise StepError("/api/rewrite returned no rewritten text")
        self.record_step(
            f"rewrite-api-{expect_mode}",
            "passed",
            artifact=str(artifact),
            data={
                "mode": mode,
                "provider": response.get("provider") if isinstance(response, dict) else None,
                "model": response.get("model") if isinstance(response, dict) else None,
                "rewrittenPreview": rewritten[:180],
            },
        )
        return response

    def verify_mock_fallback(self, preview_url: str) -> None:
        if not self.args.verify_fallback:
            self.record_step("mock-fallback", "skipped")
            return
        self.log("Verifying mock fallback by temporarily replacing CODEX_BIN.")
        env_path = self.runtime_path / ".env.local"
        original = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        try:
            env_path.write_text(
                upsert_env(
                    original,
                    {
                        "CODEX_BIN": "/__missing__/codex",
                        "CODEX_MODEL": self.codex_model,
                        "CODEX_REWRITE_TIMEOUT_MS": str(self.args.codex_timeout_ms),
                    },
                ),
                encoding="utf-8",
            )
            preview_url = self.restart_preview()
            self.verify_rewrite_api(preview_url, expect_mode="mock")
        finally:
            env_path.write_text(original, encoding="utf-8")
            with contextlib.suppress(Exception):
                self.restart_preview()

    def write_report(self, preview_url: str) -> None:
        self.log("Writing RC-5C report.")
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        change_status = read_json_file(self.run_dir / "change-status-after.json")
        real_response = read_json_file(self.run_dir / "rewrite-api-real.json")
        mock_response = read_json_file(self.run_dir / "rewrite-api-mock.json")
        git_log = self.run_cmd(
            ["git", "log", "--oneline", "--decorate", "--max-count=8"],
            cwd=self.runtime_path,
            capture_name="git-log-after.txt",
            return_text=True,
        )
        report = f"""# RC-5C · AI Writing Naturalizer — Codex CLI Rewrite Provider

**Date:** {dt.date.today().isoformat()}
**Studio project:** `{self.studio_project_id}`
**Runtime project id:** `{self.runtime_project_id}`
**Runtime path:** `{self.runtime_path.relative_to(self.root)}`
**Preview URL:** `{preview_url}`
**Codex model:** `{self.codex_model}`
**Run artifacts:** `{self.run_dir.relative_to(self.root)}`

---

## Result

The Naturalizer Codex CLI rewrite provider change was executed through
Studio Change Request mode.

- Codex CLI smoke test before code change: PASS
- Change Request run: completed
- Runtime typecheck: PASS
- Runtime build: PASS
- Change artifacts validation: PASS
- Real rewrite API smoke: PASS
- Mock fallback smoke: {"PASS" if mock_response else "SKIPPED"}

---

## Change evidence

```json
{json.dumps(redact_obj(summarize_change_json(change_status)), indent=2, ensure_ascii=False)}
```

Recent runtime commits:

```text
{git_log.strip()}
```

---

## Real mode verification

```json
{json.dumps(redact_obj(summarize_rewrite_response(real_response)), indent=2, ensure_ascii=False)}
```

---

## Mock fallback verification

```json
{json.dumps(redact_obj(summarize_rewrite_response(mock_response)), indent=2, ensure_ascii=False)}
```

---

## Security checks

- Codex CLI is invoked only from server-side code.
- No `OPENAI_API_KEY` is required.
- No npm dependency was added by this script.
- No detector integration was part of this change.
- Fallback mode is required when Codex CLI is unavailable or fails.
- UI should not expose shell commands, local paths, stack traces, or
  environment values.

---

## Next

If the Codex rewrite provider is accepted, the next scoped Change Request
should add one real detector provider with the local heuristic detector
kept as fallback.
"""
        self.report_path.write_text(report, encoding="utf-8")
        self.record_step("report", "written", artifact=str(self.report_path))

    def run_cmd(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        capture_name: str,
        stream: bool = False,
        timeout: int | None = None,
        return_text: bool = False,
    ) -> str:
        self.remaining()
        timeout = min(timeout or self.remaining(), self.remaining())
        artifact = self.run_dir / capture_name
        redacted_cmd = " ".join(redact_text(part) for part in cmd)
        self.log(f"$ {redacted_cmd}  (cwd={cwd})")
        if stream:
            with artifact.open("w", encoding="utf-8") as out:
                proc = subprocess.Popen(
                    cmd,
                    cwd=cwd,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=os.environ.copy(),
                )
                deadline = time.monotonic() + timeout
                assert proc.stdout is not None
                lines: list[str] = []
                while True:
                    if time.monotonic() > deadline:
                        proc.kill()
                        raise StepError(f"Command timed out: {redacted_cmd}")
                    line = proc.stdout.readline()
                    if line:
                        safe = redact_text(line)
                        print(safe, end="", flush=True)
                        out.write(safe)
                        lines.append(safe)
                    elif proc.poll() is not None:
                        break
                    else:
                        time.sleep(0.2)
                rc = proc.wait()
                if rc != 0:
                    raise StepError(f"Command failed rc={rc}: {redacted_cmd}")
                text = "".join(lines)
        else:
            try:
                completed = subprocess.run(
                    cmd,
                    cwd=cwd,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    check=False,
                    env=os.environ.copy(),
                )
            except subprocess.TimeoutExpired as exc:
                raise StepError(f"Command timed out: {redacted_cmd}") from exc
            text = redact_text(completed.stdout or "")
            artifact.write_text(text, encoding="utf-8")
            if completed.returncode != 0:
                raise StepError(
                    f"Command failed rc={completed.returncode}: {redacted_cmd}\n"
                    f"See {artifact}"
                )
        if return_text:
            return text
        return ""


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the RC-5C Codex CLI rewrite provider change with an 8-hour budget."
    )
    parser.add_argument("--root", default="/Users/qc/Documents/LocalAgents")
    parser.add_argument("--studio-project", default="ai-writing-naturalizer")
    parser.add_argument("--studio-url", default="http://127.0.0.1:3015")
    parser.add_argument("--model", default=os.environ.get("CODEX_MODEL", "gpt-5.5"))
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", "codex"))
    parser.add_argument("--codex-timeout-ms", type=int, default=120000)
    parser.add_argument("--duration-hours", type=float, default=8.0)
    parser.add_argument(
        "--report",
        default="docs/rc5c-naturalizer-codex-rewrite-report.md",
        help="Report path relative to --root.",
    )
    parser.add_argument(
        "--no-preview",
        dest="preview",
        action="store_false",
        help="Do not restart Studio preview or verify /api/rewrite.",
    )
    parser.set_defaults(preview=True)
    parser.add_argument(
        "--preview-url",
        default="",
        help="Existing preview URL to verify when --no-preview is used.",
    )
    parser.add_argument(
        "--no-fallback-check",
        dest="verify_fallback",
        action="store_false",
        help="Skip temporary missing-Codex fallback verification.",
    )
    parser.set_defaults(verify_fallback=True)
    return parser.parse_args()


def env_local_is_ignored(gitignore_text: str) -> bool:
    patterns = {line.strip() for line in gitignore_text.splitlines()}
    return any(pattern in patterns for pattern in {".env*.local", ".env.local", ".env"})


def upsert_env(original: str, values: dict[str, str]) -> str:
    lines = original.splitlines()
    seen: set[str] = set()
    next_lines: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in values:
            next_lines.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            next_lines.append(line)
    for key, value in values.items():
        if key not in seen:
            next_lines.append(f"{key}={value}")
    return "\n".join(next_lines).rstrip() + "\n"


def http_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    method: str = "POST",
    timeout: int = 60,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if method != "GET" else None
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {"Content-Type": "application/json"}).items():
        req.add_header(key, value)
    if method != "GET" and "Content-Type" not in (headers or {}):
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise StepError(f"HTTP {exc.code} from {url}: {redact_text(body[:1000])}") from exc
    except urllib.error.URLError as exc:
        raise StepError(f"HTTP request failed for {url}: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StepError(f"Non-JSON response from {url}: {raw[:500]}") from exc
    if not isinstance(parsed, dict):
        raise StepError(f"Unexpected JSON response from {url}: {type(parsed).__name__}")
    return parsed


def extract_rewritten_text(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    for key in ("rewrittenText", "rewritten", "text", "output"):
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def summarize_change_json(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    wanted: dict[str, Any] = {}
    for key in (
        "id",
        "change_id",
        "state",
        "status",
        "commit",
        "selected_candidate",
        "promotion",
        "project_id",
        "session_id",
    ):
        if key in value:
            wanted[key] = value[key]
    for nested_key in ("change", "result", "delivery", "report"):
        nested = value.get(nested_key)
        if isinstance(nested, dict):
            nested_summary = summarize_change_json(nested)
            if nested_summary:
                wanted[nested_key] = nested_summary
    return wanted or value


def summarize_rewrite_response(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "mode": value.get("mode"),
        "provider": value.get("provider"),
        "model": value.get("model"),
        "rewrittenPreview": extract_rewritten_text(value)[:240],
        "changeSummary": value.get("changeSummary"),
        "warnings": value.get("warnings"),
    }


def reviews_has_open_items(value: Any) -> bool:
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        if isinstance(value.get("items"), list):
            return len(value["items"]) > 0
        for key in ("open", "open_review_count", "blocking_review_count"):
            if isinstance(value.get(key), int) and value[key] > 0:
                return True
    return False


def redact_text(text: str) -> str:
    text = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[REDACTED_OPENAI_KEY]", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer [REDACTED]", text)
    return text


def redact_obj(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_obj(item) for item in value]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if "key" in key.lower() or "secret" in key.lower() or "authorization" in key.lower():
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_obj(item)
        return redacted
    return value


def main() -> int:
    return Runner(parse_args()).run()


if __name__ == "__main__":
    raise SystemExit(main())
