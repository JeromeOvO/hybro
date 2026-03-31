"use client"

import React from 'react';
import Link from 'next/link';
import { SidebarTrigger, useSidebar } from '@/components/ui/sidebar';
import { Logo } from '@/components/logo';
import { useUser } from '@clerk/nextjs';

export const DeveloperHeader = () => {
  const [mounted, setMounted] = React.useState(false);
  const { isMobile } = useSidebar();
  const { isLoaded, isSignedIn } = useUser();
  
  React.useEffect(() => {
    setMounted(true);
  }, []);

  const showAuthButtons = mounted && isLoaded && !isSignedIn;

  if (!mounted) {
    return (
      <header className="sticky top-0 z-40 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 md:hidden">
        <div className="flex h-14 items-center gap-4 px-4">
          <div className="flex items-center gap-2">
            <SidebarTrigger />
            <Logo size="sm" />
            <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Dev</span>
          </div>
          <div className="flex-1" />
        </div>
      </header>
    );
  }

  if (showAuthButtons) {
    return (
      <header className="sticky top-0 z-40 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 border-b border-border/40">
        <div className="flex h-14 items-center gap-6 px-6 max-w-6xl mx-auto">
          <Logo size="sm" />
          <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Dev</span>
          <div className="flex-1" />
          <Link
            href="/sign-in"
            className="text-sm text-muted-foreground hover:text-foreground transition-colors whitespace-nowrap py-2"
          >
            Sign in
          </Link>
          <Link
            href="/sign-up"
            className="text-sm font-medium px-4 py-1.5 rounded-md btn-brand-gradient whitespace-nowrap"
          >
            Get Started
          </Link>
        </div>
      </header>
    );
  }
  
  if (!isMobile) {
    return null;
  }
  
  return (
    <header className="sticky top-0 z-40 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="flex h-14 items-center gap-4 px-4">
        <div className="flex items-center gap-2">
          <SidebarTrigger />
          <Logo size="sm" />
          <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Dev</span>
        </div>
        <div className="flex-1" />
      </div>
    </header>
  );
};
