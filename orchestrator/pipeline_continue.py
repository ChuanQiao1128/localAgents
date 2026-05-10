from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_STUDIO = REPO_ROOT / "agent-studio"
KNOWN_VISUAL_VARIANTS = ["minimalist-editorial", "dense-dashboard", "bold-marketing"]


@dataclass
class StepResult:
    name: str
    command: list[str]
    returncode: int
    status: str
    duration_seconds: float
    stdout_path: Path | None = None
    stderr_path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.status not in {"failed", "request_changes"}


@dataclass
class PipelineState:
    project_id: str
    project_path: Path
    run_dir: Path
    steps: list[StepResult] = field(default_factory=list)
    product_review_status: str = "unknown"
    product_review_score: str = "unknown"
    codex_summary_path: Path | None = None
    codex_verdict: str = "unknown"
    next_command: str = ""
    result_json_path: Path | None = None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    state = run_pipeline(args)
    print(f"Pipeline run: {state.run_dir}")
    print(f"Project: {state.project_id}")
    print(f"Product review: {state.product_review_score} {state.product_review_status}")
    if state.codex_summary_path:
        print(f"Codex summary: {state.codex_summary_path}")
    print(f"Codex verdict: {state.codex_verdict}")
    if state.next_command:
        print(f"Next command: {state.next_command}")
    if state.result_json_path:
        print(f"Result JSON: {state.result_json_path}")
    failed = [step for step in state.steps if not step.ok]
    if failed:
        print("Non-passing steps:")
        for step in failed:
            print(f"- {step.name}: {step.status} rc={step.returncode}")
        return 1
    return 0 if state.product_review_status in {"pass", "unknown"} else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="continue_pipeline.py",
        description="Continue a Local Agent Dev Studio project through visual review, implementation, QA, review, and product review.",
    )
    parser.add_argument("--root", default=str(REPO_ROOT), help="Agent Studio workspace root. Default: repo root.")
    parser.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    parser.add_argument("--model", default="gpt-5.5", help="Codex CLI model for multimodal review and summary. Default: gpt-5.5.")
    parser.add_argument(
        "--visual-provider",
        choices=["mock", "v0", "auto"],
        default="mock",
        help="Provider for visual direction generation. Default: mock to avoid v0 credits.",
    )
    parser.add_argument("--prompt-mode", choices=["concise", "full"], default="concise")
    parser.add_argument("--refresh-visual", action="store_true", help="Regenerate visual direction screenshots and rerun Codex visual review.")
    parser.add_argument("--skip-visual", action="store_true", help="Skip visual direction generation and review.")
    parser.add_argument("--skip-codex-summary", action="store_true", help="Do not run final Codex CLI text summary.")
    parser.add_argument("--codex-timeout", type=int, default=1200, help="Seconds to wait for each Codex CLI call. Default: 1200.")
    parser.add_argument("--v0-timeout", type=int, default=900)
    parser.add_argument("--v0-request-timeout", type=int, default=300)
    parser.add_argument("--v0-model", default=None, help="v0 model id. Use 'none' to omit modelId.")
    parser.add_argument("--max-iterations", type=int, default=1, help="Implementation/QA/review/product-review iterations. Default: 1.")
    parser.add_argument(
        "--auto-follow-verdict",
        action="store_true",
        help="When Codex verdict is `continue`, run another iteration until max-iterations is reached.",
    )
    parser.add_argument(
        "--run-hardening",
        action="store_true",
        help="Run implementation hardening after the deterministic draft before QA/review.",
    )
    parser.add_argument("--hardening-target", default="backend-api", help="Implementation hardening target. Default: backend-api.")
    parser.add_argument("--run-unit-tests", action="store_true", help="Run `python3 -m unittest` after the pipeline.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned commands without executing them.")
    return parser


