import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  className,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-[1000] bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <D.Content
          className={cn(
            "fixed left-1/2 top-1/2 z-[1001] max-h-[90vh] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border bg-white p-5 shadow-lg data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
            className,
          )}
        >
          <D.Title className="pr-6 text-base font-semibold">{title}</D.Title>
          {description ? <D.Description className="mt-1 text-sm text-muted-foreground">{description}</D.Description> : <D.Description className="sr-only">{title}</D.Description>}
          <div className="mt-4">{children}</div>
          <D.Close className="absolute right-4 top-4 rounded p-1 text-muted-foreground hover:bg-secondary" aria-label="Close">
            <X className="h-4 w-4" />
          </D.Close>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
