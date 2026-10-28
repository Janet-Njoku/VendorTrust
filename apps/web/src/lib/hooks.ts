"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, toApiError } from "./api/errors";

export interface Query<T> {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  reload: () => void;
}

/**
 * Run an async loader whenever `key` changes (or `reload` is called). Stale data stays visible
 * while a reload is in flight; errors replace it. The loader may close over anything: only `key`
 * decides when it runs.
 */
export function useQuery<T>(load: () => Promise<T>, key: string): Query<T> {
  const [bump, setBump] = useState(0);
  const [state, setState] = useState<{ token: string; data?: T; error?: ApiError }>({ token: "" });
  const loader = useRef(load);
  useEffect(() => {
    loader.current = load;
  });
  const token = `${key}#${bump}`;

  useEffect(() => {
    let cancelled = false;
    loader
      .current()
      .then((data) => {
        if (!cancelled) setState({ token, data });
      })
      .catch((error: unknown) => {
        if (!cancelled) setState((previous) => ({ token, data: previous.data, error: toApiError(error) }));
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const reload = useCallback(() => setBump((n) => n + 1), []);
  const loading = state.token !== token;
  return {
    data: state.error && !loading ? undefined : state.data,
    error: loading ? undefined : state.error,
    loading,
    reload,
  };
}

/** Run a mutation with pending and error state, for buttons and forms. */
export function useAction<A extends unknown[], R>(
  run: (...args: A) => Promise<R>,
): {
  run: (...args: A) => Promise<R | undefined>;
  pending: boolean;
  error: ApiError | undefined;
  clear: () => void;
} {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | undefined>();
  const fn = useRef(run);
  useEffect(() => {
    fn.current = run;
  });
  const exec = useCallback(async (...args: A) => {
    setPending(true);
    setError(undefined);
    try {
      return await fn.current(...args);
    } catch (e) {
      setError(toApiError(e));
      return undefined;
    } finally {
      setPending(false);
    }
  }, []);
  const clear = useCallback(() => setError(undefined), []);
  return { run: exec, pending, error, clear };
}