def run_pipeline(args: argparse.Namespace) -> PipelineState:
    workspace_root = Path(args.root).resolve()
    status_payload = _agent_studio_json(workspace_root, ["status", "--json", *_project_args(args.project)])
    project = status_payload.get("project") or {}
    project_id = str(project.get("id") or args.project or "")
    project_path = Path(str(project.get("path") or ""))
    if not project_id or not project_path:
        raise ValueError("Could not resolve a project. Run `./agent-studio status --json` first.")
    run_dir = _new_run_dir(project_path)
    state = PipelineState(project_id=project_id, project_path=project_path, run_dir=run_dir)

    for iteration in range(1, max(1, int(args.max_iterations)) + 1):
        print(f"== Pipeline iteration {iteration}/{max(1, int(args.max_iterations))} ==")
        stage_ok = True
        if not args.skip_visual:
            stage_ok = _run_visual_stage(args, state)
        if stage_ok:
            stage_ok = _run_implementation_stage(args, state)
        if stage_ok:
            stage_ok = _run_quality_stage(args, state)
        if stage_ok:
            state.product_review_status, state.product_review_score = _read_product_review(project_path)
        else:
            state.product_review_status = "blocked"
            state.product_review_score = "not_run"
        if not args.skip_codex_summary:
            summary = _run_codex_summary(args, state, iteration=iteration)
            state.codex_summary_path = summary
            state.codex_verdict, state.next_command = _read_codex_decision(summary)
        else:
            state.codex_verdict = "skipped"

        if not stage_ok:
            state.codex_verdict = "stop_fix_required" if state.codex_verdict in {"unknown", "skipped"} else state.codex_verdict
            break
        if state.codex_verdict == "done":
            break
        if state.codex_verdict == "stop_fix_required":
            break
        if state.codex_verdict == "continue" and args.auto_follow_verdict and iteration < max(1, int(args.max_iterations)):
            _run_remediation_planning(args, state)
            continue
        if state.product_review_status == "pass":
            break
        if iteration < max(1, int(args.max_iterations)):
            _run_remediation_planning(args, state)

    if args.run_unit_tests:
        _run_step(args, state, "unit-tests", ["python3", "-m", "unittest"], raw=True)
    state.result_json_path = _write_result_json(state)
    return state


def _run_visual_stage(args: argparse.Namespace, state: PipelineState) -> bool:
    if args.refresh_visual or not _visual_review_ready(state.project_path):
        command = [
            "design",
            "directions",
            "--project",
            state.project_id,
            "--provider",
            args.visual_provider,
            "--prompt-mode",
            args.prompt_mode,
            "--progress",
        ]
        for variant_id in KNOWN_VISUAL_VARIANTS:
            command.extend(["--variant", variant_id])
        if args.visual_provider == "v0":
            command.extend(["--v0-timeout", str(args.v0_timeout), "--v0-request-timeout", str(args.v0_request_timeout)])
            if args.v0_model:
                command.extend(["--v0-model", args.v0_model])
        if not _run_step(args, state, "design-directions", command).ok:
            return False

    if args.refresh_visual or not _visual_review_ready(state.project_path):
        if not _run_step(
            args,
            state,
            "design-review-variants",
            [
                "design",
                "review-variants",
                "--project",
                state.project_id,
                "--provider",
                "codex-cli",
                "--model",
                args.model,
                "--timeout",
                str(args.codex_timeout),
            ],
        ).ok:
            return False
    return True


def _run_implementation_stage(args: argparse.Namespace, state: PipelineState) -> bool:
    if not _run_step(args, state, "implementation-team", ["implementation", "team", "--project", state.project_id]).ok:
        return False
    if not _run_step(args, state, "implementation-draft", ["implementation", "draft", "--project", state.project_id]).ok:
        return False
    if args.run_hardening and not _run_step(
        args,
        state,
        "implementation-hardening",
        ["implementation", "harden", "--project", state.project_id, "--target", args.hardening_target],
    ).ok:
        return False
    return True


def _run_quality_stage(args: argparse.Namespace, state: PipelineState) -> bool:
    if not _run_step(args, state, "qa", ["run-agent", "qa", "--project", state.project_id, "--materialize"]).ok:
        return False
    if not _run_step(args, state, "reviewer", ["run-agent", "reviewer", "--project", state.project_id, "--materialize"]).ok:
        return False
    if not _run_step(args, state, "product-build-review", ["prd", "build-review", "--project", state.project_id]).ok:
        return False
    return True


def _run_remediation_planning(args: argparse.Namespace, state: PipelineState) -> None:
    _run_step(args, state, "teams-plan", ["teams", "plan", "--project", state.project_id])
    _run_step(args, state, "design-team", ["design", "team", "--project", state.project_id])
    _run_step(args, state, "team-system-review", ["teams", "review", "--project", state.project_id])


