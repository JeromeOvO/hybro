"use client"

import React from 'react';
import { SidebarTrigger, useSidebar } from '@/components/ui/sidebar';
import { Logo } from '@/components/logo';

export const DeveloperHeader = () => {
  const [mounted, setMounted] = React.useState(false);
  const { isMobile } = useSidebar();
  
  React.useEffect(() => {
    setMounted(true);
  }, []);

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
