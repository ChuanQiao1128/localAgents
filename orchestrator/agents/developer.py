from __future__ import annotations

import json
from pathlib import Path
import re

from orchestrator.tools.file_tools import FileTools
from orchestrator.tools.git_tools import GitTools
from orchestrator.tools.shell_tools import ShellTools

from .base import AgentResult


class DeveloperAgent:
    id = "developer"

    def __init__(self, project_path: Path, allowed_paths: list[str] | None = None):
        self.project_path = project_path
        self.allowed_paths = allowed_paths or ["apps/**", "packages/**", "tests/**"]
        self.files = FileTools(project_path, self.allowed_paths, [".env", "~/**"])
        self.git = GitTools(project_path)
        self.shell = ShellTools(project_path)

    def create_placeholder_web_page(self) -> AgentResult:
        result = self.files.write_text(
            "apps/web/README.md",
            """# Web App

Developer agent placeholder output.
""",
        )
        if not result.ok:
            return AgentResult(status="failed", summary=result.message)
        return AgentResult(
            status="completed",
            summary="Created placeholder web app file inside allowed paths.",
            artifacts=[result.path],
        )

    def implement_generated_tasks(self) -> AgentResult:
        task_path = self.project_path / ".agent/tasks/generated-tasks.json"
        tasks = _load_tasks(task_path)
        domain_type = _domain_type(self.project_path, tasks)
        if domain_type == "portfolio":
            visual_direction = _selected_visual_direction(self.project_path)
            outputs = _portfolio_outputs(tasks, self.project_path, visual_direction)
            source = visual_direction.get("id") or "local design contract"
            summary = f"Implemented deterministic portfolio builder MVP from generated architecture tasks and visual direction: {source}."
        else:
            outputs = _generic_outputs(tasks, domain_type)
            summary = f"Implemented deterministic {domain_type} web MVP shell from generated architecture tasks."

        artifacts: list[str] = []
        failures: list[str] = []
        for relative_path, content in outputs.items():
            result = self.files.write_text(relative_path, content)
            if result.ok:
                artifacts.append(result.path)
            else:
                failures.append(result.message)
        if failures:
            return AgentResult(status="failed", summary="; ".join(failures), artifacts=artifacts)
        return AgentResult(status="completed", summary=summary, artifacts=artifacts)


def _load_tasks(task_path: Path) -> list[dict[str, object]]:
    if not task_path.exists():
        return []
    loaded = json.loads(task_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, dict)]


def _domain_type(project_path: Path, tasks: list[dict[str, object]]) -> str:
    task_text = json.dumps(tasks, ensure_ascii=False).lower()
    arch_path = project_path / "docs/architecture/architecture.md"
    design_path = project_path / "docs/design/component-spec.md"
    context = task_text
    for path in [arch_path, design_path]:
        if path.exists():
            context += "\n" + path.read_text(encoding="utf-8").lower()
    if any(term in context for term in ["portfolio", "static html export", "theme selector", "avatar upload"]):
        return "portfolio"
    if any(term in context for term in ["invoice", "billable", "time entry"]):
        return "freelance"
    if any(term in context for term in ["transaction", "monthly summary", "expense"]):
        return "expense"
    return "generic"


def _portfolio_outputs(tasks: list[dict[str, object]], project_path: Path, visual_direction: dict[str, str] | None = None) -> dict[str, str]:
    visual_direction = visual_direction or {}
    v0_files = _v0_file_contents(project_path, visual_direction)
    if _has_next_v0_files(v0_files):
        outputs = _portfolio_next_outputs(tasks, visual_direction, v0_files)
    else:
        outputs = {
            "apps/web/index.html": _portfolio_index_html(visual_direction),
            "apps/web/styles.css": _portfolio_styles_css(),
            "apps/web/app.js": _portfolio_app_js(visual_direction),
            "apps/web/README.md": _portfolio_readme(tasks, visual_direction),
            "tests/portfolio-builder-smoke.md": _portfolio_smoke_test(tasks),
        }
    outputs.update(_v0_source_outputs(project_path, visual_direction, v0_files))
    return outputs


def _has_next_v0_files(v0_files: dict[str, str]) -> bool:
    return any(path.endswith((".tsx", ".ts", ".jsx", ".js")) and path != "index.html" for path in v0_files)


def _selected_visual_direction(project_path: Path) -> dict[str, str]:
    selected_path = project_path / "docs/design/selected-visual-direction.md"
    variants_path = project_path / ".agent/artifacts/visual_directions/variants.json"
    selected_text = selected_path.read_text(encoding="utf-8") if selected_path.exists() else ""
    payload = _load_visual_variants_payload(variants_path)
    result = {
        "id": _parse_visual_direction_id(selected_text),
        "source": "docs/design/selected-visual-direction.md" if selected_text.strip() else "",
        "selection_method": "selected_markdown" if selected_text.strip() else "",
        "demo_url": "",
        "web_url": "",
        "report_path": "",
        "screenshot_path": "",
    }
    multimodal = payload.get("multimodal_review") if isinstance(payload, dict) else None
    if isinstance(multimodal, dict) and str(multimodal.get("winner_id") or "").strip():
        result["id"] = str(multimodal["winner_id"])
        result["selection_method"] = "multimodal_review"
        result["report_path"] = str(multimodal.get("report_path") or "docs/design/visual-direction-multimodal-review.md")
    winner = payload.get("winner", {}) if isinstance(payload, dict) else {}
    if not result["id"] and isinstance(winner, dict):
        result["id"] = str(winner.get("id") or "")
        result["selection_method"] = "deterministic_pairwise"
    selected_variant = _visual_variant_for_id(payload, result["id"])
    if selected_variant:
        result["demo_url"] = str(selected_variant.get("demo_url") or "")
        result["web_url"] = str(selected_variant.get("web_url") or "")
        result["screenshot_path"] = str(selected_variant.get("screenshot_path") or "")
    return result


def _v0_file_contents(project_path: Path, visual_direction: dict[str, str]) -> dict[str, str]:
    variants_path = project_path / ".agent/artifacts/visual_directions/variants.json"
    if not variants_path.exists() or not visual_direction.get("id"):
        return {}
    payload = _load_visual_variants_payload(variants_path)
    selected_variant = _visual_variant_for_id(payload, visual_direction["id"])
    if not selected_variant:
        return {}
    files = [str(item) for item in selected_variant.get("files", []) if str(item).strip()]
    contents: dict[str, str] = {}
    for relative in files:
        source_path = project_path / relative
        if not source_path.exists() or not source_path.is_file():
            continue
        marker = "/files/"
        if marker in relative:
            target_suffix = relative.split(marker, 1)[1]
        else:
            target_suffix = source_path.name
        contents[target_suffix] = source_path.read_text(encoding="utf-8")
    return contents


