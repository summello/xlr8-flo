import { ArrowRight } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";

import CommandMenu from "../command/CommandMenu";
import {
  DEFAULT_PERMISSIONS,
  permittedCommands,
  type Command,
  type Phase,
} from "../command/registry";
import { isEditableTarget, useFocusOnRouteChange } from "../../lib/focus";
import Header from "./Header";
import Sidebar from "./Sidebar";
import SkipLink from "./SkipLink";
import type { Breadcrumb } from "./Breadcrumbs";

export type ShellRoute = {
  activeHref: string;
  breadcrumbs: readonly Breadcrumb[];
  description: string;
  path: string;
  phase: Phase;
  title: string;
};

type AppShellProps = {
  navigate: (href: string) => void;
  route: ShellRoute;
};

export default function AppShell({ navigate, route }: AppShellProps) {
  const [commandOpen, setCommandOpen] = useState(false);
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);
  const [commandReturnFocus, setCommandReturnFocus] = useState<HTMLElement | null>(null);
  const mainRef = useRef<HTMLElement>(null);
  const mobileTriggerRef = useRef<HTMLButtonElement>(null);
  const announcement = useFocusOnRouteChange(route.path, route.title, mainRef);

  const openCommandMenu = () => {
    setCommandReturnFocus(
      document.activeElement instanceof HTMLElement ? document.activeElement : null,
    );
    setCommandOpen(true);
  };

  const executeCommand = useCallback((command: Command) => navigate(command.href), [navigate]);

  useEffect(() => {
    const onShortcut = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (commandOpen) {
          event.preventDefault();
          event.stopImmediatePropagation();
          setCommandOpen(false);
          return;
        }
        if (mobileNavigationOpen) {
          event.preventDefault();
          event.stopImmediatePropagation();
          setMobileNavigationOpen(false);
          return;
        }
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === "k") {
        event.preventDefault();
        openCommandMenu();
        return;
      }
      if (
        commandOpen ||
        event.altKey ||
        event.ctrlKey ||
        event.metaKey ||
        event.repeat ||
        isEditableTarget(event.target)
      ) {
        return;
      }

      const command = permittedCommands(DEFAULT_PERMISSIONS).find(
        (candidate) => candidate.shortcut === event.key.toLocaleUpperCase(),
      );
      if (command !== undefined) {
        event.preventDefault();
        executeCommand(command);
      }
    };
    window.addEventListener("keydown", onShortcut, true);
    return () => window.removeEventListener("keydown", onShortcut, true);
  }, [commandOpen, executeCommand, mobileNavigationOpen]);

  return (
    <div className="app-shell" data-phase={route.phase}>
      <SkipLink />
      <Sidebar
        activeHref={route.activeHref}
        mobileOpen={mobileNavigationOpen}
        mobileTriggerRef={mobileTriggerRef}
        onMobileOpenChange={setMobileNavigationOpen}
        onNavigate={navigate}
      />
      <div className="app-workspace">
        <Header
          breadcrumbs={route.breadcrumbs}
          mobileTriggerRef={mobileTriggerRef}
          onNavigate={navigate}
          onOpenCommandMenu={openCommandMenu}
          onOpenMobileNavigation={() => setMobileNavigationOpen(true)}
        />
        <main aria-labelledby="page-title" id="main-content" ref={mainRef} tabIndex={-1}>
          <div className="page-heading">
            <p>{route.phase}</p>
            <h1 id="page-title">{route.title}</h1>
          </div>
          <section aria-labelledby="workspace-heading" className="empty-state material">
            <ArrowRight aria-hidden="true" weight="regular" />
            <div>
              <h2 id="workspace-heading">Shell route ready</h2>
              <p>{route.description}</p>
            </div>
          </section>
        </main>
      </div>
      <p aria-atomic="true" aria-live="polite" className="visually-hidden" data-testid="route-announcer">
        {announcement}
      </p>
      <CommandMenu
        onOpenChange={setCommandOpen}
        onSelect={executeCommand}
        open={commandOpen}
        permissions={DEFAULT_PERMISSIONS}
        returnFocusTo={commandReturnFocus}
      />
    </div>
  );
}
