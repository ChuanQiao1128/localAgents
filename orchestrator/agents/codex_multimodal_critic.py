from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any


@dataclass(frozen=True)
class CodexMultimodalCriticResult:
    prompt_path: Path
    output_path: Path
    json_path: Path
    report_path: Path
    image_count: int
    status: str
    returncode: int


class CodexCliMultimodalCriticAgent:
    def run(
        self,
        *,
        project: dict[str, Any],
        model: str = "gpt-5.5",
        limit: int = 6,
        timeout_seconds: int = 900,
    ) -> CodexMultimodalCriticResult:
        project_path = Path(project["path"])
        output_dir = project_path / "docs/product/example-references"
        output_dir.mkdir(parents=True, exist_ok=True)
        examples_path = output_dir / "top-examples.json"
        visual_critic_path = output_dir / "visual-critic.md"
        prompt_path = output_dir / "multimodal-critic-prompt.md"
        output_path = output_dir / "multimodal-critic-output.md"
        json_path = output_dir / "multimodal-critic.json"
        report_path = output_dir / "multimodal-critic.md"

        images = _select_images(project_path, _load_examples(examples_path), limit)
        if not images:
            raise ValueError("No captured screenshots were found. Run `prd discover-examples` with screenshots first.")
        prompt = _render_prompt(
            project=project,
            images=images,
            existing_visual_critic=visual_critic_path.read_text(encoding="utf-8") if visual_critic_path.exists() else "",
        )
        prompt_path.write_text(prompt, encoding="utf-8")
        command = _codex_command(model=model, project_path=project_path, images=images)
        completed = _run_codex(command, prompt=prompt, timeout_seconds=timeout_seconds)
        output = (completed.stdout or "").strip()
        if completed.stderr:
            output = f"{output}\n\n<!-- stderr\n{completed.stderr.strip()}\n-->\n".strip()
        output_path.write_text(output + "\n", encoding="utf-8")
        status = "completed" if completed.returncode == 0 and output else "failed"
        payload = {
            "provider": "codex-cli",
            "model": model,
            "status": status,
            "returncode": completed.returncode,
            "image_count": len(images),
            "images": [str(path.relative_to(project_path)) for path in images],
            "prompt_path": str(prompt_path.relative_to(project_path)),
            "output_path": str(output_path.relative_to(project_path)),
            "summary": _extract_summary(output),
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report_path.write_text(_render_report(payload, output), encoding="utf-8")
        return CodexMultimodalCriticResult(
            prompt_path=prompt_path,
            output_path=output_path,
            json_path=json_path,
            report_path=report_path,
            image_count=len(images),
            status=status,
            returncode=completed.returncode,
        )


def _load_examples(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _select_images(project_path: Path, examples: list[dict[str, Any]], limit: int) -> list[Path]:
    images: list[Path] = []
    for example in examples:
        for screenshot in example.get("screenshots") or []:
            if not isinstance(screenshot, dict):
                continue
            if screenshot.get("status") != "captured" or not screenshot.get("path"):
                continue
            path = project_path / str(screenshot["path"])
            if path.exists() and path.suffix.lower() == ".png" and path not in images:
                images.append(path)
            if len(images) >= max(1, limit):
                return images
    return images


def _codex_command(*, model: str, project_path: Path, images: list[Path]) -> list[str]:
    codex = shutil.which("codex")
    if not codex:
        raise ValueError("codex CLI was not found on PATH. Install or log in to Codex CLI first.")
    command = [
        codex,
        "exec",
        "-C",
        str(project_path),
        "-m",
        model,
        "--sandbox",
        "read-only",
    ]
    for image in images:
        command.extend(["-i", str(image)])
    command.extend(["--", "-"])
    return command


def _run_codex(command: list[str], *, prompt: str, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )


def _render_prompt(*, project: dict[str, Any], images: list[Path], existing_visual_critic: str) -> str:
    image_list = "\n".join(f"{index}. {path.name}" for index, path in enumerate(images, start=1))
    critic_excerpt = existing_visual_critic[:5000]
    return f"""你是一名资深 UI/UX 设计评审和产品设计负责人。你正在帮助 Local Agent Dev Studio 评审 portfolio builder 的参考截图。

项目目标：
{project.get("idea", "")}

请逐张分析随 prompt 附带的截图。截图顺序如下：
{image_list}

已有本地像素/规则 critic 摘要：
{critic_excerpt or "暂无。"}

请用中文输出 Markdown，结构必须包含：

## 总体判断

- 这些参考是否值得作为 portfolio builder 的设计依据。
- 哪些截图是强参考，哪些只是弱参考。

## 逐图评审

每张图按下面维度评审：
- 第一屏信息层级
- 视觉质量
- 排版/留白
- CTA 和转化路径
- 作品证明感
- 可借鉴点
- 不应复制点
- 作为 portfolio builder 模板/流程参考的价值，给 1-10 分

## Pairwise 结论

- 选出最值得影响产品设计的 3 张截图。
- 说明它们分别适合影响哪个模板或工作流。

## PRD / UI Team 必须吸收的设计要求

- 写成可以交给 UI Team 和 Developer Team 执行的要求。
- 不要建议复制具体品牌、作品、Logo、文案或视觉资产。
- 如果截图展示的是 award site / marketplace / gallery，请明确哪些属于非 MVP 范围。

## 最终建议

- 下一次生成 v0 visual direction 时应该怎样改 prompt。
"""


def _extract_summary(output: str) -> str:
    cleaned = "\n".join(line.strip() for line in output.splitlines() if line.strip())
    return cleaned[:1600]


def _render_report(payload: dict[str, Any], output: str) -> str:
    image_lines = "\n".join(f"- {path}" for path in payload["images"]) or "- No images."
    body = output.strip() or "Codex CLI did not return output."
    return f"""# Codex CLI Multimodal Critic

Status: `{payload['status']}`
Model: `{payload['model']}`
Images: {payload['image_count']}
Return code: {payload['returncode']}

## Images

{image_lines}

## Critic Output

{body}
"""