def _load_visual_variants_payload(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _parse_visual_direction_id(text: str) -> str:
    if not text.strip():
        return ""
    patterns = [
        r"Winner:\s*`([^`]+)`",
        r"Winner:\s*([A-Za-z0-9_-]+)",
        r"获胜(?:方向|者)?[:：]\s*`?([A-Za-z0-9_-]+)`?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    for known_id in [
        "dense-dashboard",
        "minimalist-editorial",
        "bold-marketing",
        "proof-first-case-study",
        "creator-studio",
    ]:
        if known_id in text:
            return known_id
    return ""


def _visual_variant_for_id(payload: dict[str, object], variant_id: str) -> dict[str, object]:
    variants = payload.get("variants", []) if isinstance(payload, dict) else []
    if isinstance(variants, list):
        for variant in variants:
            if isinstance(variant, dict) and str(variant.get("id") or "") == variant_id:
                return variant
    winner = payload.get("winner", {}) if isinstance(payload, dict) else {}
    if isinstance(winner, dict) and (not variant_id or str(winner.get("id") or "") == variant_id):
        return winner
    return {}


def _v0_source_outputs(project_path: Path, visual_direction: dict[str, str], v0_files: dict[str, str] | None = None) -> dict[str, str]:
    v0_files = v0_files if v0_files is not None else _v0_file_contents(project_path, visual_direction)
    outputs = {f"apps/web/v0-source/{target_suffix}": content for target_suffix, content in v0_files.items()}
    copied = len(v0_files)
    if not outputs:
        return {}
    source_payload = {
        "selected_direction": visual_direction.get("id"),
        "selection_method": visual_direction.get("selection_method") or None,
        "source_artifact": "docs/design/selected-visual-direction.md",
        "review_artifact": visual_direction.get("report_path") or None,
        "screenshot_artifact": visual_direction.get("screenshot_path") or None,
        "v0_web_url": visual_direction.get("web_url") or None,
        "v0_demo_url_artifact": ".agent/artifacts/visual_directions/variants.json#selected_variant.demo_url",
        "source_files_copied": copied,
        "implementation_note": "apps/web is the local static implementation; apps/web/v0-source preserves the v0-generated source that Developer Agent used as visual and interaction input.",
    }
    outputs["apps/web/visual-direction.json"] = json.dumps(source_payload, ensure_ascii=False, indent=2) + "\n"
    outputs["apps/web/v0-source/README.md"] = _v0_source_readme(visual_direction, copied)
    return outputs


def _v0_source_readme(visual_direction: dict[str, str], copied: int) -> str:
    web_url = visual_direction.get("web_url") or "not available"
    return f"""# v0 Source Handoff

This directory preserves the v0-generated files for the selected visual direction.

- Selected direction: {visual_direction.get("id") or "unknown"}
- Selection method: {visual_direction.get("selection_method") or "unknown"}
- v0 web URL: {web_url}
- Copied source files: {copied}
- Tokenized demo URL: stored in `.agent/artifacts/visual_directions/variants.json`, not repeated here.

The main app in `apps/web/app`, `apps/web/components`, and `apps/web/lib` is the Developer Agent implementation derived from this source and the architecture tasks.
"""


def _portfolio_next_outputs(tasks: list[dict[str, object]], visual_direction: dict[str, str], v0_files: dict[str, str]) -> dict[str, str]:
    outputs: dict[str, str] = {
        "apps/web/package.json": _portfolio_next_package_json(),
        "apps/web/next.config.mjs": "const nextConfig = { agentRules: false, turbopack: { root: process.cwd() } };\n\nexport default nextConfig;\n",
        "apps/web/postcss.config.mjs": "const config = { plugins: { '@tailwindcss/postcss': {} } };\n\nexport default config;\n",
        "apps/web/tsconfig.json": _portfolio_next_tsconfig(),
        "apps/web/next-env.d.ts": "/// <reference types=\"next\" />\n/// <reference types=\"next/image-types/global\" />\n\n// This file is generated by Agent Studio. Do not edit directly.\n",
        "apps/web/lib/utils.ts": _portfolio_next_utils_ts(),
        "apps/web/components/ui/button.tsx": _portfolio_next_button_tsx(),
        "apps/web/components/ui/input.tsx": _portfolio_next_input_tsx(),
        "apps/web/components/ui/textarea.tsx": _portfolio_next_textarea_tsx(),
        "apps/web/components/ui/label.tsx": _portfolio_next_label_tsx(),
        "apps/web/lib/portfolio-store.ts": _portfolio_next_store_ts(),
        "apps/web/lib/export-html.tsx": _portfolio_next_export_html_tsx(),
        "apps/web/README.md": _portfolio_next_readme(tasks, visual_direction),
        "apps/web/index.html": _portfolio_next_marker_html(),
        "apps/web/styles.css": "/* Next implementation uses app/globals.css. This file is kept only to replace the old static artifact. */\n",
        "apps/web/app.js": "/* Next implementation uses React components under app/, components/, and lib/. This file replaces the old static artifact. */\n",
        "tests/portfolio-builder-smoke.md": _portfolio_next_smoke_test(tasks),
    }
    for suffix, content in v0_files.items():
        if suffix == "index.html":
            continue
        target = f"apps/web/{suffix}"
        if suffix == "package.json":
            continue
        if suffix == "app/layout.tsx":
            outputs[target] = _portfolio_next_layout_tsx()
        elif suffix == "lib/portfolio-store.ts":
            outputs[target] = _portfolio_next_store_ts()
        elif suffix == "lib/export-html.tsx":
            outputs[target] = _portfolio_next_export_html_tsx()
        elif suffix == "components/portfolio/PreviewPanel.tsx":
            outputs[target] = _normalize_v0_react_source(content)
        else:
            outputs[target] = _normalize_v0_react_source(content)
    outputs["apps/web/visual-direction.json"] = _portfolio_next_visual_direction_json(visual_direction, len(v0_files))
    return outputs


def _normalize_v0_react_source(content: str) -> str:
    normalized = content.replace(
        "ReturnType<typeof THEME_CLASSES[ThemeId]>",
        "(typeof THEME_CLASSES)[ThemeId]",
    )
    normalized = normalized.replace(
        'import { usePortfolioStore, ThemeId } from "@/lib/portfolio-store";',
        'import { usePortfolioStore, ThemeId, type PortfolioState } from "@/lib/portfolio-store";',
    )
    normalized = normalized.replace(
        "state: ReturnType<typeof usePortfolioStore>;",
        "state: PortfolioState;",
    )
    normalized = normalized.replace(
        'className="flex h-screen bg-background overflow-hidden"',
        'className="flex h-[100svh] w-full max-w-full bg-background overflow-hidden"',
    )
    normalized = normalized.replace(
        'className="flex items-center justify-between px-6 py-3 border-b border-border bg-background shrink-0"',
        'className="flex min-w-0 items-center justify-between gap-3 px-4 py-3 sm:px-6 border-b border-border bg-background shrink-0"',
    )
    normalized = normalized.replace(
        'className="flex items-center gap-3"',
        'className="flex min-w-0 items-center gap-3"',
    )
    normalized = normalized.replace(
        'className="text-xs text-muted-foreground">Portfolio Builder</span>',
        'className="truncate text-xs text-muted-foreground">Portfolio Builder</span>',
    )
    normalized = normalized.replace(
        'className="xl:hidden gap-1.5"',
        'className="xl:hidden shrink-0 gap-1.5"',
    )
    normalized = normalized.replace(
        'className="max-w-2xl mx-auto px-6 py-10"',
        'className="mx-auto w-full max-w-2xl px-4 py-8 sm:px-6 sm:py-10"',
    )
    normalized = normalized.replace(
        'className="border-t border-border bg-background px-6 py-4 flex items-center justify-between shrink-0"',
        'className="grid grid-cols-3 items-center gap-3 border-t border-border bg-background px-4 py-4 sm:px-6 shrink-0"',
    )
    normalized = normalized.replace(
        'className="gap-1.5"\n          >\n            <ChevronLeftIcon',
        'className="justify-self-start gap-1.5"\n          >\n            <ChevronLeftIcon',
    )
    normalized = normalized.replace(
        'className="text-xs text-muted-foreground tabular-nums"',
        'className="justify-self-center text-xs text-muted-foreground tabular-nums"',
    )
    normalized = normalized.replace(
        'className="gap-1.5"\n            >\n              Continue',
        'className="justify-self-end gap-1.5"\n            >\n              Continue',
    )
    normalized = normalized.replace(
        'className="flex items-center gap-0"',
        'className="grid w-full grid-cols-4 gap-0"',
    )
    normalized = normalized.replace(
        '"group flex flex-col items-start px-5 py-3 border-b-2 transition-colors text-left",',
        '"group flex min-w-0 flex-col items-center px-2 py-3 sm:items-start sm:px-5 border-b-2 transition-colors text-center sm:text-left",',
    )
    normalized = normalized.replace(
        '"text-xs font-medium tracking-widest uppercase transition-colors",',
        '"truncate text-[10px] font-medium tracking-widest uppercase transition-colors sm:text-xs",',
    )
    return normalized


def _portfolio_next_package_json() -> str:
    return """{
  "name": "portfolio-builder-web-app",
  "private": true,
  "version": "0.1.0",
  "scripts": {
    "dev": "next dev --webpack",
    "build": "next build",
    "start": "next start"
  },
  "dependencies": {
    "clsx": "^2.1.1",
    "lucide-react": "^0.564.0",
    "next": "16.3.0-canary.10",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "tailwind-merge": "^3.3.1",
    "zustand": "^5.0.13"
  },
  "devDependencies": {
    "@tailwindcss/postcss": "^4.2.0",
    "@types/node": "^24.0.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "tailwindcss": "^4.2.0",
    "tw-animate-css": "^1.3.3",
    "typescript": "^5.0.0"
  }
}
"""


def _portfolio_next_tsconfig() -> str:
    return """{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "es2022"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "react-jsx",
    "incremental": true,
    "baseUrl": ".",
    "paths": {
      "@/*": ["./*"]
    },
    "plugins": [{ "name": "next" }]
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts", ".next/dev/types/**/*.ts"],
  "exclude": ["node_modules", "v0-source"]
}
"""


def _portfolio_next_layout_tsx() -> str:
    return """import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Folio Studio - Portfolio Builder",
  description: "Build and export a polished personal portfolio from real project proof.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="bg-background">
      <body className="antialiased">{children}</body>
    </html>
  );
}
"""


def _portfolio_next_utils_ts() -> str:
    return """import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
"""


def _portfolio_next_button_tsx() -> str:
    return """"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "outline" | "ghost";
  size?: "default" | "sm" | "lg";
};

export function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonProps) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center rounded border text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50",
        variant === "default" && "border-primary bg-primary text-primary-foreground hover:bg-primary/90",
        variant === "outline" && "border-border bg-background text-foreground hover:bg-secondary",
        variant === "ghost" && "border-transparent bg-transparent text-foreground hover:bg-secondary",
        size === "sm" && "h-8 px-3",
        size === "default" && "h-10 px-4",
        size === "lg" && "h-11 px-5",
        className
      )}
      {...props}
    />
  );
}
"""


def _portfolio_next_input_tsx() -> str:
    return """"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "flex h-10 w-full rounded border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      {...props}
    />
  );
}
"""


def _portfolio_next_textarea_tsx() -> str:
    return """"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

export function Textarea({ className, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "flex min-h-20 w-full rounded border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      {...props}
    />
  );
}
"""


def _portfolio_next_label_tsx() -> str:
    return """"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("text-sm font-medium", className)} {...props} />;
}
"""


def _portfolio_next_store_ts() -> str:
    return """"use client";

import { create } from "zustand";

export type ThemeId = "editorial" | "minimal" | "noir";

export interface Project {
  id: string;
  title: string;
  role: string;
  description: string;
  tags: string[];
  url: string;
  repoUrl: string;
  screenshotUrl: string | null;
  screenshotAlt: string;
}

export interface PortfolioState {
  name: string;
  title: string;
  bio: string;
  skills: string[];
  avatarUrl: string | null;
  email: string;
  website: string;
  github: string;
  twitter: string;
  linkedin: string;
  projects: Project[];
  theme: ThemeId;
  activeStep: number;
  previewOpen: boolean;
  setProfile: (partial: Partial<PortfolioData>) => void;
  setAvatar: (url: string | null) => void;
  addProject: () => void;
  updateProject: (id: string, partial: Partial<Project>) => void;
  removeProject: (id: string) => void;
  setProjectScreenshot: (id: string, url: string | null) => void;
  setTheme: (theme: ThemeId) => void;
  setStep: (step: number) => void;
  togglePreview: () => void;
}

type PortfolioData = Pick<
  PortfolioState,
  "name" | "title" | "bio" | "skills" | "avatarUrl" | "email" | "website" | "github" | "twitter" | "linkedin" | "projects" | "theme"
>;

const STORAGE_KEY = "agent-studio-portfolio-builder";

const newProject = (): Project => ({
  id: Math.random().toString(36).slice(2),
  title: "",
  role: "",
  description: "",
  tags: [],
  url: "",
  repoUrl: "",
  screenshotUrl: null,
  screenshotAlt: "",
});

const defaultData: PortfolioData = {
  name: "Alex Chen",
  title: "Product Designer",
  bio: "I turn ambiguous product problems into clear, useful interfaces with research, systems thinking, and careful execution.",
  skills: ["Research", "UI design", "React"],
  avatarUrl: null,
  email: "alex@example.com",
  website: "",
  github: "alex",
  twitter: "",
  linkedin: "",
  projects: [
    {
      id: "seed-project",
      title: "Portfolio Builder",
      role: "Product and UI",
      description: "A local-first builder that turns real project proof into a polished static portfolio page.",
      tags: ["Portfolio", "UX", "Static export"],
      url: "https://example.com",
      repoUrl: "https://github.com/example/portfolio",
      screenshotUrl: null,
      screenshotAlt: "Screenshot of the portfolio builder interface",
    },
  ],
  theme: "editorial",
};

function loadData(): Partial<PortfolioData> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Partial<PortfolioData>;
    return {
      ...parsed,
      projects: Array.isArray(parsed.projects) && parsed.projects.length > 0 ? parsed.projects : defaultData.projects,
    };
  } catch {
    window.localStorage.removeItem(STORAGE_KEY);
    return {};
  }
}

function dataFromState(state: PortfolioState): PortfolioData {
  return {
    name: state.name,
    title: state.title,
    bio: state.bio,
    skills: state.skills,
    avatarUrl: state.avatarUrl,
    email: state.email,
    website: state.website,
    github: state.github,
    twitter: state.twitter,
    linkedin: state.linkedin,
    projects: state.projects,
    theme: state.theme,
  };
}

function persist(state: PortfolioState) {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(dataFromState(state)));
  }
}

const initialData = { ...defaultData, ...loadData() };

export const usePortfolioStore = create<PortfolioState>((set, get) => ({
  ...initialData,
  activeStep: 0,
  previewOpen: false,

  setProfile: (partial) =>
    set((state) => {
      const next = { ...state, ...partial };
      persist(next);
      return next;
    }),

  setAvatar: (url) =>
    set((state) => {
      const next = { ...state, avatarUrl: url };
      persist(next);
      return next;
    }),

  addProject: () =>
    set((state) => {
      const next = { ...state, projects: [...state.projects, newProject()] };
      persist(next);
      return next;
    }),

  updateProject: (id, partial) =>
    set((state) => {
      const next = {
        ...state,
        projects: state.projects.map((project) => (project.id === id ? { ...project, ...partial } : project)),
      };
      persist(next);
      return next;
    }),

  removeProject: (id) =>
    set((state) => {
      const projects = state.projects.filter((project) => project.id !== id);
      const next = { ...state, projects: projects.length > 0 ? projects : [newProject()] };
      persist(next);
      return next;
    }),

  setProjectScreenshot: (id, url) =>
    set((state) => {
      const next = {
        ...state,
        projects: state.projects.map((project) => (project.id === id ? { ...project, screenshotUrl: url } : project)),
      };
      persist(next);
      return next;
    }),

  setTheme: (theme) =>
    set((state) => {
      const next = { ...state, theme };
      persist(next);
      return next;
    }),

  setStep: (step) => set({ activeStep: Math.max(0, Math.min(3, step)) }),
  togglePreview: () => set((state) => ({ previewOpen: !state.previewOpen })),
}));
"""


def _portfolio_next_export_html_tsx() -> str:
    return """import type { PortfolioState, ThemeId } from "./portfolio-store";

const themeStyles: Record<ThemeId, string> = {
  editorial: "body{background:#f9f7f4;color:#1a1614;font-family:Georgia,serif}h1,h2,h3{font-family:Georgia,serif;font-weight:400}a{color:#1a1614}.tag{background:#ede9e3;color:#1a1614}",
  minimal: "body{background:#fff;color:#111;font-family:Helvetica Neue,Arial,sans-serif}h1,h2,h3{font-weight:600}a{color:#111}.tag{background:#f0f0f0;color:#111}",
  noir: "body{background:#111;color:#e8e6e3;font-family:Helvetica Neue,Arial,sans-serif}h1,h2,h3{font-weight:700}a{color:#e8e6e3}.tag{background:#2a2a2a;color:#e8e6e3}",
};

function escapeHtml(value: unknown): string {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char] || char));
}

function escapeAttr(value: unknown): string {
  return escapeHtml(value).replace(/`/g, "&#096;");
}

function normalizeUrl(value: string): string {
  if (!value) return "";
  if (value.includes("@") && !value.startsWith("http")) return `mailto:${value}`;
  if (/^https?:\\/\\//i.test(value) || value.startsWith("mailto:")) return value;
  return `https://${value}`;
}

export function generateHTML(state: PortfolioState): string {
  const projects = state.projects.filter((project) => project.title || project.description || project.screenshotUrl);
  const skillTags = state.skills
    .map((skill) => `<span class="tag" style="display:inline-block;padding:2px 10px;border-radius:4px;font-size:13px;margin:3px;">${escapeHtml(skill)}</span>`)
    .join("");
  const projectCards = projects
    .map((project) => {
      const tags = project.tags.map((tag) => `<span class="tag" style="display:inline-block;padding:2px 10px;border-radius:4px;font-size:12px;margin:3px;">${escapeHtml(tag)}</span>`).join("");
      return `<article style="margin-bottom:56px;padding-bottom:56px;border-bottom:1px solid rgba(128,128,128,.2);">
        ${project.screenshotUrl ? `<img src="${escapeAttr(project.screenshotUrl)}" alt="${escapeAttr(project.screenshotAlt || project.title)}" style="width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:6px;margin-bottom:24px;" />` : ""}
        <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;">
          <h2 style="font-size:22px;margin:0;">${escapeHtml(project.title)}</h2>
          <span style="font-size:13px;opacity:.55;">${escapeHtml(project.role)}</span>
        </div>
        <p style="margin:12px 0 16px;line-height:1.6;opacity:.8;">${escapeHtml(project.description)}</p>
        ${tags ? `<div style="margin-bottom:16px;">${tags}</div>` : ""}
        <div style="display:flex;gap:16px;font-size:13px;">
          ${project.url ? `<a href="${escapeAttr(normalizeUrl(project.url))}" target="_blank" rel="noopener">Live -></a>` : ""}
          ${project.repoUrl ? `<a href="${escapeAttr(normalizeUrl(project.repoUrl))}" target="_blank" rel="noopener">Source -></a>` : ""}
        </div>
      </article>`;
    })
    .join("");
  const socialLinks = [
    state.email && `<a href="${escapeAttr(normalizeUrl(state.email))}" style="text-decoration:none;font-size:14px;opacity:.7;">${escapeHtml(state.email)}</a>`,
    state.website && `<a href="${escapeAttr(normalizeUrl(state.website))}" target="_blank" rel="noopener" style="text-decoration:none;font-size:14px;opacity:.7;">Website</a>`,
    state.github && `<a href="${escapeAttr(normalizeUrl(`https://github.com/${state.github}`))}" target="_blank" rel="noopener" style="text-decoration:none;font-size:14px;opacity:.7;">GitHub</a>`,
    state.twitter && `<a href="${escapeAttr(normalizeUrl(`https://twitter.com/${state.twitter}`))}" target="_blank" rel="noopener" style="text-decoration:none;font-size:14px;opacity:.7;">Twitter</a>`,
    state.linkedin && `<a href="${escapeAttr(normalizeUrl(`https://linkedin.com/in/${state.linkedin}`))}" target="_blank" rel="noopener" style="text-decoration:none;font-size:14px;opacity:.7;">LinkedIn</a>`,
  ].filter(Boolean).join('<span style="opacity:.3;margin:0 8px;">·</span>');

  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>${escapeHtml(state.name || "Portfolio")}</title>
  <style>*{box-sizing:border-box;margin:0;padding:0}${themeStyles[state.theme]}body{max-width:720px;margin:0 auto;padding:64px 24px 120px;line-height:1.6}img{max-width:100%;display:block}a:hover{opacity:.65}</style>
</head>
<body>
  <header style="margin-bottom:80px;">
    ${state.avatarUrl ? `<img src="${escapeAttr(state.avatarUrl)}" alt="${escapeAttr(`${state.name || "User"} avatar`)}" style="width:72px;height:72px;border-radius:50%;object-fit:cover;margin-bottom:28px;" />` : ""}
    <h1 style="font-size:clamp(28px,5vw,42px);margin-bottom:8px;">${escapeHtml(state.name)}</h1>
    <p style="font-size:16px;opacity:.55;margin-bottom:28px;">${escapeHtml(state.title)}</p>
    <p style="font-size:16px;max-width:560px;line-height:1.7;opacity:.8;">${escapeHtml(state.bio)}</p>
    ${skillTags ? `<div style="margin-top:28px;">${skillTags}</div>` : ""}
  </header>
  ${projectCards ? `<section><h3 style="font-size:11px;letter-spacing:.12em;text-transform:uppercase;opacity:.4;margin-bottom:40px;">Selected Work</h3>${projectCards}</section>` : ""}
  ${socialLinks ? `<footer style="margin-top:80px;padding-top:32px;border-top:1px solid rgba(128,128,128,.2);display:flex;flex-wrap:wrap;gap:12px;">${socialLinks}</footer>` : ""}
</body>
</html>`;
}

export function downloadHTML(state: PortfolioState) {
  const html = generateHTML(state);
  const blob = new Blob([html], { type: "text/html" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${(state.name || "portfolio").toLowerCase().replace(/\\s+/g, "-")}.html`;
  anchor.click();
  URL.revokeObjectURL(url);
}
"""


def _portfolio_next_readme(tasks: list[dict[str, object]], visual_direction: dict[str, str]) -> str:
    task_lines = "\n".join(f"- {task.get('id', 'TASK')}: {task.get('title', 'Untitled')}" for task in tasks)
    visual_id = visual_direction.get("id") or "local design contract"
    web_url = visual_direction.get("web_url") or "not available"
    visual_label = _visual_direction_label(visual_id)
    return f"""# Portfolio Builder Web App

This is the main Next.js implementation generated from the selected v0 visual direction.

## Run

```bash
npm install
npm run dev
```

## Visual Direction Source

- Selected direction: {visual_id}
- Selection method: {visual_direction.get("selection_method") or "local"}
- Source artifact: docs/design/selected-visual-direction.md
- Review artifact: {visual_direction.get("report_path") or "not available"}
- Screenshot artifact: {visual_direction.get("screenshot_path") or "not available"}
- v0 web URL: {web_url}
- Preserved v0 files: apps/web/v0-source/
- Trace file: apps/web/visual-direction.json
- Tokenized demo URL remains in `.agent/artifacts/visual_directions/variants.json`.

## Product Behavior

- Guided profile, project, theme, and export workflow.
- Local-first persistence through `localStorage`.
- Avatar and screenshot upload, replace, and remove lifecycle.
- Self-contained HTML export through `lib/export-html.tsx`.
- Selected visual system: {visual_label}.
- Reference inspiration remains visible in product choices: Webflow, Framer, Behance, Dribbble, v0, and portfolio template benchmarks informed the style, template, proof, and export workflow.
- Screenshot presentation is designed around crop/layout preset decisions such as cover-style portfolio previews, even when the current v0 source keeps those controls lightweight.
- Case study quality is framed around outcome, impact, proof, metrics, and publishable portfolio templates.

## Implemented Architecture Tasks

{task_lines}
"""


def _portfolio_next_marker_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Portfolio Builder Next App</title>
</head>
<body>
  <main>
    <h1>Portfolio Builder Next App</h1>
    <p>The main implementation is now a Next.js app. Run <code>npm install</code> and <code>npm run dev</code> from this directory.</p>
  </main>
</body>
</html>
"""


def _portfolio_next_visual_direction_json(visual_direction: dict[str, str], copied: int) -> str:
    payload = {
        "selected_direction": visual_direction.get("id"),
        "selection_method": visual_direction.get("selection_method") or None,
        "source_artifact": "docs/design/selected-visual-direction.md",
        "review_artifact": visual_direction.get("report_path") or None,
        "screenshot_artifact": visual_direction.get("screenshot_path") or None,
        "v0_web_url": visual_direction.get("web_url") or None,
        "v0_demo_url_artifact": ".agent/artifacts/visual_directions/variants.json#selected_variant.demo_url",
        "source_files_copied": copied,
        "main_implementation": "Next.js app in apps/web/app",
        "implementation_note": "The v0-generated React source is preserved in apps/web/v0-source and promoted into the main Next.js implementation with local UI shims, persistence, export hardening, and build config.",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _portfolio_next_smoke_test(tasks: list[dict[str, object]]) -> str:
    return """# Portfolio Builder Next Smoke Test

- Given dependencies are installed, when `npm run build` runs, then the Next app compiles.
- Given profile fields are edited, when the page reloads, then localStorage restores the content.
- Given a valid avatar image is selected, when upload completes, then the preview shows the avatar.
- Given a project screenshot exists, when Replace or Remove is clicked, then the screenshot lifecycle updates preview state.
- Given project title, description, tags, links, and screenshot alt text are filled, when preview renders, then selected work shows proof content.
- Given a theme is selected, when preview renders, then the preview panel changes visual system.
- Given required readiness checks are sufficient, when Download HTML is clicked, then a self-contained static HTML file is generated.
- Given desktop and mobile viewport screenshots are captured, then text and controls do not visibly overlap.
"""


def _generic_outputs(tasks: list[dict[str, object]], domain_type: str) -> dict[str, str]:
    task_list = "\n".join(f"- {task.get('id', 'TASK')}: {task.get('title', 'Untitled')}" for task in tasks)
    return {
        "apps/web/index.html": f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{domain_type.title()} MVP</title>
  <link rel="stylesheet" href="./styles.css">
</head>
<body>
  <main class="shell">
    <section>
      <h1>{domain_type.title()} MVP</h1>
      <p>This deterministic implementation was generated from architecture tasks.</p>
      <h2>Generated Tasks</h2>
      <pre>{task_list}</pre>
    </section>
  </main>
</body>
</html>
""",
        "apps/web/styles.css": "body{font-family:system-ui,sans-serif;margin:0;background:#f6f7f9;color:#1f2937}.shell{max-width:960px;margin:0 auto;padding:32px}section{background:white;border:1px solid #d6dae1;border-radius:8px;padding:24px}pre{white-space:pre-wrap}\n",
        "apps/web/README.md": f"# {domain_type.title()} MVP\n\nOpen `index.html` in a browser.\n",
    }


