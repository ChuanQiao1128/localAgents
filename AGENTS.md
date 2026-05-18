下面这份计划按 **“先做一个能真正开发小项目的本地 MVP，再逐步接近 Claude Team 那种多 agent 协作体验”** 来设计。核心原则是：**先把流程、状态、权限、任务板、测试门禁做扎实，再增加 agent 数量和自动化程度。**

------

# **当前最高优先级：把 Local Agent Studio 升级为 Agent Factory**

## **0.1 当前定位**

Local Agent Studio 不再被视为 Agent Factory 之外的另一个项目。它就是 Agent Factory 的 **本地 cockpit + runtime**。

项目当前目标定义为：

```text
Local Agent Studio is a local-first Agent Factory runtime for turning
product ideas and change requests into reviewed, test-verified code changes.
```

中文定位：

```text
Local Agent Studio 是一个本地优先的 Agent Factory 运行环境，
用来把产品想法和变更请求转化为经过 review、测试验证的代码变更。
```

## **0.1.1 和 Codex / AGENTS.md / requirements.md 的关系**

Codex + `AGENTS.md` + `requirements.md` 已经能完成很多一次性开发任务。Local Agent Studio 的价值不是“再包一层 Codex UI”，而是把这种手工 agent workflow 固化为一个可重复、可审计、可评估的本地开发工厂。

必须长期保持这个产品边界：

```text
Codex is the execution backend.
Local Agent Studio is the factory control system.
```

中文：

```text
Codex 是执行器。
Local Agent Studio 是工厂控制系统。
```

Codex / Claude Code / patch-worker 负责：

```text
读代码
改代码
跑命令
生成 patch
解释局部实现
```

Studio 负责：

```text
需求生命周期
requirements / design / tasks 的版本和审批
workflow state machine
run manager
candidate patch 可解释性
build / typecheck / test gate
repair loop
delivery report
product review
eval metrics
可选 GitHub PR handoff
```

因此后续开发不要重复实现 Codex 已经做好的代码生成能力。优先补 Studio 独有的系统层：

```text
1. Artifact lifecycle: requirements/design/tasks/report 都有版本、hash、状态。
2. Runtime enforcement: 未审批不能进入 implementation，不只靠 prompt obedience。
3. Run observability: 每个 run 的阶段、命令、失败、repair 都可见。
4. Verification gates: build/typecheck/test 不通过不能 delivered。
5. Feedback loop: product review 能生成下一轮 scoped Change Request。
6. Evaluation: 每个 run 产生 metrics，失败能分类和复盘。
```

这意味着后续开发不要另起一个 “Agent Factory” 项目，而是在现有 Local Agent Studio 上收敛、标准化、模块化、产品化。

当前产品演进目标：

```text
从：
  能跑 agentic coding loop 的本地 dogfood 工具

升级为：
  有标准 spec pipeline、有审批 gate、有运行可解释性、
  有 CI repair、有 GitHub PR 集成、有 eval 的 Agent Factory。
```

## **0.2 Agent Factory 的核心原则**

Agent Factory 不追求“用户一句话后无监督写代码、merge、部署”。正确目标是：

```text
Autonomous implementation,
human-gated planning and merging.
```

中文：

```text
实现阶段高自治，
规划和合并阶段人工把关。
```

因此任何新增自动化都必须遵守：

```text
1. agent 可以自动生成阶段产物；
2. 用户必须能 review / 修改 / approve 关键产物；
3. implementation 只能基于已 approve 的 requirements / design / tasks 启动；
4. build/typecheck/test/review gate 失败不能伪装成 delivered；
5. 高风险产品方向、安全边界、合规文案必须进入 human approval；
6. 不允许无确认自动 merge / deploy / push production。
```

## **0.3 目标工作流**

最终标准 workflow：

```text
Idea / Change Request
  ↓
Clarify
  ↓
requirements.md
  ↓
Approve requirements
  ↓
design.md
  ↓
Approve design
  ↓
tasks.md
  ↓
Approve tasks
  ↓
Implementation run
  ↓
Verify
  ↓
Review queue
  ↓
Delivery report
  ↓
Optional GitHub PR
```

后续每个 feature / change request 都应该能回答：

```text
这次需求是什么？
谁确认了？
设计是什么？
谁确认了？
任务是什么？
谁确认了？
agent 正在做哪个 task？
它为什么这样改？
它跑了哪些验证？
失败时怎么修？
最后交付依据是什么？
```

## **0.4 AF-1：Spec Pipeline Hardening**

当前第一优先级是 AF-1，不是 GitHub PR、SaaS、多模型接入或更多 UI polish。

AF-1 目标：

```text
让每个 agent run 都基于 approved requirements、approved design、approved tasks，
并且所有 implementation、repair、delivery 都能追溯回这些已确认产物。
```

每个 project / feature / change request 应该有正式 spec lifecycle：

```text
runtime-project/
  specs/
    <feature-or-cr-id>/
      requirements.md
      design.md
      tasks.md
      approvals.json
      status.json
      design-issues.md
      versions/
        requirements.v1.md
        requirements.v2.md
        design.v1.md
        tasks.v1.md
      runs/
        run-001/
          plan.md
          patch.diff
          commands.log
          verification.md
          delivery-report.md
```

Approval 必须绑定 artifact 的版本和内容 hash，不能只记录“用户点过 approve”：

```json
{
  "artifact": "requirements.md",
  "version": 3,
  "sha256": "abc123...",
  "approved_by": "user",
  "approved_at": "2026-05-18T10:30:00Z"
}
```

如果 `requirements.md`、`design.md` 或 `tasks.md` 在 approval 后被修改，对应 approval 必须自动失效。

AF-1 最小验收标准：

```text
1. 可以从一个 idea 创建 spec workspace。
2. agent 可以生成 requirements.md。
3. 用户可以手动编辑 requirements.md。
4. 用户可以 approve requirements。
5. requirements 改动后 approval 自动失效。
6. agent 只能基于 approved requirements 生成 design.md。
7. 用户可以 approve design。
8. agent 只能基于 approved design 生成 tasks.md。
9. 用户可以 approve tasks。
10. implementation 只能在 tasks approved 后启动。
11. implementation 不能直接修改 requirements/design/tasks。
12. 如果 implementation 发现设计问题，只能写入 design-issues.md 并暂停。
13. delivery report 必须引用 requirements/design/tasks 的 approved version/hash。
14. UI 必须清楚显示当前 spec 状态：draft / changed_since_approval / approved / blocked。
15. CLI 和 Studio Console 对同一 spec 状态的判断必须一致。
```

