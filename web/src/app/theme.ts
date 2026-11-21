import { useCallback, useEffect, useState } from "react";

export type ThemePreference = "system" | "light" | "dark";

const STORAGE_KEY = "color-scheme";

function readStoredPreference(): ThemePreference {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "light" || raw === "dark") return raw;
  } catch {
    /* Storage access may be restricted. */
  }
  return "system";
}

function applyMetaScheme(preference: ThemePreference) {
  const meta = document.querySelector<HTMLMetaElement>(
    'meta[name="color-scheme"]',
  );
  if (meta) {
    meta.content = preference === "system" ? "light dark" : preference;
  }
}

function getSystemScheme(): "light" | "dark" {
  if (typeof window === "undefined" || !window.matchMedia) return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function useTheme() {
  const [preference, setPreferenceState] =
    useState<ThemePreference>(readStoredPreference);
  const [systemScheme, setSystemScheme] = useState<"light" | "dark">(
    getSystemScheme,
  );

  useEffect(() => {
    applyMetaScheme(preference);
  }, [preference]);

  useEffect(() => {
    if (!window.matchMedia) return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (event: MediaQueryListEvent) => {
      setSystemScheme(event.matches ? "dark" : "light");
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== STORAGE_KEY) return;
      const next =
        event.newValue === "light" || event.newValue === "dark"
          ? event.newValue
          : "system";
      setPreferenceState(next);
      applyMetaScheme(next);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    applyMetaScheme(next);
    try {
      if (next === "system") window.localStorage.removeItem(STORAGE_KEY);
      else window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* Apply in-memory even when storage is blocked. */
    }
  }, []);

  return { preference, systemScheme, setPreference };
}
