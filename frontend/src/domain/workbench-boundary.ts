/**
 * 工作台请求交接边界。
 *
 * 该模块不依赖 Vue/DOM：组件卸载或模块切换只意味着 UI 订阅者变化，已经发出的
 * 请求记录和“发出时绑定的处理程序”仍由模块级单例保留。新入口也可以凭同一个
 * request id 取回原 Promise/回执，而不是再次调用发送函数。
 */

export type RequestId = string

export type BoundaryStatus = 'pending' | 'fulfilled' | 'rejected'

export interface BoundaryOutcome<T> {
  ok: boolean
  receipt?: T
  error?: unknown
  request: BoundaryRequest<T>
}

export type BoundaryHandler<T> = (outcome: BoundaryOutcome<T>) => void

export interface BoundaryRequest<T> {
  id: RequestId
  scope: string
  kind: string
  subjectKey?: string
  status: BoundaryStatus
  sentAt: number
  settledAt?: number
  receipt?: T
  error?: unknown
  promise: Promise<T>
  handlers: Set<BoundaryHandler<T>>
}

export interface SubmitOptions<T> {
  /** 显式请求标识；同一个 id 的重入一律取回原请求。 */
  id?: RequestId
  /** 模块 key，例如 load_window / reprocess_task。 */
  scope: string
  /** list/create/action/export 等请求类别。 */
  kind?: string
  /** 实际提交对象，例如 load_window:12:确认装载。 */
  subjectKey?: string
  /** 发送时即绑定的回执处理程序；模块切换不会释放它。 */
  handler?: BoundaryHandler<T>
  /** 实际网络请求；同一个 boundary id 至多调用一次。 */
  send: (context: { id: RequestId }) => Promise<T>
  now?: () => number
}

export interface PendingFilter {
  scope?: string
  kind?: string
  subjectKey?: string
}

let sequence = 0

export function createRequestId(prefix = 'wb-req'): RequestId {
  const cryptoRef = globalThis.crypto as Crypto | undefined
  if (cryptoRef && typeof cryptoRef.randomUUID === 'function') {
    return `${prefix}-${cryptoRef.randomUUID()}`
  }
  sequence += 1
  return `${prefix}-${Date.now()}-${sequence}`
}

export function subjectKey(
  moduleKey: string,
  entryId: string | number,
  operation: string,
): string {
  return `${moduleKey}:${String(entryId)}:${operation}`
}

/** 与旧 frontend 调用保持一致：中文键和原始空白都交给 URL/后端处理。 */
export function toQueryString(filters: Record<string, unknown> = {}): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value === null || value === undefined) continue
    params.append(key, String(value))
  }
  return params.toString()
}

/** 查询/导出共享的已提交条件快照；之后继续输入不会改变本次范围。 */
export function commitFilters(
  draft: Record<string, string>,
  previousNonce = 0,
): { filters: Record<string, string>; queryString: string; nonce: number; committedAt: number } {
  const filters = { ...draft }
  return {
    filters,
    queryString: toQueryString(filters),
    nonce: previousNonce + 1,
    committedAt: Date.now(),
  }
}

export class WorkbenchRequestBoundary {
  private requests = new Map<RequestId, BoundaryRequest<unknown>>()

  createId(prefix?: string): RequestId {
    return createRequestId(prefix)
  }

  has(id: RequestId): boolean {
    return this.requests.has(id)
  }

  get<T = unknown>(id: RequestId): BoundaryRequest<T> | undefined {
    return this.requests.get(id) as BoundaryRequest<T> | undefined
  }

  getReceipt<T = unknown>(id: RequestId): T | undefined {
    const request = this.get<T>(id)
    return request?.status === 'fulfilled' ? request.receipt : undefined
  }

  getError(id: RequestId): unknown {
    const request = this.get(id)
    return request?.status === 'rejected' ? request.error : undefined
  }

  /** 新入口凭原 id 等待同一份回执；不会重新发送。 */
  async receive<T = unknown>(id: RequestId): Promise<T> {
    const request = this.get<T>(id)
    if (!request) throw new Error(`工作台请求 ${id} 不存在或已释放`)
    return request.promise
  }

