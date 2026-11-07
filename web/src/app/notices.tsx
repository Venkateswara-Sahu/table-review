import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { AlertTriangle, Check, Info, X, XCircle } from "lucide-react";
import { errorMessage } from "../api/client";

type Tone = "info" | "success" | "warning" | "error";

type Notice = {
  id: number;
  tone: Tone;
  message: string;
};

type NoticeValue = {
  notify: (message: string, tone?: Tone) => void;
  notifyError: (error: unknown) => void;
};

const NoticeContext = createContext<NoticeValue>({
  notify: () => {},
  notifyError: () => {},
});

let next = 1;

export function NoticeProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Notice[]>([]);
  const timers = useRef(new Map<number, number>());

  const dismiss = useCallback((id: number) => {
    setItems((value) => value.filter((item) => item.id !== id));
    const timer = timers.current.get(id);
    if (timer) window.clearTimeout(timer);
    timers.current.delete(id);
  }, []);

  const notify = useCallback(
    (message: string, tone: Tone = "info") => {
      const id = next++;
      setItems((value) => [...value.slice(-3), { id, tone, message }]);
      const timer = window.setTimeout(
        () => dismiss(id),
        tone === "error" ? 8000 : 5000,
      );
      timers.current.set(id, timer);
    },
    [dismiss],
  );

  const notifyError = useCallback(
    (error: unknown) => notify(errorMessage(error), "error"),
    [notify],
  );

  useEffect(
    () => () => {
      for (const timer of timers.current.values()) window.clearTimeout(timer);
      timers.current.clear();
    },
    [],
  );

  const value = useMemo(() => ({ notify, notifyError }), [notify, notifyError]);

  return (
    <NoticeContext.Provider value={value}>
      {children}
      <div className="toast-stack" aria-live="polite" aria-atomic="false">
        {items.map((item) => (
          <div className={`toast toast-${item.tone}`} key={item.id}>
            <span className="toast-icon" aria-hidden="true">
              {item.tone === "success" ? (
                <Check size={15} />
              ) : item.tone === "error" ? (
                <XCircle size={15} />
              ) : item.tone === "warning" ? (
                <AlertTriangle size={15} />
              ) : (
                <Info size={15} />
              )}
            </span>
            <span>{item.message}</span>
            <button
              className="toast-close"
              aria-label="Dismiss"
              onClick={() => dismiss(item.id)}
            >
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </NoticeContext.Provider>
  );
}

export function useNotices() {
  return useContext(NoticeContext);
}
