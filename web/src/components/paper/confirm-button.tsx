"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";

/** Two clicks for anything that sells: the first arms it ("Confirm…" for 4 s),
    the second runs it. */
export function ConfirmButton({
  children,
  confirmLabel,
  onConfirm,
  disabled,
  title,
  size = "sm",
  variant = "outline",
}: {
  children: React.ReactNode;
  confirmLabel: React.ReactNode;
  onConfirm: () => void;
  disabled?: boolean;
  title?: string;
  size?: "xs" | "sm" | "default";
  variant?: "outline" | "destructive" | "ghost";
}) {
  const [armed, setArmed] = useState(false);

  useEffect(() => {
    if (!armed) return;
    const timer = setTimeout(() => setArmed(false), 4_000);
    return () => clearTimeout(timer);
  }, [armed]);

  return (
    <Button
      size={size}
      variant={armed ? "destructive" : variant}
      disabled={disabled}
      title={title}
      onClick={() => {
        if (armed) {
          setArmed(false);
          onConfirm();
        } else {
          setArmed(true);
        }
      }}
    >
      {armed ? confirmLabel : children}
    </Button>
  );
}
