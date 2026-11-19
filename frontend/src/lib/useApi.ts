import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "./api";

export interface ApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** GET a resource; refetches when `path` or `params` change. Pass path=null to skip. */
export function useApi<T>(path: string | null, params?: Record<string, unknown>): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const [tick, setTick] = useState(0);
  const key = JSON.stringify(params ?? {});
  const seq = useRef(0);

  useEffect(() => {
    if (!path) {
      setLoading(false);
      return;
    }
    const my = ++seq.current;
    setLoading(true);
    setError(null);
    api
      .get<T>(path, { params: JSON.parse(key) })
      .then((r) => my === seq.current && setData(r.data))
      .catch((e) => my === seq.current && setError(errorMessage(e)))
      .finally(() => my === seq.current && setLoading(false));
  }, [path, key, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

/** Wraps a mutating call with busy/error state. */
export function useAction<A extends unknown[], R>(fn: (...args: A) => Promise<R>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(
    async (...args: A): Promise<R | undefined> => {
      setBusy(true);
      setError(null);
      try {
        return await fn(...args);
      } catch (e) {
        setError(errorMessage(e));
        return undefined;
      } finally {
        setBusy(false);
      }
    },
    [fn],
  );
  return { run, busy, error, setError };
}
