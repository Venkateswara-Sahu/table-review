import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { ShortcutsDialog } from "./ShortcutsDialog";

type ShortcutsContextValue = {
  open: () => void;
};

const ShortcutsContext = createContext<ShortcutsContextValue | null>(null);

function isTyping(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)
  );
}

export function ShortcutsProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "?" && !isTyping(event.target)) {
        event.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, []);

  const value = useMemo(() => ({ open: () => setOpen(true) }), []);

  return (
    <ShortcutsContext.Provider value={value}>
      {children}
      <ShortcutsDialog open={open} onOpenChange={setOpen} />
    </ShortcutsContext.Provider>
  );
}

export function useShortcuts() {
  const value = useContext(ShortcutsContext);
  if (!value)
    throw new Error("useShortcuts must be used inside ShortcutsProvider");
  return value;
}
