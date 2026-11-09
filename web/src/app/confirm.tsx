import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";
import { Dialog, DialogContent } from "../ui/dialog";
import { Button } from "../ui/primitives";

export type ConfirmOptions = {
  title: string;
  body?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "default" | "danger";
};

type ConfirmState = {
  options: ConfirmOptions;
  resolve: (value: boolean) => void;
};

const ConfirmContext = createContext<
  (options: ConfirmOptions) => Promise<boolean>
>(async () => false);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ConfirmState | null>(null);

  const confirm = useCallback(
    (options: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        setState((current) => {
          current?.resolve(false);
          return { options, resolve };
        });
      }),
    [],
  );

  function finish(value: boolean) {
    state?.resolve(value);
    setState(null);
  }

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Dialog
        open={!!state}
        onOpenChange={(open) => {
          if (!open) finish(false);
        }}
      >
        {state && (
          <DialogContent
            title={state.options.title}
            size="sm"
            footer={
              <>
                <Button variant="ghost" onClick={() => finish(false)}>
                  {state.options.cancelLabel ?? "Cancel"}
                </Button>
                <Button
                  variant={
                    state.options.tone === "danger" ? "danger" : "primary"
                  }
                  onClick={() => finish(true)}
                >
                  {state.options.confirmLabel ?? "Continue"}
                </Button>
              </>
            }
          >
            <div className="confirm-body">{state.options.body}</div>
          </DialogContent>
        )}
      </Dialog>
    </ConfirmContext.Provider>
  );
}

export function useConfirm() {
  return useContext(ConfirmContext);
}