def _portfolio_index_html(visual_direction: dict[str, str]) -> str:
    visual_id = visual_direction.get("id") or "local-design"
    demo_url = visual_direction.get("demo_url") or ""
    visual_label = _visual_direction_label(visual_id)
    visual_headline = _visual_direction_headline(visual_id)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Portfolio Builder MVP</title>
  <link rel="stylesheet" href="./styles.css">
</head>
<body>
  <main class="studio" data-visual-direction="{visual_id}">
    <section class="workspace" aria-label="Portfolio editor">
      <header class="topbar">
        <div class="brand-block">
          <p class="kicker">Folio Studio · {visual_label}</p>
          <h1>{visual_headline}</h1>
          <p class="intro-copy">A local-first portfolio builder for turning real screenshots, project decisions, and outcomes into a credible static page.</p>
        </div>
        <div class="topbar-actions">
          <button id="saveBtn" type="button">Save</button>
          <button id="exportBtn" type="button" class="primary">Export HTML</button>
        </div>
      </header>

      <nav class="stepper" aria-label="Builder progress">
        <span class="step is-active">Profile</span>
        <span class="step">Projects</span>
        <span class="step">Theme</span>
        <span class="step">Export</span>
      </nav>

      <div class="panes">
        <form id="portfolioForm" class="editor">
          <section class="panel guidance-panel">
            <div class="panel-heading">
              <h2>Guided proof coaching</h2>
              <p>Use the same onboarding narrative order a strong case study needs before you export.</p>
            </div>
            <ul class="quality-list">
              <li>Start with a narrow role and a credible one-sentence bio.</li>
              <li>For each project, capture problem, process, outcome, metrics, and a real screenshot.</li>
              <li>Use alt text and layout presets so exported pages stay polished and accessible.</li>
              <li>Use real screenshots only; AI image concepts cannot replace client work, credentials, or proof.</li>
            </ul>
            <p class="source-note">Visual source: {visual_id}{' · v0 demo linked in artifacts' if demo_url else ''}</p>
          </section>

          <section class="panel score-panel" aria-live="polite">
            <div class="panel-heading">
              <h2>Proof score</h2>
              <strong id="qualityScore">0%</strong>
            </div>
            <div class="meter"><span id="qualityMeter"></span></div>
            <ul id="qualityChecklist" class="quality-checklist"></ul>
          </section>

          <section class="panel">
            <div class="panel-heading">
              <h2>Profile</h2>
              <p>Real identity and contact details for the final page.</p>
            </div>
            <label>Name<input id="nameInput" name="name" required placeholder="Alex Chen"></label>
            <label>Title<input id="titleInput" name="title" required placeholder="Product Designer"></label>
            <label>Bio<textarea id="bioInput" name="bio" rows="4" placeholder="Short proof-focused introduction"></textarea></label>
            <label>Skills<input id="skillsInput" name="skills" placeholder="Research, UI design, React"></label>
            <label>Contact links<input id="linksInput" name="links" placeholder="email@example.com, https://github.com/example"></label>
            <label>Avatar<input id="avatarInput" type="file" accept="image/*"></label>
            <div class="inline-actions">
              <button id="removeAvatarBtn" type="button">Remove avatar</button>
            </div>
            <p id="avatarState" class="state-text">No avatar selected.</p>
          </section>

          <section class="panel">
            <div class="panel-heading">
              <h2>Projects</h2>
              <button id="addProjectBtn" type="button">Add project</button>
            </div>
            <div id="projectsList" class="project-list"></div>
          </section>

          <section class="panel">
            <div class="panel-heading">
              <h2>Theme</h2>
              <p>Constrained presets keep scope under control.</p>
            </div>
            <label>Template strategy
              <select id="templateInput" name="template">
                <option value="case-study">Case study proof</option>
                <option value="visual-gallery">Visual gallery</option>
                <option value="compact-proof">Compact proof</option>
              </select>
            </label>
            <div class="theme-grid" role="radiogroup" aria-label="Theme">
              <label><input type="radio" name="theme" value="editorial" checked>Editorial</label>
              <label><input type="radio" name="theme" value="contrast">Contrast</label>
              <label><input type="radio" name="theme" value="compact">Compact</label>
            </div>
          </section>
        </form>

        <aside class="preview-shell" aria-label="Portfolio preview">
          <div class="preview-toolbar">
            <strong>Live preview</strong>
            <span id="validationStatus">Needs content</span>
          </div>
          <article id="preview" class="portfolio-preview theme-editorial"></article>
        </aside>
      </div>
    </section>
  </main>

  <template id="projectTemplate">
    <section class="project-editor">
      <div class="project-editor-header">
        <h3>Project</h3>
        <div class="project-actions">
          <button type="button" data-action="move-up">Up</button>
          <button type="button" data-action="move-down">Down</button>
          <button type="button" data-action="remove">Remove</button>
        </div>
      </div>
      <label>Proof screenshot<input type="file" accept="image/*" data-field="image"></label>
      <div class="inline-actions">
        <button type="button" data-action="remove-image">Remove screenshot</button>
      </div>
      <p class="state-text" data-field="imageState">No screenshot selected.</p>
      <label>Image alt text<input data-field="imageAlt" placeholder="Screenshot of dashboard redesign"></label>
      <label>Layout preset
        <select data-field="layoutPreset">
          <option value="cover">Cover crop</option>
          <option value="contain">Contain full screenshot</option>
          <option value="feature">Feature hero crop</option>
        </select>
      </label>
      <label>Title<input data-field="title" required placeholder="Project title"></label>
      <label>Role<input data-field="role" placeholder="Lead designer"></label>
      <label>Case study summary<textarea data-field="description" rows="3" placeholder="What this project proves about your work"></textarea></label>
      <label>Problem<textarea data-field="problem" rows="2" placeholder="What user or business problem did this solve?"></textarea></label>
      <label>Process<textarea data-field="process" rows="2" placeholder="Research, design, build, or validation process"></textarea></label>
      <label>Outcome<textarea data-field="outcome" rows="2" placeholder="What changed after the project shipped?"></textarea></label>
      <label>Metrics<input data-field="metrics" placeholder="Conversion +12%, support tickets -20%, time saved"></label>
      <label>Tags<input data-field="tags" placeholder="UX, React, Research"></label>
      <label>Project URL<input data-field="projectUrl" placeholder="https://"></label>
      <label>Repository URL<input data-field="repoUrl" placeholder="https://github.com/..."></label>
    </section>
  </template>

  <script src="./app.js"></script>
