import { useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { Building2, ChevronDown, ListChecks, LogOut, WifiOff } from "lucide-react";
import { Badge, ErrorPanel } from "../components/ui";
import { Button } from "../components/ui/button";
import { Logo } from "../components/Logo";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import { getErrorMessage } from "../lib/api";
import { useOnlineStatus } from "../lib/hooks";
import { cn } from "../lib/utils";
import type { DataMode, Organization, SessionUser } from "../lib/types";

export function Shell({
  user,
  organization,
  sourceMode,
  onLogout,
  onSwitchOrganization,
}: {
  user: SessionUser;
  organization: Organization;
  sourceMode?: DataMode;
  onLogout: () => Promise<void>;
  onSwitchOrganization: () => void;
}) {
  const [error, setError] = useState<string>();
  const [pending, setPending] = useState(false);
  const online = useOnlineStatus();
  const { pathname } = useLocation();
  const projectSection = ["/projects", "/imports", "/knowledge"].some((path) =>
    pathname.startsWith(path),
  );
  const navigation = [
    { label: "Decisions", href: "/overview", icon: ListChecks, active: !projectSection },
    { label: "Projects", href: "/projects", icon: Building2, active: projectSection },
  ];

  async function signOut() {
    setPending(true);
    setError(undefined);
    try {
      await onLogout();
    } catch (cause) {
      setError(getErrorMessage(cause, "Could not sign out. Please try again."));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="min-h-screen bg-background">
      <a className="sr-only focus:not-sr-only" href="#content">
        Skip to content
      </a>
      <header className="sticky top-0 z-20 border-b border-border/70 bg-background/95 backdrop-blur">
        <div className="mx-auto flex min-h-18 min-w-0 w-full max-w-[1440px] items-center gap-2 px-5 sm:gap-3 sm:px-8 lg:px-12">
          <Link
            to="/overview"
            className="flex shrink-0 items-center"
            aria-label="IntelliQ home"
          >
            <span className="hidden sm:block">
              <Logo />
            </span>
            <span className="sm:hidden">
              <Logo mark />
            </span>
          </Link>

          <nav
            aria-label="Main navigation"
            className="ml-5 hidden self-stretch items-stretch gap-6 sm:flex"
          >
            {navigation.map((item) => (
              <Link
                key={item.href}
                to={item.href}
                aria-current={item.active ? "page" : undefined}
                className={cn(
                  "flex items-center border-b-2 border-transparent px-1 text-sm font-medium text-muted-foreground hover:text-foreground",
                  item.active && "border-primary text-foreground",
                )}
              >
                {item.label}
              </Link>
            ))}
          </nav>

          <div className="ml-auto flex shrink-0 items-center gap-1 sm:gap-3">
            {sourceMode === "fixture" && (
              <Badge className="hidden sm:inline-flex" tone="fixture">
                Demo data
              </Badge>
            )}
            {!online && (
              <span className="hidden items-center gap-1.5 text-xs text-amber-700 sm:flex">
                <WifiOff className="size-3.5" /> Offline · read-only
              </span>
            )}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  className="flex min-h-11 items-center gap-1.5 rounded-full px-2 text-sm hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring sm:gap-2 sm:px-2.5"
                  aria-label="Account menu"
                >
                  <span className="hidden max-w-40 truncate md:block">
                    {user.name || user.email}
                  </span>
                  <span className="flex size-8 items-center justify-center rounded-full bg-secondary font-semibold text-secondary-foreground">
                    {(user.name || user.email || "IQ").slice(0, 1).toUpperCase()}
                  </span>
                  <ChevronDown className="hidden size-4 text-muted-foreground sm:block" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-52">
                <DropdownMenuLabel className="max-w-52 truncate">
                  {user.email || user.name || "Signed in"}
                  <span className="mt-1 block truncate text-foreground">
                    {organization.name}
                  </span>
                </DropdownMenuLabel>
                <DropdownMenuItem onSelect={onSwitchOrganization}>
                  <Building2 className="size-4 text-muted-foreground" />
                  Switch organization
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem disabled={pending} onSelect={() => void signOut()}>
                  <LogOut className="size-4 text-muted-foreground" />
                  {pending ? "Signing out…" : "Sign out"}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </header>
      <main
        id="content"
        className="mx-auto min-w-0 max-w-[1440px] px-5 pt-8 pb-[calc(6rem+env(safe-area-inset-bottom))] sm:px-8 sm:py-10 lg:px-12 lg:py-14"
        tabIndex={-1}
      >
        {error && <ErrorPanel message={error} onRetry={signOut} />}
        <Outlet />
      </main>
      <nav
        aria-label="Mobile navigation"
        className="fixed inset-x-0 bottom-0 z-30 flex border-t border-border bg-background pb-[env(safe-area-inset-bottom)] sm:hidden"
      >
        {navigation.map((item) => (
          <Link
            key={item.href}
            to={item.href}
            aria-current={item.active ? "page" : undefined}
            className={cn(
              "flex min-h-16 flex-1 flex-col items-center justify-center gap-1 text-xs font-medium text-muted-foreground",
              item.active && "text-primary",
            )}
          >
            <item.icon className="size-5" />
            {item.label}
          </Link>
        ))}
      </nav>
    </div>
  );
}
