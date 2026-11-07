import { Dialog, DialogContent } from "../ui/dialog";
import { Kbd } from "../ui/primitives";

const shortcuts: Array<[string, string]> = [
  ["Arrow keys", "Move between cells"],
  ["Shift + arrow / click", "Select a range"],
  ["Enter or F2", "Edit the selected cell"],
  ["Ctrl / ⌘ + Enter", "Save the cell text"],
  ["Escape", "Discard the current edit"],
  ["Page Up / Page Down", "Move a screen through the source"],
  ["?", "Open this list"],
];

export function ShortcutsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Keyboard shortcuts"
        description="The review grid is built to be used without leaving the keyboard."
        size="sm"
      >
        <dl className="shortcut-list">
          {shortcuts.map(([keys, description]) => (
            <div className="shortcut-row" key={keys}>
              <dt>
                {keys.split(" / ").map((key, index) => (
                  <span key={key}>
                    {index > 0 && <span className="shortcut-or">/</span>}
                    <Kbd>{key}</Kbd>
                  </span>
                ))}
              </dt>
              <dd>{description}</dd>
            </div>
          ))}
        </dl>
      </DialogContent>
    </Dialog>
  );
}
