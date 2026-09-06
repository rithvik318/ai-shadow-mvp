import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import * as api from "../api";
import { setActiveUserId } from "../api/client";
import type { User } from "../api/types";

/**
 * Which Digital Twin the workspace is acting as.
 *
 * Identity, not authentication: the backend trusts `X-User-ID` exactly as
 * sent. Selecting a twin selects whose profile and memories shape an answer
 * and whose memories are being managed — it grants nothing, and the UI says
 * so where a person can read it.
 */
interface TwinContextValue {
  twins: User[];
  currentTwin: User | null;
  loading: boolean;
  error: string | null;
  select: (id: string) => void;
  refresh: () => Promise<void>;
  create: (input: { name: string; email: string; role: string }) => Promise<User>;
}

const TwinContext = createContext<TwinContextValue | null>(null);

const STORAGE_KEY = "sunradia.currentTwinId";

export function TwinProvider({ children }: { children: ReactNode }) {
  const [twins, setTwins] = useState<User[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const list = await api.listUsers();
      setTwins(list.items);

      setCurrentId((current) => {
        if (current && list.items.some((twin) => twin.id === current)) return current;

        const remembered = window.localStorage.getItem(STORAGE_KEY);
        if (remembered && list.items.some((twin) => twin.id === remembered)) {
          return remembered;
        }

        return list.items[0]?.id ?? null;
      });
    } catch {
      setError("Unable to load Digital Twins. Check that the backend is running.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (currentId) window.localStorage.setItem(STORAGE_KEY, currentId);
  }, [currentId]);

  const create = useCallback(
    async (input: { name: string; email: string; role: string }) => {
      const created = await api.createUser(input);
      setTwins((existing) => [...existing, created]);
      setCurrentId(created.id);

      return created;
    },
    [],
  );

  // Published to the API client during render, not in an effect. React runs
  // child effects *before* a parent's, so an effect here would land after the
  // first request a child fires on mount — which is exactly how the Email
  // Agent's provider-status call went out with no identity and came back 401.
  // The assignment is idempotent and touches no React state, so repeating it
  // on every render (including StrictMode's double render) costs nothing.
  setActiveUserId(currentId);

  const value = useMemo<TwinContextValue>(
    () => ({
      twins,
      currentTwin: twins.find((twin) => twin.id === currentId) ?? null,
      loading,
      error,
      select: setCurrentId,
      refresh,
      create,
    }),
    [twins, currentId, loading, error, refresh, create],
  );

  return <TwinContext.Provider value={value}>{children}</TwinContext.Provider>;
}

export function useTwins(): TwinContextValue {
  const value = useContext(TwinContext);

  if (!value) throw new Error("useTwins must be used inside a TwinProvider.");

  return value;
}