AF-1 状态机：

```text
IDEA
  ↓
CLARIFYING
  ↓
REQUIREMENTS_DRAFTED
  ↓
REQUIREMENTS_APPROVED
  ↓
DESIGN_DRAFTED
  ↓
DESIGN_APPROVED
  ↓
TASKS_DRAFTED
  ↓
TASKS_APPROVED
  ↓
IMPLEMENTING
  ↓
VERIFYING
  ↓
REVIEW_READY
  ↓
DELIVERED
```

异常状态：

```text
BLOCKED_NEEDS_CLARIFICATION
BLOCKED_DESIGN_ISSUE
BUILD_FAILED
TYPECHECK_FAILED
REPAIRING
STOPPED_BY_USER
FAILED
```

## **0.5 AF-2：Implementation Run Manager**

AF-2 目标是让 implementation 从黑盒变成可解释 run。

每个 implementation run 必须持续写事件：

```json
{
  "run_id": "run_001",
  "phase": "implementation",
  "event_type": "command_completed",
  "message": "npm run typecheck completed successfully",
  "timestamp": "2026-05-18T11:02:00Z",
  "metadata": {
    "exit_code": 0,
    "duration_ms": 18342
  }
}
```

UI 应显示：

```text
当前 run id
当前 task id
当前阶段：planning / editing / verifying / repairing / reviewing
候选 patch 数量
每个 candidate 的摘要
为什么选择这个 candidate
跑了哪些命令
命令结果是什么
失败原因是什么
repair 尝试了几次
最终改了哪些文件
哪些测试通过
哪些风险还存在
```

AF-2 验收标准：

```text
1. 长任务必须有实时 event log。
2. Stop / Cancel 只能停止本 run 记录的 pid，不得杀错进程。
3. Retry 必须基于失败原因和同一 approved spec，不得 silently 改 spec。
4. Candidate patch、command trace、repair history、verification summary 必须可查看。
5. Run failed 时必须有 failure category，不允许只有“failed”。
```

## **0.6 AF-3：CI Repair Agent**

AF-3 先作为 Local Agent Studio 内部模块实现，稳定后再抽出公开 demo。

职责：

```text
读取 build/typecheck/test/CI log
  ↓
分类失败原因
  ↓
定位相关文件
  ↓
生成 repair patch
  ↓
重新跑验证命令
  ↓
输出 repair report
```

第一版 failure categories：

```text
build_failure
typecheck_failure
unit_test_failure
lint_failure
dependency_failure
runtime_exception
environment_error
spec_ambiguity
```

AF-3 验收标准：

```text
1. 本地 build/typecheck/test 失败能被分类。
2. repair patch 必须在 allowed paths 内。
3. repair 后必须重新跑原失败命令。
4. 连续同类失败超过阈值时必须暂停，生成 human review item。
5. repair report 必须说明失败原因、修改策略、验证结果、剩余风险。
```

## **0.7 AF-4：GitHub PR Integration**

GitHub PR 集成排在 AF-1 / AF-2 / AF-3 之后。

目标 workflow：

```text
GitHub issue / label trigger
  ↓
创建 feature workspace
  ↓
生成 requirements/design/tasks
  ↓
等待 approval
  ↓
创建 branch
  ↓
implementation agent 跑
  ↓
CI repair agent 处理失败
  ↓
开 PR
  ↓
PR body 附 delivery report
```

PR body 必须包含：

```text
## Summary
## Linked Spec
- requirements.md vN + hash
- design.md vN + hash
- tasks.md vN + hash
## Implementation
## Tests
## CI / Verification
## Risks
## Human Review Checklist
```

阶段性 GitHub 纪律：

```text
1. 每个 AF milestone 完成后必须形成一个清晰 git commit。
2. commit message 必须包含 milestone id，例如 `AF-1: add spec approval hashes`。
3. push 到 GitHub 前必须通过对应 validation。
4. 不允许把 `.env.local`、secret、真实 API key、私有日志提交到 GitHub。
5. 不允许自动 merge；PR 必须由用户最终 review。
6. 当前 Agent Factory 长跑已授权：每个 milestone 验证通过后可以自动 push stage branch/commit。
```

## **0.8 AF-5：Evaluation Dashboard**

AF-5 目标是把 Agent Factory 从 demo 变成可评估系统。

每次 run 必须写 metrics：

```json
{
  "run_id": "run_001",
  "feature_id": "forgot-password",
  "phase": "implementation",
  "status": "delivered",
  "build_passed": true,
  "typecheck_passed": true,
  "tests_passed": true,
  "repair_attempts": 2,
  "files_changed": 5,
  "human_approvals_required": 3,
  "spec_drift_detected": false,
  "duration_seconds": 840
}
```

Dashboard 应展示：

```text
成功率
失败类型分布
平均 repair 次数
平均交付时间
spec approval 到 implementation 的转化率
最常见失败原因
```

## **0.9 当前开发路线**

后续开发顺序固定为：

```text
AF-1: Spec Pipeline Hardening
AF-2: Implementation Run Manager
AF-3: CI Repair Agent
AF-4: GitHub PR Integration
AF-5: Evaluation Dashboard
```

不要被以下方向分散：

```text
马上做 SaaS
马上做复杂多 agent 并行
马上接更多模型
马上做 GitHub PR
马上公开完整源码
马上做生产部署
```

当前第一优先级只有一个：

```text
把 Local Agent Studio 的开发生命周期标准化。
```

长跑执行材料：

```text
requirements.md
  Agent Factory 最终产品需求合同。定义为什么做、最终形态、功能需求、
  人工 gate、自动执行边界、验收标准和非目标。

docs/AGENT_FACTORY_LONG_RUN_PLAN.md
  AF-1 到 AF-5/AF-6 的长跑 runbook、commit 纪律、stop conditions。

docs/AF-1_SPEC_PIPELINE_PLAN.md
  AF-1 的数据模型、状态机、子阶段、验收标准。

docs/agent-factory/change-requests/AF-1A_SPEC_APPROVAL_HASHES.md
  长跑第一步的可执行 change request。
```

后续 Codex 长跑必须先读取 `requirements.md` 和 `AGENTS.md`，再从 `AF-1A_SPEC_APPROVAL_HASHES.md` 开始。不要直接尝试一次性实现 AF-1 到 AF-5。

## **0.10 安全与仓库纪律**

