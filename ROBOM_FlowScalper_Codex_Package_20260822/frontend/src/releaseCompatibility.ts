// 정적 프론트엔드와 백엔드가 같은 불변 릴리스인지 판정해 혼합 배포를 차단한다.
const COMMIT_PATTERN = /^[0-9a-f]{40}$/i

export type ReleaseCompatibility = {
  compatible: boolean
  frontendCommit: string
  backendCommit: string
}

function normalizedCommit(value: unknown) {
  const candidate = typeof value === 'string' ? value.trim() : ''
  return COMMIT_PATTERN.test(candidate) ? candidate.toLowerCase() : 'development'
}

export function readFrontendReleaseCommit(documentRoot: Document = document) {
  return normalizedCommit(
    documentRoot.querySelector<HTMLMetaElement>('meta[name="robom-release-commit"]')?.content,
  )
}

export function compareReleaseCommits(
  frontendValue: unknown,
  backendValue: unknown,
): ReleaseCompatibility {
  const frontendCommit = normalizedCommit(frontendValue)
  const backendCommit = normalizedCommit(backendValue)
  const immutableReleasePresent =
    frontendCommit !== 'development' || backendCommit !== 'development'
  return {
    compatible: !immutableReleasePresent || frontendCommit === backendCommit,
    frontendCommit,
    backendCommit,
  }
}
