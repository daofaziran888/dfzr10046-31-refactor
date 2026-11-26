/**
 * 模块级工作台控制器注册表。
 *
 * WorkbenchView 可以随路由切换卸载，但每个模块的状态和已发出请求的绑定处理程序
 * 保留在这个注册表中。load_window 与 reprocess_task 因而拥有彼此隔离的查询条件、
 * 列表序号和动作对象；交叉延迟返回时只会写入各自的模块状态。
 */
import { computed, reactive, type MaybeRef } from 'vue'

import {
  createEntry,
  downloadEntries,
  fetchActionReceipt,
  listEntries,
  moduleMeta,
  runAction as postAction,
} from '@/api/backend-service'
import type { ActionResult, EntryRow } from '@/data/types'
import {
  commitFilters,
  subjectKey,
  workbenchBoundary,
  type BoundaryHandler,
  type BoundaryRequest,
} from '@/domain/workbench-boundary'

export interface WorkbenchState {
  moduleKey: string
  rows: EntryRow[]
  total: number
  errorMessage: string
  filters: Record<string, string>
  committedFilters: Record<string, string>
  committedQueryString: string
  queryNonce: number
  showCreate: boolean
  creating: boolean
  createValues: Record<string, string>
  loaded: boolean
  activeActionId: string | null
  activeActionSubject: string | null
  activeActions: Record<string, string>
  actionRequestIds: Record<string, string>
  lastReceipt: ActionResult | null
}

export interface RunActionOptions {
  /** 旧在途请求的 id；传入后只取回原回执，不会再次发送动作。 */
  requestId?: string
}

function fallbackMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

export class WorkbenchController {
  readonly key: string
  readonly state: WorkbenchState
  private latestListRequestId: string | null = null

  constructor(key: string) {
    this.key = key
    this.state = reactive<WorkbenchState>({
      moduleKey: key,
      rows: [],
      total: 0,
      errorMessage: '',
      filters: {},
      committedFilters: {},
      committedQueryString: '',
      queryNonce: 0,
      showCreate: false,
      creating: false,
      createValues: {},
      loaded: false,
      activeActionId: null,
      activeActionSubject: null,
      activeActions: {},
      actionRequestIds: {},
      lastReceipt: null,
    })
  }

  resetFilters(): void {
    this.state.filters = {}
    void this.reload()
  }

  async reload(): Promise<void> {
    this.state.errorMessage = ''
    const committed = commitFilters(this.state.filters, this.state.queryNonce)
    this.state.queryNonce = committed.nonce
    this.state.committedFilters = committed.filters
    this.state.committedQueryString = committed.queryString

    const requestId = `${this.key}-list-${committed.nonce}`
    const request = workbenchBoundary.submit({
      id: requestId,
      scope: this.key,
      kind: 'list',
      send: () => listEntries(this.key, committed.filters),
    })
    this.latestListRequestId = requestId

    try {
      const payload = await request.promise
      // 只让本模块最近一次已提交查询落列表；旧条件的迟到响应不能覆盖新范围。
      if (this.latestListRequestId !== requestId) return
      this.state.rows = payload.items
      this.state.total = payload.total
      this.state.loaded = true
    } catch (error) {
      if (this.latestListRequestId !== requestId) return
      this.state.errorMessage = fallbackMessage(error, '业务列表读取失败')
    }
  }

  async exportRows(): Promise<void> {
    this.state.errorMessage = ''
    try {
      // 必须与最近一次“查询/提交”快照同条件，而不是导出尚未提交的输入框草稿。
      await downloadEntries(this.key, this.state.committedFilters)
    } catch (error) {
      this.state.errorMessage = fallbackMessage(error, '导出失败')
    }
  }

  openCreate(): void {
    const meta = moduleMeta(this.key)
    this.state.createValues = Object.fromEntries(meta.fields.map((field) => [field, '']))
    this.state.errorMessage = ''
    this.state.showCreate = true
  }

  closeCreate(): void {
    this.state.showCreate = false
  }

  async submitCreate(): Promise<void> {
    this.state.creating = true
    this.state.errorMessage = ''
    // 复制实际提交对象，之后关闭弹窗/切换模块都不能改写本次 values。
    const values = { ...this.state.createValues }
    const request = workbenchBoundary.submit({
      scope: this.key,
      kind: 'create',
      send: () => createEntry(this.key, values),
    })

    try {
      const result = await request.promise
      if (!result.ok) throw new Error(result.message)
      this.state.showCreate = false
      await this.reload()
    } catch (error) {
      this.state.errorMessage = fallbackMessage(error, '登记失败')
    } finally {
      this.state.creating = false
    }
  }

