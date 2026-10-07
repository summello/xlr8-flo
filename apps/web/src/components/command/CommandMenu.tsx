import * as Dialog from "@radix-ui/react-dialog";
import { MagnifyingGlass } from "@phosphor-icons/react";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";

import { restoreFocus } from "../../lib/focus";
import {
  fuzzyMatch,
  permittedCommands,
  searchCommands,
  type Command,
  type CommandGroup,
  type Permission,
} from "./registry";

type CommandMenuProps = {
  onOpenChange: (open: boolean) => void;
  onSelect: (command: Command) => void;
  open: boolean;
  permissions: ReadonlySet<Permission>;
  returnFocusTo: HTMLElement | null;
};

type CommandSection = {
  commands: Command[];
  label: "Recent" | CommandGroup;
};

const GROUPS: readonly CommandGroup[] = ["Records", "Actions", "Navigation"];

function HighlightedLabel({ label, query }: { label: string; query: string }) {
  const matched = new Set(fuzzyMatch(query, label)?.indices ?? []);
  return (
    <>
      {[...label].map((character, index) =>
        matched.has(index) ? (
          <mark key={`${character}-${index}`}>{character}</mark>
        ) : (
          <Fragment key={`${character}-${index}`}>{character}</Fragment>
        ),
      )}
    </>
  );
}

export default function CommandMenu({
  onOpenChange,
  onSelect,
  open,
  permissions,
  returnFocusTo,
}: CommandMenuProps) {
  const [query, setQuery] = useState("");
  const [showDestructive, setShowDestructive] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const restoreOnClose = useRef(true);

  const sections = useMemo<CommandSection[]>(() => {
    const visible = searchCommands(permittedCommands(permissions, showDestructive), query);
    if (query.trim().length === 0) {
      const recent = visible.filter((command) => command.recent);
      const remaining = visible.filter((command) => !command.recent);
      return [
        { commands: recent, label: "Recent" },
        ...GROUPS.map((group) => ({
          commands: remaining.filter((command) => command.group === group),
          label: group,
        })),
      ].filter((section): section is CommandSection => section.commands.length > 0);
    }
    return GROUPS.map((group) => ({
      commands: visible.filter((command) => command.group === group),
      label: group,
    })).filter((section) => section.commands.length > 0);
  }, [permissions, query, showDestructive]);

  const results = sections.flatMap((section) => section.commands);
  const selectedIndex = Math.min(activeIndex, Math.max(results.length - 1, 0));

  useEffect(() => {
    if (!open) return;

    const onModifier = (event: KeyboardEvent) => {
      if (event.key === "Alt") setShowDestructive(event.type === "keydown");
    };
    window.addEventListener("keydown", onModifier);
    window.addEventListener("keyup", onModifier);
    return () => {
      window.removeEventListener("keydown", onModifier);
      window.removeEventListener("keyup", onModifier);
    };
  }, [open]);

  const changeOpen = (nextOpen: boolean) => {
    if (!nextOpen) {
      setActiveIndex(0);
      setQuery("");
      setShowDestructive(false);
    } else {
      restoreOnClose.current = true;
    }
    onOpenChange(nextOpen);
  };

  const select = (command: Command) => {
    restoreOnClose.current = false;
    changeOpen(false);
    window.setTimeout(() => onSelect(command), 0);
  };

  return (
    <Dialog.Root onOpenChange={changeOpen} open={open}>
      <Dialog.Portal>
        <Dialog.Overlay className="command-overlay" />
        <Dialog.Content
          aria-describedby="command-description"
          className="command-menu glass-command-palette material-shadow"
          data-shadow="xl"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            if (restoreOnClose.current) restoreFocus(returnFocusTo);
          }}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            restoreOnClose.current = true;
            setActiveIndex(0);
            setQuery("");
            setShowDestructive(false);
            inputRef.current?.focus();
          }}
        >
          <Dialog.Title className="visually-hidden">Command menu</Dialog.Title>
          <Dialog.Description className="visually-hidden" id="command-description">
            Search permitted records, actions, and navigation destinations.
          </Dialog.Description>
          <label className="command-search">
            <MagnifyingGlass aria-hidden="true" weight="regular" />
            <span className="visually-hidden">Search commands</span>
            <input
              aria-activedescendant={results[selectedIndex] ? `command-${results[selectedIndex].id}` : undefined}
              aria-controls="command-results"
              aria-expanded="true"
              aria-label="Search commands"
              autoComplete="off"
              onChange={(event) => {
                setQuery(event.target.value);
                setActiveIndex(0);
              }}
              onKeyDown={(event) => {
                if (event.key === "ArrowDown") {
                  event.preventDefault();
                  setActiveIndex((current) => (current + 1) % Math.max(results.length, 1));
                } else if (event.key === "ArrowUp") {
                  event.preventDefault();
                  setActiveIndex(
                    (current) => (current - 1 + Math.max(results.length, 1)) % Math.max(results.length, 1),
                  );
                } else if (event.key === "Enter") {
                  const command = results[selectedIndex];
                  if (command !== undefined) {
                    event.preventDefault();
                    select(command);
                  }
                }
              }}
              placeholder="Find a record or action"
              ref={inputRef}
              role="combobox"
              type="search"
              value={query}
            />
          </label>

          <div aria-label="Command results" className="command-results" id="command-results" role="listbox">
            {sections.map((section) => (
              <section aria-labelledby={`command-group-${section.label}`} key={section.label}>
                <h2 id={`command-group-${section.label}`}>{section.label}</h2>
                {section.commands.map((command) => {
                  const index = results.indexOf(command);
                  const Icon = command.icon;
                  return (
                    <button
                      aria-selected={index === selectedIndex}
                      className="command-result"
                      data-phase={command.phase}
                      id={`command-${command.id}`}
                      key={command.id}
                      onClick={() => select(command)}
                      onMouseMove={() => setActiveIndex(index)}
                      role="option"
                      type="button"
                    >
                      <Icon aria-hidden="true" weight="regular" />
                      <span>
                        <HighlightedLabel label={command.label} query={query} />
                      </span>
                      {command.destructive ? <span className="command-modifier">Alt</span> : null}
                      {command.shortcut ? <kbd>{command.shortcut}</kbd> : null}
                    </button>
                  );
                })}
              </section>
            ))}
            {results.length === 0 ? <p className="command-empty">No permitted commands match.</p> : null}
          </div>
          <p aria-live="polite" className="command-footer">
            {results.length} {results.length === 1 ? "result" : "results"}. Hold Alt for destructive commands.
          </p>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