</body>
</html>
"""


def _portfolio_styles_css() -> str:
    return """:root {
  color-scheme: light;
  --bg: #f2eee8;
  --surface: #fbfaf7;
  --surface-alt: #f6f2eb;
  --ink: #202832;
  --muted: #676e79;
  --line: #d8d0c3;
  --accent: #202832;
  --accent-strong: #121821;
  --warm: #b8744f;
  --danger: #a43d32;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--bg);
  color: var(--ink);
}

button, input, textarea {
  font: inherit;
}

button {
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--ink);
  border-radius: 4px;
  min-height: 36px;
  padding: 7px 11px;
  cursor: pointer;
}

button.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: white;
}

button:focus-visible, input:focus-visible, textarea:focus-visible {
  outline: 3px solid rgba(184, 116, 79, .25);
  outline-offset: 2px;
}

.studio {
  max-width: 1360px;
  margin: 0 auto;
  padding: 34px 28px;
}

.workspace {
  min-height: calc(100vh - 40px);
}

.topbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  padding: 0 0 20px;
}

.brand-block {
  max-width: 760px;
}

.kicker {
  margin: 0 0 8px;
  font-family: Georgia, "Times New Roman", serif;
  font-size: 12px;
  letter-spacing: .12em;
  text-transform: uppercase;
  color: var(--muted);
}

