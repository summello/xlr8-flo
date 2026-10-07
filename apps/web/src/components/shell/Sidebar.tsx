import * as Dialog from "@radix-ui/react-dialog";
import { X } from "@phosphor-icons/react";
import { useRef, type RefObject } from "react";

import { NAVIGATION_PHASES } from "../command/registry";
import { restoreFocus } from "../../lib/focus";

type NavigationProps = {
  activeHref: string;
  onNavigate: (href: string) => void;
};

type SidebarProps = NavigationProps & {
  mobileOpen: boolean;
  mobileTriggerRef: RefObject<HTMLButtonElement | null>;
  onMobileOpenChange: (open: boolean) => void;
};

function Navigation({ activeHref, onNavigate }: NavigationProps) {
  return (
    <nav aria-label="Lifecycle modules" className="module-navigation">
      {NAVIGATION_PHASES.map((phase) => (
        <section className="navigation-phase" data-phase={phase.id} key={phase.id}>
          <h2>{phase.label}</h2>
          <ul>
            {phase.items.map((item) => {
              const Icon = item.icon;
              const active = activeHref === item.href;
              return (
                <li key={item.id}>
                  <a
                    aria-current={active ? "page" : undefined}
                    href={item.href}
                    onClick={(event) => {
                      event.preventDefault();
                      onNavigate(item.href);
                    }}
                    title={item.label}
                  >
                    <Icon aria-hidden="true" className="nav-icon" weight="regular" />
                    <span>{item.label}</span>
                  </a>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </nav>
  );
}

function SidebarBody(props: NavigationProps) {
  return (
    <>
      <a className="product-mark" href="/projects" onClick={(event) => {
        event.preventDefault();
        props.onNavigate("/projects");
      }}>
        <span aria-hidden="true">XF</span>
        <strong>XLR8 FLO</strong>
      </a>
      <Navigation {...props} />
    </>
  );
}

export default function Sidebar({
  activeHref,
  mobileOpen,
  mobileTriggerRef,
  onMobileOpenChange,
  onNavigate,
}: SidebarProps) {
  const navigationProps = { activeHref, onNavigate };
  const closeReason = useRef<"dismiss" | "navigation">("dismiss");

  return (
    <>
      <aside aria-label="Primary sidebar" className="desktop-sidebar">
        <SidebarBody {...navigationProps} />
      </aside>

      <Dialog.Root
        onOpenChange={(open) => {
          if (open) closeReason.current = "dismiss";
          onMobileOpenChange(open);
        }}
        open={mobileOpen}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="drawer-overlay glass-sheet-backdrop" />
          <Dialog.Content
            aria-describedby="mobile-navigation-description"
            className="mobile-drawer material-shadow"
            data-shadow="xl"
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              if (closeReason.current === "dismiss") restoreFocus(mobileTriggerRef.current);
            }}
          >
            <aside aria-label="Mobile primary sidebar" className="mobile-sidebar">
              <Dialog.Title className="visually-hidden">Navigation</Dialog.Title>
              <Dialog.Description className="visually-hidden" id="mobile-navigation-description">
                Navigate between capital lifecycle modules.
              </Dialog.Description>
              <Dialog.Close aria-label="Close navigation" className="icon-button drawer-close">
                <X aria-hidden="true" weight="regular" />
              </Dialog.Close>
              <SidebarBody
                activeHref={activeHref}
                onNavigate={(href) => {
                  closeReason.current = "navigation";
                  onNavigate(href);
                  onMobileOpenChange(false);
                }}
              />
            </aside>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