def _run_step(
    args: argparse.Namespace,
    state: PipelineState,
    name: str,
    command_args: list[str],
    *,
    raw: bool = False,
) -> StepResult:
    command = command_args if raw else [sys.executable, str(AGENT_STUDIO), "--root", str(Path(args.root).resolve()), *command_args]
    started = time.monotonic()
    print(f"$ {' '.join(command)}")
    if args.dry_run:
        result = StepResult(name=name, command=command, returncode=0, status="dry_run", duration_seconds=0.0)
        state.steps.append(result)
        return result
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    duration = time.monotonic() - started
    step_index = len(state.steps) + 1
    stdout_path = state.run_dir / "steps" / f"{step_index:02d}-{name}.stdout.txt"
    stderr_path = state.run_dir / "steps" / f"{step_index:02d}-{name}.stderr.txt"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(_redact(completed.stdout), encoding="utf-8")
    stderr_path.write_text(_redact(completed.stderr), encoding="utf-8")
    status = _parse_step_status(completed.stdout, completed.returncode)
    result = StepResult(
        name=name,
        command=command,
        returncode=completed.returncode,
        status=status,
        duration_seconds=round(duration, 3),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    state.steps.append(result)
    print(_redact(completed.stdout).strip())
    if completed.stderr.strip():
        print(_redact(completed.stderr).strip(), file=sys.stderr)
    return result


def _run_codex_summary(args: argparse.Namespace, state: PipelineState, *, iteration: int) -> Path | None:
    codex = shutil.which("codex")
    docs_dir = state.project_path / "docs/lead"
    docs_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = docs_dir / "continue-pipeline-codex-prompt.md"
    output_path = docs_dir / "continue-pipeline-codex-summary.md"
    prompt = _codex_summary_prompt(state, iteration=iteration)
    prompt_path.write_text(prompt, encoding="utf-8")
    if not codex:
        output_path.write_text(
            "# Codex Pipeline Summary\n\nCodex CLI was not found on PATH, so no automatic summary was generated.\n",
            encoding="utf-8",
        )
        return output_path
    command = [
        codex,
        "exec",
        "-C",
        str(state.project_path),
        "-m",
        args.model,
        "--sandbox",
        "read-only",
        "--",
        "-",
    ]
    print(f"$ {' '.join(command)}")
    if args.dry_run:
        output_path.write_text("# Codex Pipeline Summary\n\nDry run: Codex CLI summary skipped.\n", encoding="utf-8")
        return output_path
    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        timeout=args.codex_timeout,
        check=False,
    )
    output = (completed.stdout or "").strip()
    if completed.stderr.strip():
        output = f"{output}\n\n<!-- stderr\n{_redact(completed.stderr.strip())}\n-->\n".strip()
    output_path.write_text(_redact(output) + "\n", encoding="utf-8")
    state.steps.append(
        StepResult(
            name="codex-summary",
            command=command,
            returncode=completed.returncode,
            status="completed" if completed.returncode == 0 and output else "failed",
            duration_seconds=0.0,
            stdout_path=output_path,
            stderr_path=None,
        )
    )
    return output_path


def _codex_summary_prompt(state: PipelineState, *, iteration: int) -> str:
    files = [
        "docs/design/selected-visual-direction.md",
        "docs/design/visual-direction-multimodal-review.md",
        "docs/implementation/implementation-contract.json",
        "docs/implementation/hardening-plan.md",
        "apps/web/visual-direction.json",
        "docs/architecture/api.openapi.yaml",
        "docs/architecture/database-schema.md",
        "docs/qa/test-results.md",
        "docs/qa/bugs.md",
        "docs/review/review-report.md",
        "docs/product/post-build-product-review.md",
    ]
    excerpts = []
    for relative in files:
        path = state.project_path / relative
        if path.exists():
            excerpts.append(f"\n\n# {relative}\n\n{_redact(path.read_text(encoding='utf-8'))[:6000]}")
    step_rows = "\n".join(
        f"| {step.name} | {step.status} | {step.returncode} |"
        for step in state.steps
        if step.name != "codex-summary"
    )
    return f"""你是 Local Agent Dev Studio 的 Lead Agent。请读取下面这次自动继续执行的结果，判断是否可以进入下一步。

项目：{state.project_id}
迭代：{iteration}
脚本记录的 product review 状态：{state.product_review_score} {state.product_review_status}

本轮步骤状态：

| Step | Status | Return code |
| --- | --- | ---: |
{step_rows or "| none | unknown | - |"}

请用中文 Markdown 输出，必须包含：

## Verdict

只能写其中一个：`continue` / `stop_fix_required` / `done`

## What Happened

总结本轮跑了什么、关键 winner/QA/review/product review 状态。

## Next Command

如果可以继续，给出下一条最合适的本地命令。不要包含 secret，不要输出 tokenized URL。

## Risks

列出最多 5 个还没解决的风险。

本地报告摘录：
{''.join(excerpts)}
"""