本项目是 local-first / private-first 开发工具。任何 agent 或人工修改都必须遵守：

```text
1. 不读取、不打印、不修改、不删除 `.env.local`。
2. 不提交 secret、API key、private token、真实用户数据。
3. 不自动 deploy。
4. 不自动 merge。
5. 当前 Agent Factory 长跑可以在 milestone 验证通过后自动 git push；其他场景仍需用户确认。
6. 不删除已有 dogfood evidence，除非用户明确要求。
7. 对已有脏工作区必须先识别，不得覆盖用户或其他 agent 的改动。
8. 每个阶段完成后必须输出：files changed、validation、known limitations、next step。
```

公开开源策略：

```text
可以公开：
  ci-repair-agent-demo
  bugfix-agent-bench
  toy sample app
  seeded failures
  简化 prompts
  架构图
  eval metrics 格式
  delivery report 格式
  safety model

暂时不要公开：
  Local Agent Studio 全部源码
  真实 agent prompts
  真实 dogfood logs
  真实项目数据
  真实 run traces
  tokens / credentials
  私有 eval 集
  高价值 orchestration 细节
```

------

# **一、项目定位**

项目暂定名：**Local Agent Dev Studio**

目标：

在你自己的电脑上运行一个多 agents 软件开发工作台。它能根据你的需求，让 PM agent 先调研，UI agent 做设计参考和界面规范，架构 agent 设计系统方案，开发 agent 写代码，QA agent 跑测试，Reviewer agent 审查 diff，最后由 Lead agent 统一协调和合并。

这个项目不以上线 SaaS 为目标，而是本地运行：

```text
localhost Dashboard
本地 SQLite 数据库
本地项目目录
本地 Git / worktree
本地 Docker sandbox
本地日志和产物
外部 API 可选：OpenAI / Claude / Tavily / Firecrawl / v0 / Figma
```

我建议参考但不照搬现成框架。Claude Code Agent Teams 的关键设计是 lead session 协调多个独立 Claude Code 实例，通过共享任务、agent 消息和集中管理来协作；官方也说明它适合研究、review、新模块、跨前后端和测试的并行任务，但有协调成本、token 成本和实验性限制。这个方向适合做你的目标架构参考。 

------

# **二、总体技术方案**

## **2.1 推荐技术栈**

第一版建议用：

```text
Orchestrator:
  Python 3.12 + FastAPI + Typer CLI

本地数据库:
  SQLite + SQLModel / SQLAlchemy

Dashboard:
  Next.js + React + Tailwind + shadcn/ui

Agent 调用:
  LiteLLM 或自写 Model Adapter
  支持 OpenAI / Anthropic / OpenRouter / Ollama

Coding Worker:
  第一版先自写 lightweight worker
  第二版接 Claude Code CLI / OpenHands CLI / Aider / Codex CLI

Sandbox:
  Docker
  Git worktree per task / per agent

Research:
  Tavily / Firecrawl / Playwright screenshot

UI 生成:
  v0 API 第一优先
  Figma MCP 第二阶段接入

测试:
  pytest / vitest / eslint / tsc / playwright
```

OpenHands 可以作为 coding worker 的参考或插件，因为它本身提供 SDK 和 CLI，可以定义 agents 并在本地运行；OpenHands 文档也支持本地 GUI、Docker-based setup，以及在 sandbox 中挂载本地 repo。 

## **2.2 架构图**

```text
┌────────────────────────────────────────────────────────────┐
│                    Local Dashboard                         │
│  项目列表 / 任务板 / Agent 状态 / Diff / 测试结果 / 审批      │
└───────────────────────┬────────────────────────────────────┘
                        │
                        v
┌────────────────────────────────────────────────────────────┐
│                 Orchestrator API / CLI                     │
│  workflow engine / task DAG / agent registry / permissions │
└───────┬───────────────┬──────────────────┬────────────────┘
        │               │                  │
        v               v                  v
┌─────────────┐ ┌────────────────┐ ┌───────────────────────┐
│ SQLite DB   │ │ Agent Runtime   │ │ Tool Router / MCP      │
│ tasks/runs  │ │ PM UX Dev QA    │ │ search/git/shell/v0    │
└─────────────┘ └───────┬────────┘ └───────────┬───────────┘
                        │                      │
                        v                      v
              ┌────────────────┐      ┌────────────────────┐
              │ Model Router    │      │ Docker Sandbox      │
              │ LLM providers   │      │ Git worktree        │
              └────────────────┘      └────────────────────┘
```

核心技术判断：**大流程用代码控制，小任务让 LLM agent 自主完成。** OpenAI Agents SDK 文档把多 agent 编排分成两类：由 LLM 决策，或者由代码决定 agent 的执行顺序；这两种可以混合。你的项目非常适合“workflow 由代码控制，具体研究/设计/编码由 agent 完成”。 

------

# **三、产品 MVP 边界**

第一版不要做成完整“AI 软件公司”。MVP 应该只支持一个典型场景：

用户输入一个软件想法，系统完成：调研 → PRD → UI 方案 → 架构方案 → 任务拆分 → 生成代码 → 跑测试 → 输出报告。

第一版支持 Web app 项目即可，例如：

```text
Next.js frontend
FastAPI backend
SQLite/Postgres
Playwright e2e
Docker compose
```

暂时不做：

```text
移动端原生 app
多人协作
云端部署
复杂权限系统
自动购买域名/部署
企业级安全合规
大型 monorepo 智能重构
```

------

# **四、Agent 角色设计**

## **4.1 第一版保留 7 个核心 agents**

```text
Lead Agent
Product Manager Agent
UI Designer Agent
Architect Agent
Full-stack Developer Agent
QA Agent
Reviewer Agent
```

后续再拆：

```text
Frontend Agent
Backend Agent
Mobile Agent
Security Agent
Performance Agent
DevOps Agent
Docs Agent
Data Agent
```

Claude Code subagents 的设计值得借鉴：每个 subagent 有独立上下文、自定义系统提示词、工具权限和独立权限，适合处理会污染主上下文的研究、日志、代码搜索等任务。 

## **4.2 各 agent 职责**

### **Lead Agent**

职责：

```text
理解用户目标
选择 workflow
创建任务 DAG
分配任务
检查阶段产物
决定是否进入下一阶段
处理失败重试
汇总最终报告
```

不能做：

```text
直接大规模写代码
绕过 QA 合并
绕过用户审批
```

### **Product Manager Agent**

职责：

