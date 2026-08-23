// 대시보드 HTTP 요청의 timeout·취소·한국어 오류 계약을 한곳에서 처리한다.
export class ApiError extends Error {
  status: number
  code: string
  messageKo: string
  retryable: boolean
  details: unknown

  constructor(options: {
    status?: number
    code: string
    messageKo: string
    retryable?: boolean
    details?: unknown
  }) {
    super(options.messageKo)
    this.name = 'ApiError'
    this.status = options.status ?? 0
    this.code = options.code
    this.messageKo = options.messageKo
    this.retryable = options.retryable ?? false
    this.details = options.details
  }
}

function abortSignals(external: AbortSignal | null | undefined, timeout: AbortSignal) {
  if (!external) return timeout
  if ('any' in AbortSignal) return AbortSignal.any([external, timeout])
  const controller = new AbortController()
  const abort = () => controller.abort()
  external.addEventListener('abort', abort, { once: true })
  timeout.addEventListener('abort', abort, { once: true })
  return controller.signal
}

export async function fetchJson<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = 15_000,
): Promise<T> {
  const timeoutController = new AbortController()
  const timer = window.setTimeout(() => timeoutController.abort(), timeoutMs)
  const signal = abortSignals(init.signal, timeoutController.signal)
  try {
    const response = await fetch(path, {
      ...init,
      signal,
      headers: {
        'Content-Type': 'application/json',
        ...init.headers,
      },
    })
    if (response.status === 204) return undefined as T
    let body: unknown
    try {
      body = await response.json()
    } catch {
      if (response.ok) {
        throw new ApiError({
          status: response.status,
          code: 'INVALID_RESPONSE',
          messageKo: '프로그램 서버의 응답 형식이 올바르지 않습니다.',
        })
      }
    }
    if (!response.ok) {
      const root = body && typeof body === 'object' ? body as Record<string, unknown> : {}
      const rawDetail = root.detail
      const detail = rawDetail && typeof rawDetail === 'object'
        ? rawDetail as Record<string, unknown>
        : root
      const message = typeof detail.error_message_ko === 'string'
        ? detail.error_message_ko
        : typeof rawDetail === 'string'
          ? rawDetail
          : '프로그램 요청을 처리하지 못했습니다.'
      throw new ApiError({
        status: response.status,
        code: typeof detail.error_code === 'string' ? detail.error_code : `HTTP_${response.status}`,
        messageKo: message,
        retryable: detail.retryable === true,
        details: detail,
      })
    }
    return body as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (timeoutController.signal.aborted && !init.signal?.aborted) {
      throw new ApiError({
        code: 'REQUEST_TIMEOUT',
        messageKo: '요청 시간이 초과되었습니다. 프로그램 상태를 확인하고 다시 시도하세요.',
        retryable: true,
      })
    }
    if (init.signal?.aborted) throw error
    throw new ApiError({
      code: 'BACKEND_UNREACHABLE',
      messageKo: '프로그램 서버에 연결하지 못했습니다.',
      retryable: true,
      details: error,
    })
  } finally {
    window.clearTimeout(timer)
  }
}
