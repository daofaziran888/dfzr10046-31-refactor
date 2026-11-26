/** 装载窗口的纯领域判定；阈值和字段名与后端 LoadWindowService 保持一致。 */
import type { EntryRow } from '@/data/types'

export const LOAD_WINDOW_MODULE = 'load_window'
export const ACCEPTED_STATUS = '已装载'
export const REJECTED_STATUS = '装载中断'
export const MAX_DEVIATION = 0.2

export function deviationRatio(expectedRows: number, actualRows: number): number | null {
  if (!Number.isFinite(expectedRows) || expectedRows <= 0) return null
  return Math.abs(actualRows - expectedRows) / expectedRows
}

export function canAdvanceWindow(expectedRows: number, actualRows: number): boolean {
  const ratio = deviationRatio(expectedRows, actualRows)
  return ratio !== null && ratio <= MAX_DEVIATION
}

export function findWindowByReference(
  rows: EntryRow[],
  reference: unknown,
): EntryRow | undefined {
  if (reference === null || reference === undefined) return undefined
  if (typeof reference === 'object') {
    const maybe = reference as { entry?: EntryRow; id?: number | string; 窗口编号?: string }
    const entry = maybe.entry ?? maybe
    if (entry?.id !== undefined) {
      const byId = rows.find((row) => String(row.id) === String(entry.id))
      if (byId) return byId
    }
    reference = entry?.窗口编号 ?? entry?.id
  }

  const text = String(reference).trim()
  if (!text) return undefined
  return rows.find((row) => String(row.窗口编号 ?? '').trim() === text)
    ?? rows.find((row) => String(row.id) === text.replace(/^(load_window:|window:|id:)/, ''))
}

/** 只有真实已受理窗口才给出截止日期；被拒窗口的预览终点不会被当作受理日期。 */
export function acceptedCutoff(window: EntryRow | undefined): string | null {
  if (!window || window.status !== ACCEPTED_STATUS) return null
  if (window.ok === false || window.accepted === false) return null
  const cutoff = window.装载截止日期 ?? window.装载水位 ?? window.窗口终点
  const text = cutoff === null || cutoff === undefined ? '' : String(cutoff).trim()
  return text || null
}

export interface WindowDependencyResolution {
  window: EntryRow
  windowNo: string
  factTable: string
  cutoffDate: string
}

export function resolveAcceptedWindowDependency(
  rows: EntryRow[],
  reference: unknown,
): { resolution: WindowDependencyResolution | null; error: string | null } {
  const window = findWindowByReference(rows, reference)
  if (!window) return { resolution: null, error: null }
  const windowNo = String(window.窗口编号 ?? reference)
  const cutoffDate = acceptedCutoff(window)
  const factTable = String(window.所属事实表 ?? '').trim()
  if (!cutoffDate || !factTable) {
    return {
      resolution: null,
      error: `依赖的装载窗口 ${windowNo} 尚未受理或推进被拒，不能使用预览截止日期排期`,
    }
  }
  return { resolution: { window, windowNo, factTable, cutoffDate }, error: null }
}