```text
需求澄清
竞品/参考产品调研
功能范围设计
用户故事
验收标准
MVP / V1 / V2 拆分
```

可用工具：

```text
web_search
firecrawl_scrape
browser_screenshot
read_project_docs
write_product_docs
```

产物：

```text
docs/product/research.md
docs/product/prd.md
docs/product/user-stories.md
docs/product/acceptance-criteria.md
docs/product/scope.md
```

PM 调研建议接 Tavily 或 Firecrawl。Tavily 官方定位是给 AI agents 提供实时搜索、提取、研究和网页抓取；Firecrawl 可以把网页转成 markdown、JSON、screenshot 或 HTML，适合给 LLM 做调研输入。 

### **UI Designer Agent**

职责：

```text
分析目标用户和场景
找 UI/UX 参考
输出用户流
定义信息架构
定义设计系统
生成组件规范
可调用 v0 生成 UI 草稿
```

产物：

```text
docs/design/ui-references.md
docs/design/user-flow.md
docs/design/design-system.md
docs/design/component-spec.md
docs/design/wireframes.md
```

UI 生成第一版可以接 v0 API，因为 v0 Platform API 提供 text-to-app 的接口，可以从 prompt 生成项目、代码文件和 demo；它也支持把自己的文件、源码、git 或 shadcn registry 作为上下文。 

Figma MCP 放到第二阶段。Figma MCP 可以把 Figma 设计上下文、变量、组件、布局数据接入 AI coding agent，并支持从选中的 frame 生成代码或把内容写回 canvas。 

### **Architect Agent**

职责：

```text
选择技术栈
设计目录结构
设计数据模型
设计 API contract
设计鉴权/权限
拆分开发任务
定义测试策略
```

产物：

```text
docs/architecture/architecture.md
docs/architecture/api.openapi.yaml
docs/architecture/database-schema.md
docs/architecture/adr/*.md
.agent/tasks/generated-tasks.json
```

### **Full-stack Developer Agent**

职责：

```text
根据任务写代码
只修改 allowed_paths
写单元测试
跑 lint/typecheck/test
提交 worktree diff
```

第一版先用 full-stack developer，一个 agent 写前后端；第二版再拆 frontend/backend。

### **QA Agent**

职责：

```text
读 PRD 和验收标准
制定测试计划
跑自动化测试
补充 e2e 测试
记录 bug
复现失败
给开发 agent 创建 fix task
```

产物：

```text
docs/qa/test-plan.md
docs/qa/test-results.md
docs/qa/bugs.md
```

### **Reviewer Agent**

职责：

```text
审查 diff
检查是否满足 PRD
检查是否过度实现
检查安全风险
检查测试覆盖
决定 approve / request changes
```

产物：

```text
docs/review/review-report.md
```

------

# **五、工作流设计**

## **5.1 默认软件开发 workflow**

```text
0. Intake
   用户输入项目想法

1. Product Research
   PM 调研参考产品、竞品、设计趋势、功能模式

2. PRD Lock
   PM 输出 PRD、scope、用户故事、验收标准

3. UX/UI Design
   UI agent 输出用户流、设计系统、组件规范
   可选调用 v0 生成 UI 草稿

4. Architecture
   Architect 输出架构、API、DB schema、任务 DAG

5. Implementation
   Developer agent 按任务写代码

6. QA
   QA agent 跑测试、补 e2e、记录 bug

7. Review
   Reviewer agent 审查 diff

8. Merge
   Lead agent 合并 worktree，生成最终报告
```

MetaGPT 和 BMAD 都说明了一件事：多 agent 软件开发不能只是“多个 agent 聊天”，而要靠 SOP、角色、产物和交接流程来降低混乱。MetaGPT 的 GitHub 说明它从一句需求输出 user stories、竞品分析、需求、数据结构、API 和文档，并包含 PM、架构师、项目经理、工程师等角色；BMAD 也强调结构化 AI-driven agile workflow。 

## **5.2 workflow 状态机**

每个阶段有固定状态：

```text
pending
running
blocked
needs_approval
failed
completed
skipped
```

每个阶段有固定 gate：

```text
research_gate
prd_gate
design_gate
architecture_gate
implementation_gate
qa_gate
review_gate
merge_gate
```

建议强制以下 gate：

```text
PRD 没有完成，不允许设计
设计没有完成，不允许架构
架构没有完成，不允许开发
测试没有通过，不允许合并
Reviewer 没 approve，不允许最终完成
```

------

# **六、任务系统设计**

## **6.1 Task 数据结构**

```json
{
  "id": "FEAT-001",
  "project_id": "project_abc",
  "run_id": "run_001",
  "title": "Build authentication flow",
  "description": "Implement login, register, logout and session persistence.",
  "owner": "developer",
  "status": "pending",
  "phase": "implementation",
  "depends_on": ["ARCH-002", "UX-003"],
  "priority": "high",
  "allowed_paths": [
    "apps/web/**",
    "apps/api/**",
    "packages/db/**"
  ],
  "inputs": [
    "docs/product/prd.md",
    "docs/design/component-spec.md",
    "docs/architecture/api.openapi.yaml"
  ],
  "outputs": [
    "apps/web/app/login/page.tsx",
    "apps/api/auth.py",
    "tests/auth.spec.ts"
  ],
  "acceptance_criteria": [
    "User can register with email/password",
    "User can log in and log out",
    "Invalid password shows clear error",
    "E2E test passes"
  ],
  "test_commands": [
    "npm run typecheck",
    "npm run lint",
    "npm run test",
    "npx playwright test auth"
  ]
}
```

## **6.2 Task Board 表**

SQLite 表建议：

```text
projects
runs
agents
tasks
task_dependencies
messages
tool_calls
artifacts
approvals
file_locks
worktrees
test_results
reviews
memories
costs
settings
```

## **6.3 file lock 规则**

每个 task 开始前，系统根据 `allowed_paths` 创建 file/path lock：

```text
.lock/apps-web-auth.json
.lock/apps-api-auth.json
```

同一时间不能有两个 agent 修改同一 path pattern。

规则：

```text
docs/product/**        PM only
docs/design/**         UI only
docs/architecture/**   Architect only
apps/web/**            Frontend / Developer
apps/api/**            Backend / Developer
tests/**               Developer / QA
.agent/**              Lead only
```

Claude Agent Teams 官方也强调并行 agent 最适合相互独立的任务；如果任务是顺序依赖、同文件编辑或依赖很多，单 session 或 subagent 更合适。 

------

