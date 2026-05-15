"use client";

import { useMemo } from "react";

export function useDirtyState<T extends Record<string, unknown>>(
  initial: T,
  current: T
): boolean {
  return useMemo(
    () => JSON.stringify(initial) !== JSON.stringify(current),
    [initial, current]
  );
}
