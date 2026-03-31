import type { SWRConfiguration } from "swr";

export const SWR_BASE_OPTIONS: SWRConfiguration = {
  revalidateOnFocus: false,
  refreshWhenHidden: false,
  refreshWhenOffline: false,
  keepPreviousData: true,
};

export const SWR_INTERVALS = {
  fastPoll: { refreshInterval: 8000, dedupingInterval: 4000 },
  normalPoll: { refreshInterval: 12000, dedupingInterval: 6000 },
  mediumPoll: { refreshInterval: 15000, dedupingInterval: 8000 },
  standardPage: { refreshInterval: 20000, dedupingInterval: 10000 },
  slowPage: { refreshInterval: 30000, dedupingInterval: 10000 },
  /** 总览 Dashboard */
  dashboardPage: { refreshInterval: 60000, dedupingInterval: 30000 },
  /** 市场分析 */
  marketAnalysisPage: { refreshInterval: 60000, dedupingInterval: 30000 },
  slowMetadata: { refreshInterval: 60000, dedupingInterval: 30000 },
} as const;

export function visibilityAwareInterval(
  visibleIntervalMs: number,
  hiddenIntervalMs = 0
): (latestData: unknown) => number {
  return () => {
    if (typeof document !== "undefined" && document.hidden) {
      return hiddenIntervalMs;
    }
    return visibleIntervalMs;
  };
}

export function buildSwrOptions(
  refreshInterval: NonNullable<SWRConfiguration["refreshInterval"]>,
  dedupingInterval: number,
  overrides: SWRConfiguration = {}
): SWRConfiguration {
  return {
    ...SWR_BASE_OPTIONS,
    refreshInterval,
    dedupingInterval,
    ...overrides,
  };
}
