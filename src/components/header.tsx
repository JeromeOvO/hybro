"use client"

import React from 'react';
import { SidebarTrigger, useSidebar } from '@/components/ui/sidebar';
import { Logo } from '@/components/logo';

export const Header = () => {
  const [mounted, setMounted] = React.useState(false);
  const { isMobile } = useSidebar();
  
  React.useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    // Return a simple header while loading to avoid hydration issues
    return (
      <header className="sticky top-0 z-40 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="flex h-14 items-center gap-4 px-4">
          <div className="flex items-center gap-2">
            <SidebarTrigger className="md:hidden" />
            <Logo size="sm" className="md:hidden" />
          </div>
          <div className="flex-1" />
        </div>
      </header>
    );
  }
  
  return (
    <header className="sticky top-0 z-40 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="flex h-14 items-center gap-4 px-4">
        <div className="flex items-center gap-2">
          {/* Only show on mobile */}
          {isMobile && (
            <>
              <SidebarTrigger />
              <Logo size="sm" />
            </>
          )}
        </div>
        <div className="flex-1" />
      </div>
    </header>
  );
};