# **七、权限系统设计**

你的本地工具一定要有权限控制。不要一开始就让 agent 随便 `rm -rf`、安装包、读 home 目录或上传文件。

Claude Code 的权限文档把工具权限分成读文件、bash 命令、文件修改等类别，并支持 allow、ask、deny 规则；它也提醒 bypass 权限只适合容器或 VM 这类隔离环境。这个思路可以直接借鉴。 

## **7.1 权限等级**

```text
L0 Read-only
  read_file
  grep
  list_dir
  git_status
  read_docs

L1 Safe write
  write docs/*
  create artifacts
  update task notes

L2 Project write
  edit allowed_paths
  create tests
  run package scripts

L3 Risky
  install package
  modify lockfile
  run migration
  delete files
  network request
  change env files

L4 Forbidden by default
  read ~/.ssh
  read browser profile
  read password files
  sudo
  docker socket direct access
  upload repo
  write outside workspace
```

## **7.2 审批规则**

```text
自动允许:
  read_file
  grep
  git diff
  npm test
  npm run typecheck

需要确认:
  npm install
  pnpm add
  database migration
  docker compose up
  deleting files
  editing .env
  editing package.json

默认禁止:
  sudo
  rm -rf /
  rm -rf ~
  cat ~/.ssh/*
  curl upload endpoints
```

------

# **八、目录结构**

建议项目自身目录：

```text
local-agent-dev-studio/
  README.md
  pyproject.toml
  package.json

  apps/
    dashboard/
      app/
      components/
      lib/
      package.json

  orchestrator/
    main.py
    cli.py
    config.py

    core/
      workflow_engine.py
      task_store.py
      agent_registry.py
      run_manager.py
      permission_engine.py
      artifact_store.py
      event_bus.py
      cost_tracker.py

    agents/
      base.py
      lead.py
      product_manager.py
      ui_designer.py
      architect.py
      developer.py
      qa.py
      reviewer.py

    tools/
      file_tools.py
      git_tools.py
      shell_tools.py
      browser_tools.py
      search_tools.py
      firecrawl_tools.py
      v0_tools.py
      figma_tools.py
      test_tools.py

    model/
      router.py
      openai_adapter.py
      anthropic_adapter.py
      ollama_adapter.py
      openrouter_adapter.py

    sandbox/
      docker_runner.py
      worktree_manager.py
      path_locker.py

    db/
      models.py
      migrations/

  agents/
    lead.yaml
    product_manager.yaml
    ui_designer.yaml
    architect.yaml
    developer.yaml
    qa.yaml
    reviewer.yaml

  workflows/
    software_project.yaml
    bugfix.yaml
    refactor.yaml
    ui_redesign.yaml

  templates/
    project_scaffold/
    docs/
    prompts/
    reports/

  tests/
    unit/
    integration/
    e2e/

  examples/
    todo-app/
    expense-tracker/
```

用户项目目录：

```text
~/AgentStudioProjects/
  my-expense-app/
    .agent/
      project.yaml
      runs/
      artifacts/
      tasks/
      memory/
      locks/
    docs/
      product/
      design/
      architecture/
      qa/
      review/
    apps/
      web/
      api/
    packages/
    tests/
```

------

# **九、配置文件设计**

## **9.1 Agent 配置**

示例：`agents/product_manager.yaml`

```yaml
id: product_manager
name: Product Manager
model: claude-sonnet
temperature: 0.3

role: >
  You are a senior product manager. You research the market,
  analyze references, define MVP scope, write PRDs, and produce
  acceptance criteria.

tools:
  - web_search
  - firecrawl_scrape
  - browser_screenshot
  - read_file
  - write_file

permissions:
  read:
    - "**/*"
  write:
    - "docs/product/**"
    - ".agent/artifacts/research/**"
  deny:
    - "apps/**"
    - ".env"
    - "~/**"

required_outputs:
  - "docs/product/research.md"
  - "docs/product/prd.md"
  - "docs/product/acceptance-criteria.md"

quality_rules:
  - "Research claims must include sources."
  - "PRD must separate MVP, V1, and future ideas."
  - "Acceptance criteria must be testable."
```

示例：`agents/developer.yaml`

```yaml
id: developer
name: Full-stack Developer
model: gpt-5.5-pro
temperature: 0.2

tools:
  - read_file
  - edit_file
  - grep
  - run_shell
  - git_diff
  - git_status

permissions:
  read:
    - "**/*"
  write:
    - "apps/**"
    - "packages/**"
    - "tests/**"
  ask:
    - "package.json"
    - "pnpm-lock.yaml"
    - "docker-compose.yml"
    - ".env.example"
  deny:
    - ".env"
    - "~/**"

required_checks:
  - "npm run lint"
  - "npm run typecheck"
  - "npm run test"
```

## **9.2 Workflow 配置**

```
workflows/software_project.yaml
id: software_project
name: Software Project Workflow

phases:
  - id: intake
    owner: lead
    output:
      - ".agent/project-brief.md"

  - id: research
    owner: product_manager
    depends_on: [intake]
    output:
      - "docs/product/research.md"

  - id: prd
    owner: product_manager
    depends_on: [research]
    output:
      - "docs/product/prd.md"
      - "docs/product/acceptance-criteria.md"
    gate: prd_approval

  - id: design
    owner: ui_designer
    depends_on: [prd]
    output:
      - "docs/design/user-flow.md"
      - "docs/design/design-system.md"
      - "docs/design/component-spec.md"

  - id: architecture
    owner: architect
    depends_on: [design]
    output:
      - "docs/architecture/architecture.md"
      - "docs/architecture/api.openapi.yaml"
      - "docs/architecture/database-schema.md"
      - ".agent/tasks/generated-tasks.json"

  - id: implementation
    owner: developer
    depends_on: [architecture]
    parallelizable: true

  - id: qa
    owner: qa
    depends_on: [implementation]
    output:
      - "docs/qa/test-results.md"

  - id: review
    owner: reviewer
    depends_on: [qa]
    output:
      - "docs/review/review-report.md"

  - id: merge
    owner: lead
    depends_on: [review]
```

------

# **十、CLI 设计**

第一版先做 CLI，比 Dashboard 更快。

```bash
agent-studio init
agent-studio new "做一个个人记账 web app"
agent-studio status
agent-studio run research
agent-studio run prd
agent-studio run design
agent-studio run architecture
agent-studio run implementation
agent-studio run qa
agent-studio run review
agent-studio approve prd
agent-studio reject design
agent-studio logs run_001
agent-studio diff task_012
agent-studio open-dashboard
```

