/**
 * Contract ↔ Project 的关联约定 —— 服务端没有强制 mapping，前端用以下顺序找：
 *   1. localStorage 里手动覆盖（每浏览器独立）
 *   2. contract.id === project.id 的精确匹配
 *   3. 找不到 → null
 *
 * 操作员可以在 Discuss & Lock 页通过下拉选择把某个 contract 关联到当前项目，
 * 这个选择就写到 localStorage。详细背景见 docs/STUDIO_CONSOLE_SPEC.md。
 */

import type { ContractSummary } from "./types";

const STORAGE_PREFIX = "studio-console:project:";
const STORAGE_SUFFIX = ":contractId";

function storageKey(projectId: string): string {
  return STORAGE_PREFIX + projectId + STORAGE_SUFFIX;
}

/** 浏览器侧：读取手动覆盖的 contractId（无 SSR 保护，调用前确认 typeof window）。 */
export function readContractOverride(projectId: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(storageKey(projectId));
  } catch {
    return null;
  }
}

/** 浏览器侧：写入手动关联。传 null 则清除。 */
export function writeContractOverride(
  projectId: string,
  contractId: string | null,
): void {
  if (typeof window === "undefined") return;
  try {
    if (contractId == null) {
      window.localStorage.removeItem(storageKey(projectId));
    } else {
      window.localStorage.setItem(storageKey(projectId), contractId);
    }
  } catch {
    // private mode / 配额满 —— 静默失败，下次刷新还是会回退到 id 匹配。
  }
}

/**
 * 给定项目 id 和 contract 列表，返回最可能"对应"的 contract。
 * 先尊重 localStorage 覆盖，然后 id 精确匹配，再返回 null。
 */
export function findContractForProject(
  projectId: string,
  contracts: readonly ContractSummary[],
): ContractSummary | null {
  const override = readContractOverride(projectId);
  if (override) {
    const found = contracts.find((c) => c.id === override);
    if (found) return found;
    // 覆盖指向的 contract 不存在了 —— 清掉，避免下次还查不到。
    writeContractOverride(projectId, null);
  }
  const exact = contracts.find((c) => c.id === projectId);
  return exact ?? null;
}
