import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useId, type ReactNode, type RefObject } from "react";

export function Sheet({ title, description, open, onOpenChange, trigger, children, returnFocusRef }: {
  title: string; description?: string; open?: boolean; onOpenChange?: (open: boolean) => void;
  trigger?: ReactNode; children: ReactNode; returnFocusRef?: RefObject<HTMLElement | null>;
}) {
  const descriptionId = useId();
  return <Dialog.Root open={open} onOpenChange={onOpenChange}>
    {trigger && <Dialog.Trigger asChild>{trigger}</Dialog.Trigger>}
    <Dialog.Portal><Dialog.Overlay className="ui-sheet-overlay" />
      <Dialog.Content className="ui-sheet" aria-describedby={description ? descriptionId : undefined}
        onCloseAutoFocus={(event) => { if (returnFocusRef?.current) { event.preventDefault(); returnFocusRef.current.focus(); } }}>
        <header className="ui-sheet__heading"><Dialog.Title>{title}</Dialog.Title>
          <Dialog.Close className="ui-button ui-button--quiet" aria-label="关闭面板"><X size={20} /></Dialog.Close>
        </header>
        {description && <Dialog.Description id={descriptionId}>{description}</Dialog.Description>}
        <div className="ui-sheet__body">{children}</div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
