"use client";

import * as React from "react";
import Script from "next/script";
import { UserPlus } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface WaitlistDialogProps {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  showTrigger?: boolean;
}

export function WaitlistDialog({ open, onOpenChange, showTrigger = true }: WaitlistDialogProps) {
  const launchlistKey = process.env.NEXT_PUBLIC_LAUNCHLIST_KEY || "6xqgWJ";
  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        {showTrigger && (
          <DialogTrigger asChild>
            <Button variant="ghost" className="w-full justify-start gap-2">
              <UserPlus className="h-4 w-4" />
              Join Waitlist
            </Button>
          </DialogTrigger>
        )}
        <DialogContent className="sm:max-w-md bg-black border shadow-lg">
          <DialogHeader>
            <DialogTitle>Join Our Waitlist</DialogTitle>
            <DialogDescription>
              Be the first to know when we launch new features.
            </DialogDescription>
          </DialogHeader>
          <form
            action={`https://getlaunchlist.com/s/${launchlistKey}`}
            method="POST"
            className="space-y-4"
          >
            <div className="space-y-2">
              <Label htmlFor="name">Name</Label>
              <Input
                id="name"
                name="name"
                type="text"
                placeholder="Enter your name"
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                name="email"
                type="email"
                placeholder="Enter your email"
                required
              />
            </div>
            <Button type="submit" className="w-full">
              Join Waitlist
            </Button>
          </form>
        </DialogContent>
      </Dialog>

      <Script
        src="https://getlaunchlist.com/js/widget-diy.js"
        strategy="afterInteractive"
      />
    </>
  );
}