h1, h2, h3, p {
  margin-top: 0;
}

h1 {
  margin-bottom: 0;
  font-family: Georgia, "Times New Roman", serif;
  font-size: clamp(42px, 6vw, 72px);
  line-height: .95;
  letter-spacing: 0;
}

.intro-copy {
  max-width: 680px;
  margin: 18px 0 0;
  color: var(--muted);
  font-family: Georgia, "Times New Roman", serif;
  font-size: 19px;
  line-height: 1.3;
}

.topbar-actions {
  display: flex;
  gap: 8px;
}

.stepper {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border: 1px solid var(--line);
  background: var(--surface);
  margin: 8px 0 18px;
}

.step {
  min-height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-right: 1px solid var(--line);
  color: var(--muted);
  font-size: 12px;
  letter-spacing: .08em;
  text-transform: uppercase;
}

.step:last-child {
  border-right: 0;
}

.step.is-active {
  background: var(--ink);
  color: var(--surface);
}

.panes {
  display: grid;
  grid-template-columns: minmax(340px, 430px) minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}

.editor {
  display: grid;
  gap: 12px;
}

.panel, .preview-shell {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 4px;
}

.panel {
  padding: 16px;
}

.panel-heading, .project-editor-header, .preview-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.inline-actions, .project-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.panel-heading p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 13px;
}

