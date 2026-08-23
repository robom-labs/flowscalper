// 캔들 입력을 변경하지 않고 전문 차트 보조지표를 결정적으로 계산한다.
export type IndicatorCandle = {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export type IndicatorPoint = { time: number; value: number }

const sorted = (candles: IndicatorCandle[]) => [...candles].sort((left, right) => left.time - right.time)
const finite = (value: number) => Number.isFinite(value)

export function sma(candles: IndicatorCandle[], period: number): IndicatorPoint[] {
  if (period <= 0) return []
  const input = sorted(candles)
  const result: IndicatorPoint[] = []
  let sum = 0
  for (let index = 0; index < input.length; index += 1) {
    sum += input[index].close
    if (index >= period) sum -= input[index - period].close
    if (index >= period - 1) {
      const value = sum / period
      if (finite(value)) result.push({ time: input[index].time, value })
    }
  }
  return result
}

function emaValues(points: IndicatorPoint[], period: number) {
  if (period <= 0 || points.length < period) return []
  const multiplier = 2 / (period + 1)
  let current = points.slice(0, period).reduce((total, point) => total + point.value, 0) / period
  const result = [{ time: points[period - 1].time, value: current }]
  for (const point of points.slice(period)) {
    current = (point.value - current) * multiplier + current
    if (finite(current)) result.push({ time: point.time, value: current })
  }
  return result
}

export function ema(candles: IndicatorCandle[], period: number): IndicatorPoint[] {
  return emaValues(sorted(candles).map((candle) => ({ time: candle.time, value: candle.close })), period)
}

export function vwap(candles: IndicatorCandle[]): IndicatorPoint[] {
  let cumulativePv = 0
  let cumulativeVolume = 0
  let previous: number | null = null
  return sorted(candles).flatMap((candle) => {
    const typical = (candle.high + candle.low + candle.close) / 3
    if (candle.volume > 0 && finite(candle.volume)) {
      cumulativePv += typical * candle.volume
      cumulativeVolume += candle.volume
    }
    const value = cumulativeVolume > 0 ? cumulativePv / cumulativeVolume : previous ?? typical
    if (!finite(value)) return []
    previous = value
    return [{ time: candle.time, value }]
  })
}

export function bollinger(candles: IndicatorCandle[], period = 20, deviation = 2) {
  if (period <= 0 || deviation < 0) return { middle: [], upper: [], lower: [] }
  const input = sorted(candles)
  const middle: IndicatorPoint[] = []
  const upper: IndicatorPoint[] = []
  const lower: IndicatorPoint[] = []
  for (let index = period - 1; index < input.length; index += 1) {
    const window = input.slice(index - period + 1, index + 1).map((candle) => candle.close)
    const mean = window.reduce((total, value) => total + value, 0) / period
    const variance = window.reduce((total, value) => total + (value - mean) ** 2, 0) / period
    const standardDeviation = Math.sqrt(variance)
    const values = [mean, mean + deviation * standardDeviation, mean - deviation * standardDeviation]
    if (values.every(finite)) {
      middle.push({ time: input[index].time, value: values[0] })
      upper.push({ time: input[index].time, value: values[1] })
      lower.push({ time: input[index].time, value: values[2] })
    }
  }
  return { middle, upper, lower }
}

export function rsi(candles: IndicatorCandle[], period = 14): IndicatorPoint[] {
  const input = sorted(candles)
  if (period <= 0 || input.length <= period) return []
  let gain = 0
  let loss = 0
  for (let index = 1; index <= period; index += 1) {
    const change = input[index].close - input[index - 1].close
    gain += Math.max(change, 0)
    loss += Math.max(-change, 0)
  }
  let averageGain = gain / period
  let averageLoss = loss / period
  const valueOf = () => averageLoss === 0
    ? averageGain === 0 ? 50 : 100
    : 100 - 100 / (1 + averageGain / averageLoss)
  const result: IndicatorPoint[] = [{ time: input[period].time, value: valueOf() }]
  for (let index = period + 1; index < input.length; index += 1) {
    const change = input[index].close - input[index - 1].close
    averageGain = (averageGain * (period - 1) + Math.max(change, 0)) / period
    averageLoss = (averageLoss * (period - 1) + Math.max(-change, 0)) / period
    const value = valueOf()
    if (finite(value)) result.push({ time: input[index].time, value })
  }
  return result
}

export function macd(candles: IndicatorCandle[], fast = 12, slow = 26, signalPeriod = 9) {
  const fastPoints = ema(candles, fast)
  const slowPoints = ema(candles, slow)
  const fastByTime = new Map(fastPoints.map((point) => [point.time, point.value]))
  const line = slowPoints.flatMap((point) => {
    const fastValue = fastByTime.get(point.time)
    const value = fastValue === undefined ? Number.NaN : fastValue - point.value
    return finite(value) ? [{ time: point.time, value }] : []
  })
  const signal = emaValues(line, signalPeriod)
  const signalByTime = new Map(signal.map((point) => [point.time, point.value]))
  const histogram = line.flatMap((point) => {
    const signalValue = signalByTime.get(point.time)
    const value = signalValue === undefined ? Number.NaN : point.value - signalValue
    return finite(value) ? [{ time: point.time, value }] : []
  })
  return { line, signal, histogram }
}
