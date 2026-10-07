import { List, MagnifyingGlass } from "@phosphor-icons/react";
import type { RefObject } from "react";

import Breadcrumbs, { type Breadcrumb } from "./Breadcrumbs";

type HeaderProps = {
  breadcrumbs: readonly Breadcrumb[];
  mobileTriggerRef: RefObject<HTMLButtonElement | null>;
  onNavigate: (href: string) => void;
  onOpenCommandMenu: () => void;
  onOpenMobileNavigation: () => void;
};

export default function Header({
  breadcrumbs,
  mobileTriggerRef,
  onNavigate,
  onOpenCommandMenu,
  onOpenMobileNavigation,
}: HeaderProps) {
  return (
    <header aria-label="Application header" className="app-header">
      <button
        aria-label="Open navigation"
        className="icon-button mobile-navigation-trigger"
        onClick={onOpenMobileNavigation}
        ref={mobileTriggerRef}
        type="button"
      >
        <List aria-hidden="true" weight="regular" />
      </button>
      <Breadcrumbs items={breadcrumbs} onNavigate={onNavigate} />
      <button className="command-trigger" onClick={onOpenCommandMenu} type="button">
        <MagnifyingGlass aria-hidden="true" weight="regular" />
        <span>Search or run a command</span>
        <kbd aria-label="Command K or Control K">⌘K</kbd>
      </button>
    </header>
  );
}