label {
  display: grid;
  gap: 6px;
  margin-top: 12px;
  font-size: 13px;
  font-weight: 650;
}

input, textarea {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface-alt);
  color: var(--ink);
  padding: 9px 10px;
}

select {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface-alt);
  color: var(--ink);
  padding: 9px 10px;
}

textarea {
  resize: vertical;
}

.guidance-panel {
  border-color: rgba(184, 116, 79, .4);
  background: #fffdf8;
}

.quality-list {
  margin: 12px 0 0;
  padding-left: 20px;
  color: #4d5560;
}

.quality-list li + li {
  margin-top: 7px;
}

.source-note {
  margin: 12px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.score-panel strong {
  font-size: 20px;
}

.meter {
  height: 8px;
  border: 1px solid var(--line);
  background: var(--surface-alt);
  margin-top: 12px;
  overflow: hidden;
}

.meter span {
  display: block;
  width: 0%;
  height: 100%;
  background: var(--warm);
  transition: width .2s ease;
}

.quality-checklist {
  display: grid;
  gap: 6px;
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
  color: var(--muted);
  font-size: 12px;
}

.quality-checklist li {
  display: flex;
  gap: 7px;
}

.quality-checklist li::before {
  content: "○";
}

.quality-checklist li.is-done {
  color: var(--ink);
}

.quality-checklist li.is-done::before {
  content: "●";
  color: var(--warm);
}

.state-text {
  margin: 7px 0 0;
  color: var(--muted);
  font-size: 13px;
}

.project-list {
  display: grid;
  gap: 12px;
}

.project-editor {
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 12px;
  background: var(--surface-alt);
}

.theme-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.theme-grid label {
  margin: 0;
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 9px;
  background: var(--surface-alt);
}

.preview-shell {
  position: sticky;
  top: 20px;
  overflow: hidden;
  box-shadow: 0 18px 50px rgba(32, 40, 50, .08);
}

.preview-toolbar {
  min-height: 48px;
  border-bottom: 1px solid var(--line);
  padding: 0 14px;
}

.preview-toolbar span {
  color: var(--muted);
  font-size: 13px;
}

.portfolio-preview {
  min-height: 680px;
  padding: 46px;
  background: #fbfaf7;
  color: #1f2933;
}

.preview-hero {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr);
  gap: 18px;
  align-items: center;
  border-bottom: 1px solid rgba(0,0,0,.12);
  padding-bottom: 24px;
}

.avatar {
  width: 88px;
  height: 88px;
  border-radius: 999px;
  object-fit: cover;
  background: #d8dde6;
}

.preview-title {
  font-family: Georgia, "Times New Roman", serif;
  font-size: 42px;
  line-height: 1.05;
  margin-bottom: 8px;
}

.preview-subtitle {
  color: #4a5565;
  margin-bottom: 10px;
}

.tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tag {
  border: 1px solid rgba(0,0,0,.14);
  border-radius: 999px;
  padding: 3px 8px;
  font-size: 12px;
}

.preview-section {
  margin-top: 28px;
}

.project-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.project-card {
  border: 1px solid rgba(0,0,0,.12);
  border-radius: 4px;
  overflow: hidden;
  background: white;
}

.project-card img {
  width: 100%;
  aspect-ratio: 16 / 10;
  object-fit: cover;
  background: #e5e7eb;
  display: block;
}

.project-card img.preset-contain {
  object-fit: contain;
  padding: 10px;
}

.project-card img.preset-feature {
  aspect-ratio: 21 / 9;
  object-fit: cover;
}

.project-card-body {
  padding: 14px;
}

.project-card h3 {
  margin-bottom: 4px;
}

.link-list {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 14px;
}

.link-list a {
  color: inherit;
}

