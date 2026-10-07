import { FloppyDisk, Trash } from "@phosphor-icons/react";
import { useId, useState, type FormEvent } from "react";

import type { GridViewDefinition } from "../../lib/grid-preferences";

type SavedViewsProps = {
  onApply: (view: GridViewDefinition) => void;
  onDelete: (id: string) => void;
  onSave: (name: string) => void;
  views: readonly GridViewDefinition[];
};

export default function SavedViews({ onApply, onDelete, onSave, views }: SavedViewsProps) {
  const [name, setName] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const nameId = useId();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (trimmed === "") {
      setError("Name the view before saving it. Your current grid settings are preserved.");
      return;
    }
    onSave(trimmed);
    setName("");
    setError(null);
  };

  return (
    <div className="grid-saved-views">
      <div className="grid-view-picker">
        <label htmlFor={`${nameId}-select`}>Saved view</label>
        <select
          id={`${nameId}-select`}
          onChange={(event) => {
            const id = event.target.value;
            setSelectedId(id);
            const view = views.find((candidate) => candidate.id === id);
            if (view !== undefined) onApply(view);
          }}
          value={selectedId}
        >
          <option value="">Current settings</option>
          {views.map((view) => (
            <option key={view.id} value={view.id}>
              {view.name}
            </option>
          ))}
        </select>
        <button
          aria-label="Delete selected personal view"
          className="grid-icon-action"
          disabled={selectedId === ""}
          onClick={() => {
            if (selectedId === "") return;
            onDelete(selectedId);
            setSelectedId("");
          }}
          type="button"
        >
          <Trash aria-hidden="true" weight="regular" />
        </button>
      </div>
      <form className="grid-view-save" onSubmit={submit}>
        <label htmlFor={nameId}>View name</label>
        <input
          aria-describedby={error === null ? undefined : `${nameId}-error`}
          aria-invalid={error === null ? undefined : true}
          id={nameId}
          onChange={(event) => setName(event.target.value)}
          placeholder="Name this view"
          value={name}
        />
        <button type="submit">
          <FloppyDisk aria-hidden="true" weight="regular" />
          Save view
        </button>
        {error === null ? undefined : (
          <p className="grid-field-error" id={`${nameId}-error`} role="alert">
            {error}
          </p>
        )}
      </form>
    </div>
  );
}