MVP 关键命令：

```bash
agent-studio new
agent-studio run
agent-studio status
agent-studio approve
agent-studio diff
agent-studio logs
```

Dashboard 第二阶段做。

------

# **十一、Dashboard 页面设计**

Dashboard 本地跑在：

```text
http://localhost:5173
或
http://localhost:3000
```

页面：

```text
1. Projects
   项目列表、创建项目、最近运行

2. Project Overview
   当前目标、技术栈、阶段进度、最新结果

3. Task Board
   Kanban: pending / running / blocked / review / done

4. Run Timeline
   agent 调用、工具调用、错误、审批点

5. Artifacts
   PRD、设计文档、架构文档、测试报告

6. Diff Viewer
   每个 task 的代码变更

7. Test Results
   lint/typecheck/unit/e2e 结果

8. Agent Settings
   模型、工具、权限、预算

9. Memory
   用户偏好、项目决策、技术栈偏好

10. Approvals
   等待你确认的高风险操作
```

Dashboard 不应该是聊天窗口为主，而应该是 **任务板 + 产物 + diff + 测试结果** 为主。聊天只是补充。

------

# **十二、开发里程碑**

下面按 8 个阶段做。每个阶段都应该能运行、能测试、能验收。

------

## **Phase 0：项目骨架和技术验证**

目标：确认最小架构能跑起来。

任务：

```text
建立 monorepo
建立 Python orchestrator
建立 Next.js dashboard 空壳
建立 SQLite DB
建立 agent 配置加载
建立 workflow 配置加载
建立基础日志系统
建立本地项目 workspace
```

交付物：

```text
agent-studio init 可运行
agent-studio new 可创建项目
SQLite 能记录 project/run/task
Dashboard 能显示项目列表
```

验收标准：

```text
可以创建一个项目
可以看到项目状态
可以读取 agents/*.yaml
可以读取 workflows/*.yaml
可以写入 .agent/runs/run_id/
```

建议目录：

```text
orchestrator/core
orchestrator/db
orchestrator/agents
apps/dashboard
agents
workflows
```

------

## **Phase 1：Workflow Engine + Task Board**

目标：把“阶段制流程”跑通，不接真实 LLM 也可以跑。

任务：

```text
实现 workflow state machine
实现 phase dependencies
实现 task DAG
实现 task status transition
实现 run event log
实现 artifact registry
实现 approval gate
```

核心状态转换：

```text
pending -> running -> completed
pending -> running -> failed
running -> needs_approval -> completed
running -> blocked
blocked -> running
```

数据库表：

```text
projects
runs
phases
tasks
task_dependencies
events
artifacts
approvals
```

验收标准：

```text
agent-studio run software_project 可以按阶段创建任务
phase 依赖正确
PRD gate 可以暂停
approve 后继续
失败可以 retry
所有事件写入 events 表
```

这里建议严格采用 deterministic workflow。Microsoft Agent Framework 文档也把 workflow 定义为预先定义的操作序列，适合复杂业务流程、多 agent、人类审批和外部系统集成；这类显式控制比让 LLM 随机决定下一步更稳定。 

------

## **Phase 2：Model Router + Agent Runtime**

目标：让 agent 真正能调用模型，并按 schema 输出结果。

任务：

```text
实现 model adapter
实现 prompt renderer
实现 structured output parser
实现 retry / timeout / cost tracking
实现 agent base class
实现 tool call interface
实现 context builder
```

第一批 model adapter：

```text
OpenAI adapter
Anthropic adapter
Ollama adapter
OpenRouter adapter 可选
```

Agent runtime 接口：

```python
class AgentRunner:
    async def run_task(
        self,
        agent_id: str,
        task_id: str,
        context: AgentContext,
    ) -> AgentResult:
        ...
```

AgentResult：

```json
{
  "status": "completed",
  "summary": "Generated PRD and acceptance criteria.",
  "artifacts": [
    "docs/product/prd.md"
  ],
  "tool_calls": [],
  "next_tasks": [],
  "requires_approval": false
}
```

验收标准：

```text
PM agent 可以读 project brief
PM agent 可以生成 docs/product/prd.md
系统能记录 token/cost/latency
agent 输出不符合 schema 时能自动修复一次
失败能重试
```

------

## **Phase 3：PM Research Agent**

目标：实现你最看重的“PM 先研究，再设计”。

任务：

```text
实现 web_search tool
实现 scrape tool
实现 source collector
实现 research note artifact
实现 citation/evidence 结构
实现竞品分析模板
实现 PRD 模板
实现 acceptance criteria 模板
```

Research 数据结构：

```json
{
  "query": "best personal finance app onboarding UX 2026",
  "sources": [
    {
      "title": "Source title",
      "url": "https://...",
      "publisher": "Example",
      "date": "2026-04-01",
      "summary": "...",
      "relevance": 0.86,
      "evidence_type": "competitor|design|technical|market"
    }
  ],
  "insights": [
    {
      "claim": "Most apps use quick categorization during transaction input.",
      "supporting_sources": [0, 2, 3],
      "confidence": "medium"
    }
  ]
}
```

PM 输出模板：

```text
1. 背景
2. 用户
3. 使用场景
4. 竞品/参考产品
5. 核心问题
6. MVP 范围
7. 非目标
8. 用户故事
9. 验收标准
10. 风险
11. 后续版本
```

验收标准：

```text
输入一个产品想法
系统自动生成搜索 query
能收集至少 5 个来源
能保存 research.md
能生成 PRD
PRD 中每个关键产品判断有来源或明确标注为假设
```

------

## **Phase 4：UI Designer Agent + v0 草稿**

目标：让 UI agent 不是凭空设计，而是基于 PM research 和 UI 参考生成设计系统。

任务：

```text
实现 UI reference collector
实现 screenshot 保存
实现 design-system.md 模板
实现 user-flow.md 模板
实现 component-spec.md 模板
实现 v0 API wrapper
实现 v0 生成结果导入 artifacts
```

UI 流程：

```text
读取 PRD
读取 research
生成 UI reference queries
抓取页面/截图
总结设计模式
生成 design system
生成 component spec
可选调用 v0
保存 v0 files 到 .agent/artifacts/v0/
```

v0 调用策略：

```text
不要让 v0 直接覆盖项目代码
v0 生成结果先进 artifacts
Reviewer 或 UI agent 整理后，Developer 再选择性合并
```

验收标准：