  private consumeActionOutcome: BoundaryHandler<ActionResult> = (outcome) => {
    if (!outcome.ok) {
      this.state.errorMessage = fallbackMessage(outcome.error, '操作失败')
      return
    }

    const result = outcome.receipt as ActionResult
    this.state.lastReceipt = result
    if (!result.ok) {
      // 与旧页面 throw new Error(result.message) 后 catch 的取值一致：
      // 空消息会得到空错误串（页脚因此不显示），不要额外替换成兜底文案。
      this.state.errorMessage = result.message ?? ''
      return
    }

    this.state.errorMessage = ''
    void this.reload()
  }

  async runAction(
    action: string,
    row: EntryRow,
    options: RunActionOptions = {},
  ): Promise<ActionResult | undefined> {
    this.state.errorMessage = ''
    const requestId = options.requestId ?? workbenchBoundary.createId(`${this.key}-action`)
    const targetKey = subjectKey(this.key, row.id, action)
    this.state.activeActionId = requestId
    this.state.activeActionSubject = targetKey
    this.state.activeActions[targetKey] = requestId
    this.state.actionRequestIds[targetKey] = requestId

    const request = workbenchBoundary.submit<ActionResult>({
      id: requestId,
      scope: this.key,
      kind: 'action',
      subjectKey: targetKey,
      handler: this.consumeActionOutcome,
      send: ({ id }) => postAction(this.key, Number(row.id), action, id),
    })

    try {
      const result = await request.promise
      // 与旧 WorkbenchView 一样，业务 ok=false 对调用方表现为一次已处理的失败。
      if (!result.ok) throw new Error(result.message)
      return result
    } catch {
      // consumeActionOutcome 已写入与旧页面一致的错误文案。
      return undefined
    } finally {
      if (this.state.activeActions[targetKey] === requestId) {
        delete this.state.activeActions[targetKey]
      }
      if (this.state.activeActionId === requestId) {
        this.state.activeActionId = null
        this.state.activeActionSubject = null
      }
    }
  }

  /**
   * 新版入口按原 request id 取回回执：优先共享边界中的同一个在途 Promise；
   * 边界没有（例如整页重载后）时只 GET 服务端回执，绝不重新 POST 动作。
   */
  async reclaimActionReceipt(requestId: string): Promise<ActionResult> {
    if (workbenchBoundary.has(requestId)) {
      return workbenchBoundary.receive<ActionResult>(requestId)
    }
    return fetchActionReceipt(requestId)
  }

  getActionRequest(requestId: string): BoundaryRequest<ActionResult> | undefined {
    return workbenchBoundary.get<ActionResult>(requestId)
  }

  /** 回到工作台但只知道行和动作时，找回仍在途的同一个请求。 */
  findPendingAction(row: EntryRow, action: string): BoundaryRequest<ActionResult> | undefined {
    return workbenchBoundary.findPending<ActionResult>({
      scope: this.key,
      kind: 'action',
      subjectKey: subjectKey(this.key, row.id, action),
    })
  }
}

const controllers = new Map<string, WorkbenchController>()

export function getWorkbenchController(moduleKey: string): WorkbenchController {
  let controller = controllers.get(moduleKey)
  if (!controller) {
    controller = new WorkbenchController(moduleKey)
    controllers.set(moduleKey, controller)
  }
  return controller
}

/** 测试/显式复位使用；路由模块切换不要清空。 */
export function resetWorkbenchControllers(): void {
  controllers.clear()
}

export function useWorkbench(moduleKey: MaybeRef<string>) {
  const key = computed(() => (typeof moduleKey === 'string' ? moduleKey : moduleKey.value))
  const controller = computed(() => getWorkbenchController(key.value))
  const state = computed(() => controller.value.state)

  return {
    key,
    controller,
    state,
    reload: () => controller.value.reload(),
    resetFilters: () => controller.value.resetFilters(),
    exportRows: () => controller.value.exportRows(),
    openCreate: () => controller.value.openCreate(),
    closeCreate: () => controller.value.closeCreate(),
    submitCreate: () => controller.value.submitCreate(),
    runAction: (action: string, row: EntryRow, options?: RunActionOptions) =>
      controller.value.runAction(action, row, options),
    reclaimActionReceipt: (requestId: string) =>
      controller.value.reclaimActionReceipt(requestId),
    findPendingAction: (row: EntryRow, action: string) =>
      controller.value.findPendingAction(row, action),
  }
}