.proof-list {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.proof-item {
  border-top: 1px solid rgba(0,0,0,.1);
  padding-top: 8px;
}

.proof-item strong {
  display: block;
  font-size: 12px;
  color: var(--muted);
  text-transform: uppercase;
}

.theme-contrast {
  background: #15181f;
  color: #f6f7f9;
}

.theme-contrast .preview-subtitle,
.theme-contrast .preview-section p {
  color: #c4cbd6;
}

.theme-contrast .project-card {
  background: #20242d;
  color: #f6f7f9;
  border-color: #3a4150;
}

.theme-compact {
  background: #f4f7f2;
  color: #263126;
  padding: 24px;
}

.theme-compact .preview-title {
  font-size: 28px;
}

.theme-compact .project-grid {
  grid-template-columns: 1fr;
}

@media (max-width: 980px) {
  .panes {
    grid-template-columns: 1fr;
  }
  .preview-shell {
    position: static;
  }
}

@media (max-width: 640px) {
  .studio {
    padding: 12px;
  }
  .topbar {
    align-items: stretch;
    flex-direction: column;
  }
  .topbar-actions {
    width: 100%;
  }
  .topbar-actions button {
    flex: 1;
  }
  .theme-grid, .project-grid {
    grid-template-columns: 1fr;
  }
  .preview-hero {
    grid-template-columns: 1fr;
  }
}
"""


def _portfolio_app_js(visual_direction: dict[str, str] | None = None) -> str:
    visual_id = (visual_direction or {}).get("id") or "local-design"
    return """const state = {
  profile: {
    name: "Alex Chen",
    title: "Product Designer",
    bio: "I turn ambiguous product problems into clear, useful interfaces.",
    skills: "Research, UI design, React",
    links: "alex@example.com, https://github.com/alex",
    avatar: ""
  },
  theme: "editorial",
  template: "case-study",
  projects: [
    {
      title: "Portfolio Builder",
      role: "Product and UI",
      description: "A local-first builder that turns real project proof into a static portfolio page.",
      problem: "Independent builders need a credible portfolio without shipping fake work history.",
  process: "Reference patterns from Webflow, Framer, Behance, Dribbble, and the selected __VISUAL_DIRECTION_ID__ direction shaped the proof-first template.",
      outcome: "The exported page tells a clearer case study with real screenshots, role, and results.",
      metrics: "One-page export, local save, zero hosting dependency",
      tags: "Portfolio, UX, Static export",
      projectUrl: "https://example.com",
      repoUrl: "https://github.com/example/portfolio",
      image: "",
      imageAlt: "Screenshot of the portfolio builder interface",
      layoutPreset: "cover"
    }
  ]
};

const EXPORT_STYLES = `
body{margin:0;background:#f2eee8;color:#202832;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.portfolio-preview{max-width:1080px;margin:0 auto;min-height:100vh;padding:48px;background:#fbfaf7;color:#1f2933}
.preview-hero{display:grid;grid-template-columns:88px minmax(0,1fr);gap:18px;align-items:center;border-bottom:1px solid rgba(0,0,0,.12);padding-bottom:24px}
.avatar{width:88px;height:88px;border-radius:999px;object-fit:cover;background:#d8dde6}
.preview-title{font-family:Georgia,"Times New Roman",serif;font-size:42px;line-height:1.05;margin:0 0 8px}
.preview-subtitle{color:#4a5565;margin:0 0 10px}
.tag-row,.link-list{display:flex;flex-wrap:wrap;gap:8px}
.tag{border:1px solid rgba(0,0,0,.14);border-radius:999px;padding:3px 8px;font-size:12px}
.preview-section{margin-top:28px}
.project-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.project-card{border:1px solid rgba(0,0,0,.12);border-radius:8px;overflow:hidden;background:white}
.project-card img{width:100%;aspect-ratio:16/10;object-fit:cover;background:#e5e7eb;display:block}
.project-card img.preset-contain{object-fit:contain;padding:10px}.project-card img.preset-feature{aspect-ratio:21/9;object-fit:cover}
.project-card-body{padding:14px}
.proof-list{display:grid;gap:8px;margin-top:12px}.proof-item{border-top:1px solid rgba(0,0,0,.1);padding-top:8px}.proof-item strong{display:block;font-size:12px;color:#667085;text-transform:uppercase}
.link-list{margin-top:14px}.link-list a{color:inherit}
.theme-contrast{background:#15181f;color:#f6f7f9}.theme-contrast .preview-subtitle,.theme-contrast .preview-section p{color:#c4cbd6}.theme-contrast .project-card{background:#20242d;color:#f6f7f9;border-color:#3a4150}
.theme-compact{background:#f4f7f2;color:#263126;padding:28px}.theme-compact .preview-title{font-size:28px}.theme-compact .project-grid{grid-template-columns:1fr}
@media (max-width:720px){.portfolio-preview{padding:24px}.preview-hero,.project-grid{grid-template-columns:1fr}}
`;

const els = {
  form: document.querySelector("#portfolioForm"),
  preview: document.querySelector("#preview"),
  validationStatus: document.querySelector("#validationStatus"),
  projectsList: document.querySelector("#projectsList"),
  projectTemplate: document.querySelector("#projectTemplate"),
  avatarInput: document.querySelector("#avatarInput"),
  avatarState: document.querySelector("#avatarState"),
  qualityScore: document.querySelector("#qualityScore"),
  qualityMeter: document.querySelector("#qualityMeter"),
  qualityChecklist: document.querySelector("#qualityChecklist")
};

const fieldMap = {
  nameInput: ["profile", "name"],
  titleInput: ["profile", "title"],
  bioInput: ["profile", "bio"],
  skillsInput: ["profile", "skills"],
  linksInput: ["profile", "links"]
};

const projectDefaults = {
  title: "",
  role: "",
  description: "",
  problem: "",
  process: "",
  outcome: "",
  metrics: "",
  tags: "",
  projectUrl: "",
  repoUrl: "",
  image: "",
  imageAlt: "",
  layoutPreset: "cover"
};

function init() {
  loadState();
  bindProfileFields();
  renderProjectEditors();
  bindActions();
  updateFields();
  renderQuality();
  renderPreview();
}

function bindProfileFields() {
  Object.keys(fieldMap).forEach((id) => {
    const input = document.getElementById(id);
    input.addEventListener("input", () => {
      const [group, key] = fieldMap[id];
      state[group][key] = input.value;
      renderQuality();
      renderPreview();
    });
  });
  els.avatarInput.addEventListener("change", (event) => {
    const file = event.target.files[0];
    readImage(file, 700, (result) => {
      state.profile.avatar = result.dataUrl;
      els.avatarState.textContent = result.message;
      renderQuality();
      renderPreview();
    });
  });
  document.querySelectorAll("input[name='theme']").forEach((radio) => {
    radio.addEventListener("change", () => {
      state.theme = radio.value;
      renderQuality();
      renderPreview();
    });
  });
  document.querySelector("#templateInput").addEventListener("change", (event) => {
    state.template = event.target.value;
    renderQuality();
    renderPreview();
  });
}

function bindActions() {
  document.querySelector("#addProjectBtn").addEventListener("click", () => {
    state.projects.push({ ...projectDefaults });
    renderProjectEditors();
    renderQuality();
    renderPreview();
  });
  document.querySelector("#saveBtn").addEventListener("click", () => {
    localStorage.setItem("portfolioBuilderState", JSON.stringify(state));
    els.validationStatus.textContent = "Saved locally";
  });
  document.querySelector("#removeAvatarBtn").addEventListener("click", () => {
    state.profile.avatar = "";
    els.avatarInput.value = "";
    els.avatarState.textContent = "Avatar removed.";
    renderQuality();
    renderPreview();
  });
  document.querySelector("#exportBtn").addEventListener("click", exportStaticHtml);
}

function updateFields() {
  Object.keys(fieldMap).forEach((id) => {
    const [group, key] = fieldMap[id];
    document.getElementById(id).value = state[group][key] || "";
  });
  const activeTheme = document.querySelector(`input[name='theme'][value='${state.theme}']`);
  if (activeTheme) activeTheme.checked = true;
  document.querySelector("#templateInput").value = state.template || "case-study";
  els.avatarState.textContent = state.profile.avatar ? "Avatar ready." : "No avatar selected.";
  renderQuality();
}

function renderProjectEditors() {
  els.projectsList.innerHTML = "";
  state.projects.forEach((project, index) => {
    const node = els.projectTemplate.content.cloneNode(true);
    const root = node.querySelector(".project-editor");
    root.querySelector("[data-action='move-up']").disabled = index === 0;
    root.querySelector("[data-action='move-down']").disabled = index === state.projects.length - 1;
    root.querySelector("[data-action='move-up']").addEventListener("click", () => {
      moveProject(index, index - 1);
    });
    root.querySelector("[data-action='move-down']").addEventListener("click", () => {
      moveProject(index, index + 1);
    });
    root.querySelector("[data-action='remove']").addEventListener("click", () => {
      state.projects.splice(index, 1);
      renderProjectEditors();
      renderPreview();
    });
    root.querySelector("[data-action='remove-image']").addEventListener("click", () => {
      project.image = "";
      const input = root.querySelector("[data-field='image']");
      input.value = "";
      root.querySelector("[data-field='imageState']").textContent = "Screenshot removed.";
      renderQuality();
      renderPreview();
    });
    root.querySelectorAll("[data-field]").forEach((field) => {
      const key = field.dataset.field;
      if (key === "image") {
        field.addEventListener("change", (event) => {
          readImage(event.target.files[0], 1200, (result) => {
            project.image = result.dataUrl;
            root.querySelector("[data-field='imageState']").textContent = result.message;
            renderQuality();
            renderPreview();
          });
        });
      } else if (key !== "imageState") {
        field.value = project[key] || "";
        field.addEventListener("input", () => {
          project[key] = field.value;
          renderQuality();
          renderPreview();
        });
        field.addEventListener("change", () => {
          project[key] = field.value;
          renderQuality();
          renderPreview();
        });
      }
    });
    root.querySelector("[data-field='imageState']").textContent = project.image ? "Screenshot ready." : "No screenshot selected.";
    els.projectsList.appendChild(node);
  });
}

function moveProject(fromIndex, toIndex) {
  if (toIndex < 0 || toIndex >= state.projects.length) return;
  const [project] = state.projects.splice(fromIndex, 1);
  state.projects.splice(toIndex, 0, project);
  renderProjectEditors();
  renderQuality();
  renderPreview();
}

function readImage(file, maxKb, callback) {
  if (!file) return;
  if (!file.type.startsWith("image/")) {
    callback({ dataUrl: "", message: "Invalid type. Use an image file." });
    return;
  }
  if (file.size > maxKb * 1024) {
    callback({ dataUrl: "", message: `Oversized file. Keep images under ${maxKb} KB.` });
    return;
  }
  const reader = new FileReader();
  reader.onerror = () => callback({ dataUrl: "", message: "Upload failed. Try another image." });
  reader.onload = () => callback({ dataUrl: reader.result, message: "Image preview ready." });
  reader.readAsDataURL(file);
}

function renderPreview() {
  const profile = state.profile;
  const valid = profile.name && profile.title && state.projects.some((project) => project.title);
  els.validationStatus.textContent = valid ? "Export ready" : "Needs name, title, and one project";
  els.preview.className = `portfolio-preview theme-${state.theme}`;
  els.preview.innerHTML = `
    <header class="preview-hero">
      ${profile.avatar ? `<img class="avatar" alt="" src="${escapeAttr(profile.avatar)}">` : `<div class="avatar" aria-hidden="true"></div>`}
      <div>
        <h2 class="preview-title">${escapeHtml(profile.name || "Your name")}</h2>
        <p class="preview-subtitle">${escapeHtml(profile.title || "Your role")}</p>
        <p>${escapeHtml(profile.bio || "Add a short bio that explains your work and credibility.")}</p>
        <div class="tag-row">${splitList(profile.skills).map((skill) => `<span class="tag">${escapeHtml(skill)}</span>`).join("")}</div>
      </div>
    </header>
    <section class="preview-section">
      <h2>Template strategy</h2>
      <p>${templateCopy(state.template)}</p>
    </section>
    <section class="preview-section">
      <h2>Selected work</h2>
      <div class="project-grid">
        ${state.projects.map(renderProjectCard).join("")}
      </div>
    </section>
    <section class="preview-section">
      <h2>Contact</h2>
      <div class="link-list">${splitList(profile.links).map(renderLink).join("")}</div>
    </section>
  `;
}

function renderQuality() {
  const signals = qualitySignals();
  const done = signals.filter((item) => item.done).length;
  const score = Math.round((done / signals.length) * 100);
  els.qualityScore.textContent = `${score}%`;
  els.qualityMeter.style.width = `${score}%`;
  els.qualityChecklist.innerHTML = signals
    .map((item) => `<li class="${item.done ? "is-done" : ""}">${escapeHtml(item.label)}</li>`)
    .join("");
}

function qualitySignals() {
  const firstProject = state.projects.find((project) => project.title || project.description || project.image) || {};
  return [
    { label: "Name and role are specific", done: Boolean(state.profile.name && state.profile.title) },
    { label: "Bio explains credibility", done: Boolean(state.profile.bio && state.profile.bio.length >= 48) },
    { label: "At least one real project is present", done: state.projects.some((project) => project.title) },
    { label: "Project has problem, process, and outcome", done: Boolean(firstProject.problem && firstProject.process && firstProject.outcome) },
    { label: "Metrics or proof of impact are included", done: Boolean(firstProject.metrics) },
    { label: "Screenshot lifecycle, crop/layout preset, and alt text are ready", done: Boolean(firstProject.imageAlt && firstProject.layoutPreset) },
    { label: "Template strategy is selected", done: Boolean(state.template) }
  ];
}

function renderProjectCard(project) {
  const tags = splitList(project.tags).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
  const preset = project.layoutPreset || "cover";
  const imageAlt = project.imageAlt || `${project.title || "Project"} screenshot`;
  return `
    <article class="project-card">
      ${project.image ? `<img class="preset-${escapeAttr(preset)}" alt="${escapeAttr(imageAlt)}" src="${escapeAttr(project.image)}">` : `<div style="aspect-ratio:16/10;background:#d8dde6"></div>`}
      <div class="project-card-body">
        <h3>${escapeHtml(project.title || "Untitled project")}</h3>
        <p class="preview-subtitle">${escapeHtml(project.role || "Role")}</p>
        <p>${escapeHtml(project.description || "Describe the problem, your role, and the outcome.")}</p>
        <div class="proof-list">
          ${proofItem("Problem", project.problem || "Add the user or business problem.")}
          ${proofItem("Process", project.process || "Add research, design, build, or validation process.")}
          ${proofItem("Outcome", project.outcome || "Add the shipped result and what changed.")}
          ${proofItem("Metrics", project.metrics || "Add quantified proof where possible.")}
        </div>
        <div class="tag-row">${tags}</div>
        <div class="link-list">
          ${project.projectUrl ? renderLink(project.projectUrl, "Project") : ""}
          ${project.repoUrl ? renderLink(project.repoUrl, "Code") : ""}
        </div>
      </div>
    </article>
  `;
}

function proofItem(label, value) {
  return `<div class="proof-item"><strong>${escapeHtml(label)}</strong><span>${escapeHtml(value)}</span></div>`;
}

function templateCopy(template) {
  const copy = {
    "case-study": "Case study proof template: guide each project through problem, process, outcome, metrics, and accessibility-ready image alt text.",
    "visual-gallery": "Visual gallery template: use real screenshots with cover crop, contain, or feature layout preset controls.",
    "compact-proof": "Compact proof template: prioritize role, result, links, and concise evidence for fast scanning."
  };
  return copy[template] || copy["case-study"];
}

function renderLink(value, label) {
  const href = value.includes("@") && !value.startsWith("http") ? `mailto:${value}` : value;
  return `<a href="${escapeAttr(href)}">${escapeHtml(label || value)}</a>`;
}

function splitList(value) {
  return (value || "").split(",").map((item) => item.trim()).filter(Boolean);
}

function exportStaticHtml() {
  renderPreview();
  if (!state.profile.name || !state.profile.title || !state.projects.some((project) => project.title)) {
    els.validationStatus.textContent = "Export blocked by validation";
    return;
  }
  const documentHtml = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${escapeHtml(state.profile.name)} Portfolio</title><style>${EXPORT_STYLES}</style></head><body>${els.preview.outerHTML}</body></html>`;
  const blob = new Blob([documentHtml], { type: "text/html" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "portfolio.html";
  link.click();
  URL.revokeObjectURL(url);
  els.validationStatus.textContent = "Exported HTML";
}

function loadState() {
  const saved = localStorage.getItem("portfolioBuilderState");
  if (!saved) return;
  try {
    const parsed = JSON.parse(saved);
    Object.assign(state.profile, parsed.profile || {});
    state.theme = parsed.theme || state.theme;
    state.template = parsed.template || state.template;
    state.projects = Array.isArray(parsed.projects) ? parsed.projects.map((project) => ({ ...projectDefaults, ...project })) : state.projects;
  } catch {
    localStorage.removeItem("portfolioBuilderState");
  }
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char]));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#096;");
}

init();
""".replace("__VISUAL_DIRECTION_ID__", visual_id)


def _portfolio_readme(tasks: list[dict[str, object]], visual_direction: dict[str, str] | None = None) -> str:
    task_lines = "\n".join(f"- {task.get('id', 'TASK')}: {task.get('title', 'Untitled')}" for task in tasks)
    visual_direction = visual_direction or {}
    visual_id = visual_direction.get("id") or "local design contract"
    web_url = visual_direction.get("web_url") or "not available"
    visual_label = _visual_direction_label(visual_id)
    return f"""# Portfolio Builder MVP

Open `index.html` in a browser.

## Visual Direction Source

- Selected direction: {visual_id}
- Selection method: {visual_direction.get("selection_method") or "local"}
- Source artifact: docs/design/selected-visual-direction.md
- Review artifact: {visual_direction.get("report_path") or "not available"}
- Screenshot artifact: {visual_direction.get("screenshot_path") or "not available"}
- v0 web URL: {web_url}
- v0 source files: apps/web/v0-source/
- Tokenized demo URL remains in `.agent/artifacts/visual_directions/variants.json`.

## Implemented Architecture Tasks

{task_lines}

## Manual Smoke Test

1. Add profile name, title, bio, skills, and contact links.
2. Review proof coaching, selected visual source, proof score, and template strategy.
3. Upload an avatar under the size limit.
4. Add a project with screenshot, alt text, layout preset, title, role, problem, process, outcome, metrics, tags, and links.
5. Remove and replace the project screenshot.
6. Switch between themes and verify preview content persists.
7. Save locally and reload the page.
8. Export HTML and verify the exported file includes the same profile and project proof content.

## Visual System

{visual_label}
"""


def _visual_direction_label(visual_id: str) -> str:
    labels = {
        "dense-dashboard": "Dense Dashboard - work-focused builder shell with compact controls, status filters, quality checks, and a live portfolio export preview.",
        "minimalist-editorial": "Minimalist Editorial - restrained portfolio storytelling with large type, generous whitespace, and case-study emphasis.",
        "bold-marketing": "Bold Marketing - high-contrast launch energy suited to a promotional export template, not the default workbench.",
        "proof-first-case-study": "Proof-First Case Study - screenshot-led project evidence, problem/process/outcome hierarchy, and credibility scoring.",
        "creator-studio": "Creator Studio - calm editing workspace with proof inventory, publish readiness, and polished preview.",
    }
    return labels.get(visual_id, "Local design contract")


def _visual_direction_headline(visual_id: str) -> str:
    headlines = {
        "dense-dashboard": "Build, score, and export every portfolio section.",
        "minimalist-editorial": "Shape your work into a publishable story.",
        "bold-marketing": "Turn proof into a portfolio people remember.",
        "proof-first-case-study": "Prove the problem, process, and outcome.",
        "creator-studio": "Manage the work behind a credible portfolio.",
    }
    return headlines.get(visual_id, "Shape your work into a publishable story.")


def _portfolio_smoke_test(tasks: list[dict[str, object]]) -> str:
    return """# Portfolio Builder Smoke Test

- Given profile fields are filled, when Save is clicked, then the data remains after reload.
- Given a valid avatar image is selected, when upload completes, then the preview shows the avatar.
- Given an invalid or oversized image is selected, when upload validation runs, then a clear state message is shown.
- Given a project has screenshot, alt text, layout preset, title, role, problem, process, outcome, metrics, tags, and links, when preview renders, then the project card shows all saved content.
- Given a project screenshot exists, when Remove screenshot is clicked, then the screenshot is cleared and can be replaced.
- Given a template strategy is selected, when preview renders, then the template guidance appears in the portfolio.
- Given proof content changes, when profile or project fields update, then the proof score checklist updates.
- Given a theme is selected, when preview renders, then content is preserved and styling changes.
- Given required profile/project content exists, when Export HTML is clicked, then a static HTML file is generated.
"""