```text
能生成 user-flow.md
能生成 design-system.md
能生成 component-spec.md
能调用 v0 生成一个 React UI 草稿
v0 结果不会直接污染主代码
```

------

## **Phase 5：Architecture Agent + 任务拆分**

目标：把产品和设计变成工程计划。

任务：

```text
实现 architecture prompt
实现技术栈选择模板
实现 OpenAPI 输出
实现 DB schema 输出
实现 ADR 输出
实现 task decomposition
实现 task dependency graph
```

Architecture 输出：

```text
docs/architecture/architecture.md
docs/architecture/api.openapi.yaml
docs/architecture/database-schema.md
docs/architecture/adr/001-tech-stack.md
.agent/tasks/generated-tasks.json
```

任务拆分示例：

```text
SETUP-001 初始化 Next.js + API 项目
DB-001 创建用户和交易表
API-001 实现 auth API
API-002 实现 transaction CRUD
WEB-001 实现 landing page
WEB-002 实现 dashboard page
WEB-003 实现 transaction form
TEST-001 添加 auth e2e
TEST-002 添加 transaction e2e
```

验收标准：

```text
Architecture agent 能基于 PRD/design 输出工程方案
能生成 OpenAPI
能生成 DB schema
能生成可执行 task DAG
每个任务有 owner、allowed_paths、acceptance criteria、test_commands
```

------

## **Phase 6：Developer Agent + Git Worktree + Sandbox**

目标：让 agent 真正写代码，但在可控环境中写。

任务：

```text
实现 worktree_manager
实现 path permission check
实现 file edit tool
实现 shell command tool
实现 git diff tool
实现 package manager detection
实现 sandbox runner
实现 task-level branch
```

执行方式：

```text
主 repo:
  my-app/

worktree:
  .agent/worktrees/task-WEB-001/
  .agent/worktrees/task-API-001/
```

Developer agent 流程：

```text
读取 task
读取相关 docs
创建 worktree
修改 allowed_paths
运行必要测试
生成 diff summary
提交 task result
```

shell 工具必须限制：

```text
工作目录限制在 worktree
命令白名单/灰名单/黑名单
超时限制
输出截断
危险命令审批
```

验收标准：

```text
Developer agent 可以完成一个简单页面
不能修改 allowed_paths 外文件
能跑 npm run typecheck
能保存 git diff
失败时能记录错误
```

------

## **Phase 7：QA Agent + Reviewer Agent**

目标：建立质量门禁。

任务：

```text
实现 test command runner
实现 test result parser
实现 QA test plan
实现 bug report
实现 reviewer diff review
实现 request changes workflow
实现 fix task 自动创建
```

QA 流程：

```text
读取 acceptance criteria
读取 changed files
确定测试命令
运行 lint/typecheck/unit/e2e
分析失败
生成 bugs.md
如果失败，创建 fix tasks
```

Reviewer 流程：

```text
读取 PRD/design/architecture
读取 diff
检查过度实现
检查安全风险
检查测试证据
给出 approve/request_changes
```

验收标准：

```text
测试失败不能进入 merge
Reviewer request changes 会自动创建 fix task
QA 报告保存命令输出
Final report 展示通过/失败测试
```

------

## **Phase 8：Local Dashboard**

目标：把 CLI 能力变成可视化工作台。

任务：

```text
实现项目列表
实现任务板
实现 agent run timeline
实现 artifact viewer
实现 diff viewer
实现 test result viewer
实现 approval UI
实现 settings UI
```

API 设计：

```text
GET  /api/projects
POST /api/projects
GET  /api/projects/{id}
GET  /api/runs/{id}
POST /api/runs/{id}/start
POST /api/tasks/{id}/retry
POST /api/approvals/{id}/approve
POST /api/approvals/{id}/reject
GET  /api/artifacts/{id}
GET  /api/tasks/{id}/diff
GET  /api/settings
POST /api/settings
```

验收标准：

```text
可以在 Dashboard 创建项目
可以启动 workflow
可以看 agent 状态
可以看 PRD/design/architecture
可以看 diff
可以 approve/reject gate
```

------

# **十三、测试计划**

## **13.1 Orchestrator 单元测试**

```text
workflow dependency resolution
task status transition
approval gate
agent config loading
permission matching
path lock conflict
artifact registration
cost tracking
```

## **13.2 工具集成测试**

```text
file read/write
git diff
git worktree create/delete
shell command timeout
sandbox command execution
web search mock
scrape mock
v0 mock
```

## **13.3 端到端测试**

准备 3 个固定样例项目：

```text
1. Todo App
2. Personal Expense Tracker
3. Landing Page Generator
```

每个样例跑完整流程：

```text
new project
research
prd
design
architecture
implementation
qa
review
final report
```

验收标准：

```text
每个样例都能生成 docs
至少一个样例能生成可运行代码
失败时能定位到具体 phase/task
重新运行不会破坏已有 artifacts
```

------

# **十四、记忆系统设计**

第一版先不要上复杂 RAG。先用 markdown + SQLite。

```text
.agent/memory/
  user-preferences.md
  project-decisions.md
  tech-stack.md
  design-preferences.md
  known-failures.md
  reusable-prompts.md
```

第二版再加向量库：

```text
Chroma
LanceDB
Qdrant
```

记忆类型：

```text
User Memory:
  你喜欢的技术栈、UI 风格、语言偏好

Project Memory:
  当前项目目标、技术决策、架构限制

Agent Memory:
  每个 agent 的成功经验和失败经验

Skill Memory:
  可复用技能，比如“生成 Next.js + FastAPI 项目”
```

CrewAI 文档中也把 memory、knowledge、guardrails、observability 作为多 agent 系统的重要能力；但你的第一版可以先做简单、可审查、可删除的本地 markdown memory，不要一开始做黑盒长期记忆。 

------

# **十五、MVP 优先级**

## **P0 必须做**

```text
本地项目创建
workflow engine
task board
agent config
model router
PM PRD 生成
Architect 任务拆分
Developer 写代码
QA 跑测试
Reviewer 审查
本地 logs/artifacts
权限系统
```

## **P1 很重要**

```text
Dashboard
Git worktree
Docker sandbox
PM 联网调研
UI design agent
v0 API
approval UI
cost tracking
```

## **P2 后续增强**

```text
Figma MCP
多 developer 并行
frontend/backend agent 拆分
visual regression
memory RAG
agent-to-agent mailbox
OpenHands worker plugin
Claude Code worker plugin
benchmark/eval
```