def _agent_studio_json(workspace_root: Path, command_args: list[str]) -> dict[str, Any]:
    command = [sys.executable, str(AGENT_STUDIO), "--root", str(workspace_root), *command_args]
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=True)
    return json.loads(completed.stdout)


def _project_args(project_id: str | None) -> list[str]:
    return ["--project", project_id] if project_id else []


def _new_run_dir(project_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = project_path / ".agent/artifacts/pipeline" / f"continue_{stamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _visual_review_ready(project_path: Path) -> bool:
    review_json = project_path / "docs/design/visual-direction-multimodal-review.json"
    selected = project_path / "docs/design/selected-visual-direction.md"
    if not review_json.exists() or not selected.exists():
        return False
    try:
        payload = json.loads(review_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return payload.get("status") == "completed" and bool(payload.get("winner_id"))


def _read_product_review(project_path: Path) -> tuple[str, str]:
    path = project_path / "docs/product/post-build-product-review.json"
    if not path.exists():
        return "unknown", "unknown"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "unknown", "unknown"
    score = f"{payload.get('final_score', 'unknown')}/{payload.get('max_score', 'unknown')}"
    return str(payload.get("status") or "unknown"), score


def _read_codex_decision(path: Path | None) -> tuple[str, str]:
    if not path or not path.exists():
        return "unknown", ""
    text = path.read_text(encoding="utf-8")
    verdict = _parse_codex_verdict(text)
    return verdict, _parse_next_command(text)


def _parse_codex_verdict(text: str) -> str:
    valid = {"continue", "stop_fix_required", "done"}
    verdict_section = re.search(r"##\s*Verdict\s*(.*?)(?:\n##\s+|\Z)", text, flags=re.IGNORECASE | re.DOTALL)
    candidates = [verdict_section.group(1)] if verdict_section else []
    candidates.append(text[:800])
    for candidate in candidates:
        match = re.search(r"`?(continue|stop_fix_required|done)`?", candidate, flags=re.IGNORECASE)
        if match:
            value = match.group(1).lower()
            if value in valid:
                return value
    return "unknown"


def _parse_next_command(text: str) -> str:
    section = re.search(r"##\s*Next Command\s*(.*?)(?:\n##\s+|\Z)", text, flags=re.IGNORECASE | re.DOTALL)
    if not section:
        return ""
    body = section.group(1).strip()
    fenced = re.search(r"```(?:bash|sh|text)?\s*(.*?)```", body, flags=re.IGNORECASE | re.DOTALL)
    command = fenced.group(1).strip() if fenced else body.splitlines()[0].strip() if body else ""
    return _redact(command)


def _parse_step_status(stdout: str, returncode: int) -> str:
    if returncode != 0:
        return "failed"
    patterns = [
        r"^Status:\s*([A-Za-z0-9_-]+)",
        r"^Run status:\s*([A-Za-z0-9_-]+)",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, stdout, flags=re.MULTILINE)
        if matches:
            return matches[-1]
    review_match = re.search(r"Post-build product review:\s*([0-9]+/[0-9]+)", stdout)
    if review_match:
        return "completed"
    return "completed"


def _redact(text: str) -> str:
    redacted = re.sub(r"__v0_token=[A-Za-z0-9._%-]+", "__v0_token=<redacted>", text)
    redacted = re.sub(r"tvly-[A-Za-z0-9_-]+", "tvly-<redacted>", redacted)
    redacted = re.sub(r"v1:[A-Za-z0-9:_-]+", "v1:<redacted>", redacted)
    return redacted


def _write_result_json(state: PipelineState) -> Path:
    path = state.run_dir / "continue-result.json"
    payload = {
        "project_id": state.project_id,
        "project_path": str(state.project_path),
        "product_review_status": state.product_review_status,
        "product_review_score": state.product_review_score,
        "codex_summary_path": str(state.codex_summary_path) if state.codex_summary_path else None,
        "codex_verdict": state.codex_verdict,
        "next_command": state.next_command,
        "steps": [
            {
                "name": step.name,
                "command": step.command,
                "returncode": step.returncode,
                "status": step.status,
                "duration_seconds": step.duration_seconds,
                "stdout_path": str(step.stdout_path) if step.stdout_path else None,
                "stderr_path": str(step.stderr_path) if step.stderr_path else None,
            }
            for step in state.steps
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
