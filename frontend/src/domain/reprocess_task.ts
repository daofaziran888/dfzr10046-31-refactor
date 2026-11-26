/** 重跑任务的纯领域规则：依赖装载窗口时，只接受窗口的真实受理事实和截止日期。 */
import type { EntryRow } from '@/data/types'
import { resolveAcceptedWindowDependency } from '@/domain/load_window'

export const REPROCESS_MODULE = 'reprocess_task'

export interface ReprocessDraft {
  影响事实表?: string
  重跑水位?: string
  装载截止日期?: string
  装载窗口编号?: string
  [key: string]: unknown
}

/**
 * 返回应提交/展示的排期字段。依赖是普通手工批次时原样返回；只有解析到装载窗口
 * 才用服务端已受理的事实表和截止日期覆盖预览值。
 */
export function withResolvedWindowDependency<T extends ReprocessDraft>(
  windows: EntryRow[],
  draft: T,
  dependencyReference: unknown = draft.依赖批次,
): { draft: T; error: string | null; resolved: boolean } {
  const { resolution, error } = resolveAcceptedWindowDependency(windows, dependencyReference)
  if (error) return { draft, error, resolved: false }
  if (!resolution) return { draft, error: null, resolved: false }

  return {
    draft: {
      ...draft,
      影响事实表: resolution.factTable,
      重跑区间: resolution.cutoffDate,
      重跑水位: resolution.cutoffDate,
      装载截止日期: resolution.cutoffDate,
      装载窗口编号: resolution.windowNo,
    },
    error: null,
    resolved: true,
  }
}
