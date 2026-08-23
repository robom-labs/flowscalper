// 실시간 점수 변동 중에도 선택 종목과 목록 위치를 안정적으로 유지한다.
import { useEffect, useMemo, useState } from 'react'
import type { ScannerRow } from '../types'

export function useStableScannerOrder(
  rows: ScannerRow[],
  locked: boolean,
  selectedSymbol: string,
  protectedSymbols: string[] = [],
) {
  const [order, setOrder] = useState<string[]>(() => [...rows].sort((left, right) => left.rank - right.rank).map((row) => row.symbol))
  const protectedKey = protectedSymbols.join('|')

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setOrder((current) => {
        const available = new Set(rows.map((row) => row.symbol))
        const existing = current.filter((symbol) => available.has(symbol))
        const newSymbols = rows.map((row) => row.symbol).filter((symbol) => !existing.includes(symbol))
        return [...existing, ...newSymbols]
      })
    }, 0)
    return () => window.clearTimeout(timer)
  }, [rows])

  useEffect(() => {
    if (locked) return
    const timer = window.setInterval(() => {
      setOrder((current) => {
        const desired = [...rows].sort((left, right) => left.rank - right.rank).map((row) => row.symbol)
        const pinned = new Set([selectedSymbol, ...protectedKey.split('|').filter(Boolean)])
        for (const symbol of current.filter((item) => pinned.has(item))) {
          const oldIndex = current.indexOf(symbol)
          const nextIndex = desired.indexOf(symbol)
          if (nextIndex >= 0) desired.splice(nextIndex, 1)
          desired.splice(Math.min(oldIndex, desired.length), 0, symbol)
        }
        return desired
      })
    }, 5_000)
    return () => window.clearInterval(timer)
  }, [locked, protectedKey, rows, selectedSymbol])

  return useMemo(() => {
    const bySymbol = new Map(rows.map((row) => [row.symbol, row]))
    return order.map((symbol) => bySymbol.get(symbol)).filter((row): row is ScannerRow => Boolean(row))
  }, [order, rows])
}