## **P3 以后再做**

```text
移动端 app workflow
云端部署
团队共享
插件市场
自动发布
复杂企业权限
```

------

# **十六、实际开发顺序**

我建议按这个顺序写代码：

```text
1. CLI + SQLite + Project 创建
2. Workflow Engine
3. Task Board
4. Agent YAML Loader
5. Model Router
6. PM Agent
7. Artifact Store
8. Approval Gate
9. Architect Agent
10. Developer Agent 基础文件编辑
11. Git Diff + Worktree
12. Shell Tool + Permission Engine
13. QA Agent
14. Reviewer Agent
15. Dashboard
16. Research Tools
17. UI Agent
18. v0 Integration
19. Docker Sandbox
20. Memory
```

不要先做 Dashboard，也不要先做漂亮的 agent 聊天界面。先把 CLI 跑通，Dashboard 只是把 CLI 的状态可视化。

------

# **十七、第一版验收 Demo**

第一版最终应该能演示：

```bash
agent-studio new "做一个个人记账 web app，支持添加收入支出、分类、月度统计"
agent-studio run
```

系统自动生成：

```text
docs/product/research.md
docs/product/prd.md
docs/product/acceptance-criteria.md
docs/design/user-flow.md
docs/design/design-system.md
docs/design/component-spec.md
docs/architecture/architecture.md
docs/architecture/api.openapi.yaml
docs/architecture/database-schema.md
apps/web/...
apps/api/...
tests/...
docs/qa/test-results.md
docs/review/review-report.md
```

最终报告：

```text
项目目标
已实现功能
未实现功能
测试结果
代码 diff
已知问题
下一步 backlog
```

------

# **十八、风险和解决方案**

## **风险 1：agent 乱改文件**

解决：

```text
allowed_paths
path lock
git worktree
permission engine
diff review
```

## **风险 2：PM 调研胡编**

解决：

```text
所有 research insight 必须带 source
没有来源必须标注为 assumption
Research artifact 保存原始 sources
```

## **风险 3：UI 生成代码质量不稳定**

解决：

```text
v0 只进 artifacts，不直接写主项目
UI agent 先出 component spec
Developer agent 再按项目规范实现
Reviewer 检查 diff
```

## **风险 4：多 agent 协调成本高**

解决：

```text
第一版不做真并行
先做阶段制 workflow
第二版再做 worktree 并行
只在任务独立时并行
```

## **风险 5：本地安全**

解决：

```text
默认 Docker sandbox
危险命令审批
禁止读 home 敏感目录
禁止 sudo
禁止上传 repo
```

## **风险 6：成本失控**

解决：

```text
每个 run 有预算
每个 agent 有 max iterations
PM research source 数量限制
日志记录 token/cost
可把 QA/reviewer 路由到便宜模型
```

------

# **十九、建议的最小可开发版本任务清单**

你可以把下面当成 GitHub issues。

## **Epic 1：Project Core**

```text
[CORE-001] 初始化 monorepo
[CORE-002] 实现 CLI 框架
[CORE-003] 实现 SQLite schema
[CORE-004] 实现 project 创建
[CORE-005] 实现 run 创建
[CORE-006] 实现 artifact store
```

## **Epic 2：Workflow**

```text
[WF-001] 读取 workflow YAML
[WF-002] 实现 phase 状态机
[WF-003] 实现 task DAG
[WF-004] 实现 approval gate
[WF-005] 实现 retry
[WF-006] 实现 event log
```

## **Epic 3：Agent Runtime**

```text
[AGENT-001] 读取 agent YAML
[AGENT-002] 实现 BaseAgent
[AGENT-003] 实现 prompt renderer
[AGENT-004] 实现 model router
[AGENT-005] 实现 structured output parser
[AGENT-006] 实现 cost tracking
```

## **Epic 4：PM / Product**

```text
[PM-001] 实现 project brief 生成
[PM-002] 实现 PRD 生成
[PM-003] 实现用户故事生成
[PM-004] 实现验收标准生成
[PM-005] 接入 Tavily search
[PM-006] 接入 Firecrawl scrape
[PM-007] 生成 research.md
```

## **Epic 5：Design**

```text
[UI-001] 实现 user-flow.md
[UI-002] 实现 design-system.md
[UI-003] 实现 component-spec.md
[UI-004] 实现 UI reference collector
[UI-005] 接入 v0 API
[UI-006] 保存 v0 生成文件
```

## **Epic 6：Architecture**

```text
[ARCH-001] 生成 architecture.md
[ARCH-002] 生成 OpenAPI
[ARCH-003] 生成 DB schema
[ARCH-004] 生成 ADR
[ARCH-005] 生成 implementation tasks
```

## **Epic 7：Development Worker**

```text
[DEV-001] 实现 file edit tool
[DEV-002] 实现 grep/search tool
[DEV-003] 实现 shell tool
[DEV-004] 实现 permission engine
[DEV-005] 实现 git diff
[DEV-006] 实现 git worktree
[DEV-007] 实现 developer agent
```

## **Epic 8：QA / Review**

```text
[QA-001] 实现 test runner
[QA-002] 实现 test result parser
[QA-003] 实现 QA agent
[QA-004] 实现 bug report
[REV-001] 实现 reviewer agent
[REV-002] 实现 approve/request changes
[REV-003] 实现 fix task 自动创建
```

## **Epic 9：Dashboard**

```text
[DASH-001] 项目列表
[DASH-002] 项目详情
[DASH-003] 任务板
[DASH-004] Run timeline
[DASH-005] Artifact viewer
[DASH-006] Diff viewer
[DASH-007] Test result viewer
[DASH-008] Approval panel
[DASH-009] Settings
```

------

# **二十、我建议的开发策略**

第一阶段不要追求“智能”，先追求“可控”。

最小闭环是：

```text
需求输入
 -> PM 生成 PRD
 -> Architect 生成任务
 -> Developer 写一个页面
 -> QA 跑测试
 -> Reviewer 审查
 -> Lead 输出报告
```

等这个闭环稳定后，再加：

```text
联网调研
v0 UI 生成
多 worktree 并行
Figma MCP
长期记忆
更多角色
```

最关键的工程判断是：

这个项目的核心不是 LLM wrapper，而是本地多 agent 软件工程操作系统：workflow、task board、artifact、permission、sandbox、review gate。

只要这几个底座做对，后面接 Claude Code、OpenHands、v0、Figma、Ollama 或其他模型都只是插件问题。