  /** 绑定一个回执消费者。若请求已结束，异步回放同一份原结果。 */
  bind<T = unknown>(id: RequestId, handler: BoundaryHandler<T>): () => void {
    const request = this.get<T>(id)
    if (!request) {
      queueMicrotask(() => handler({
        ok: false,
        error: new Error(`工作台请求 ${id} 不存在或已释放`),
        request: undefined as unknown as BoundaryRequest<T>,
      }))
      return () => {}
    }

    const alreadyBound = request.handlers.has(handler)
    request.handlers.add(handler)
    if (request.status !== 'pending' && !alreadyBound) {
      const outcome: BoundaryOutcome<T> = request.status === 'fulfilled'
        ? { ok: true, receipt: request.receipt as T, request }
        : { ok: false, error: request.error, request }
      queueMicrotask(() => handler(outcome))
    }
    return () => request.handlers.delete(handler)
  }

  submit<T>(options: SubmitOptions<T>): BoundaryRequest<T> {
    const id = options.id ?? createRequestId(`${options.scope}-${options.kind ?? 'request'}`)
    const existing = this.get<T>(id)
    if (existing) {
      if (options.handler) this.bind(id, options.handler)
      return existing
    }

    const now = options.now ?? Date.now
    const request: BoundaryRequest<T> = {
      id,
      scope: options.scope,
      kind: options.kind ?? 'request',
      subjectKey: options.subjectKey,
      status: 'pending',
      sentAt: now(),
      handlers: new Set(),
      promise: undefined as unknown as Promise<T>,
    }
    if (options.handler) request.handlers.add(options.handler)
    this.requests.set(id, request as BoundaryRequest<unknown>)

    let sent: Promise<T>
    try {
      sent = Promise.resolve(options.send({ id }))
    } catch (error) {
      sent = Promise.reject(error)
    }

    const promise = sent.then(
      (receipt) => {
        request.status = 'fulfilled'
        request.receipt = receipt
        request.settledAt = now()
        this.emit(request, { ok: true, receipt })
        return receipt
      },
      (error: unknown) => {
        request.status = 'rejected'
        request.error = error
        request.settledAt = now()
        this.emit(request, { ok: false, error })
        throw error
      },
    )
    request.promise = promise
    // 边界本身始终保留一个错误消费者，避免无人 await 时产生 unhandled rejection。
    promise.catch(() => {})
    return request
  }

  private emit<T>(request: BoundaryRequest<T>, base: { ok: boolean; receipt?: T; error?: unknown }) {
    for (const handler of [...request.handlers]) {
      try {
        handler({ ...base, request })
      } catch {
        // 一个 UI 消费者失败不能影响原回执或其他模块消费者。
      }
    }
  }

  listPending(filter: PendingFilter = {}): BoundaryRequest<unknown>[] {
    return [...this.requests.values()].filter((request) => {
      if (request.status !== 'pending') return false
      if (filter.scope && request.scope !== filter.scope) return false
      if (filter.kind && request.kind !== filter.kind) return false
      if (filter.subjectKey && request.subjectKey !== filter.subjectKey) return false
      return true
    })
  }

  findPending<T = unknown>(filter: PendingFilter = {}): BoundaryRequest<T> | undefined {
    return this.listPending(filter)[0] as BoundaryRequest<T> | undefined
  }

  /**
   * 显式释放只用于测试/页面销毁后的内存回收；正常“模块切换”不要调用，
   * 这样发出时绑定的处理程序不会被路由切换释放。
   */
  release(id: RequestId): boolean {
    return this.requests.delete(id)
  }

  clear(): void {
    this.requests.clear()
  }
}

/** 两个工作台和新版入口共享的模块级边界。 */
export const workbenchBoundary = new WorkbenchRequestBoundary()

/** 函数式别名：提交一次需要跨模块交接的工作台请求。 */
export function submitWorkbenchRequest<T>(options: SubmitOptions<T>): BoundaryRequest<T> {
  return workbenchBoundary.submit(options)
}

/** 函数式别名：按原 request id 取回同一个回执/Promise。 */
export function receiveWorkbenchReceipt<T = unknown>(id: RequestId): Promise<T> {
  return workbenchBoundary.receive<T>(id)
}

/** 函数式别名：按模块/类别/实际对象寻找仍在途的原请求。 */
export function findPendingWorkbenchRequest<T = unknown>(
  filter: PendingFilter = {},
): BoundaryRequest<T> | undefined {
  return workbenchBoundary.findPending<T>(filter)
}
