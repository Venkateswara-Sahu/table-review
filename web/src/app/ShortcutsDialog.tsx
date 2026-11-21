import { Dialog, DialogContent } from "../ui/dialog";
import { Kbd } from "../ui/primitives";

const shortcuts: Array<[string, string]> = [
  ["Arrow keys", "Move between cells"],
  ["Shift + arrow / click", "Select a range"],
  ["Double-click / Enter / F2", "Edit cell inline"],
  ["Tab / Shift + Tab", "Save inline edit & move next"],
  ["Ctrl / ⌘ + Enter", "Confirm the value — saves the edit first"],
  ["Ctrl / ⌘ + C", "Copy selected cells as TSV"],
  ["Escape", "Discard or cancel current edit"],
  ["Ctrl / ⌘ + Z", "Undo last table change"],
  ["Ctrl / ⌘ + Shift + Z", "Redo undone change"],
  ["E", "Jump to next open exception"],
  ["N", "Next unchecked cell"],
  ["Ctrl / ⌘ + F", "Find text across the PDF"],
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
