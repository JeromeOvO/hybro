"use client"

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { SidebarTrigger, useSidebar } from '@/components/ui/sidebar';
import { Logo } from '@/components/logo';
import { useUser } from '@/lib/auth';
import { cn } from '@/lib/utils';

const MARKETING_PAGES = ['/', '/about', '/pricing', '/agents', '/c', '/c/about', '/c/pricing', '/c/agents']

export const ConsumerHeader = () => {
  const [mounted, setMounted] = React.useState(false);
  const { isMobile } = useSidebar();
  const { isSignedIn, isLoaded } = useUser();
  const pathname = usePathname();
  
  React.useEffect(() => {
    setMounted(true);
  }, []);

  const isMarketingPage = MARKETING_PAGES.includes(pathname);
  const isUnauthenticated = mounted && isLoaded && !isSignedIn && isMarketingPage;

  if (!mounted) {
    return (
      <header className="sticky top-0 z-40 bg-background/35 backdrop-blur-xl md:hidden">
        <div className="flex h-14 items-center gap-4 border-b border-border/30 px-4">
          <div className="flex items-center gap-2">
            <SidebarTrigger />
            <Logo />
          </div>
          <div className="flex-1" />
        </div>
      </header>
    );
  }

  if (isUnauthenticated) {
    return (
      <header className="sticky top-0 z-40 bg-background/35 backdrop-blur-xl">
        <div className="border-b border-border/30">
          <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4 sm:px-8">
            <Logo />
            <nav className="hidden sm:flex items-center gap-1 ml-4">
              {[
                { href: '/agents', label: 'Explore' },
                { href: '/about', label: 'About' },
              ].map(link => (
                <Link
                  key={link.href}
                  href={link.href}
                  className={cn(
                    "px-3 py-2.5 text-sm rounded-md transition-colors",
                    pathname === link.href
                      ? "text-foreground font-medium bg-muted/45"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted/25"
                  )}
                >
                  {link.label}
                </Link>
              ))}
            </nav>
            <div className="flex-1" />
            <Link
              href="/sign-in"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors whitespace-nowrap py-2.5"
            >
              Sign in
            </Link>
            <Link
              href="/sign-up"
              className="text-sm font-medium px-4 py-2.5 rounded-md btn-brand-gradient whitespace-nowrap shadow-sm shadow-[hsl(var(--color-hybro-hy)/0.12)]"
            >
              Get Started
            </Link>
          </div>
        </div>
      </header>
    );
  }
  
  if (!isMobile) {
    return null;
  }
  
  return (
    <header className="sticky top-0 z-40 bg-background/35 backdrop-blur-xl">
      <div className="flex h-14 items-center gap-4 border-b border-border/30 px-4">
        <div className="flex items-center gap-2">
          <SidebarTrigger />
          <Logo />
        </div>
        <div className="flex-1" />
      </div>
    </header>
  );
};
