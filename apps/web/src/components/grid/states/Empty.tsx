import { FolderOpen } from "@phosphor-icons/react";

import Surface from "../../ui/Surface";

type GridEmptyProps = {
  actionLabel: string;
  message: string;
  onAction: () => void;
};

export default function GridEmpty({ actionLabel, message, onAction }: GridEmptyProps) {
  return (
    <Surface className="grid-state grid-empty-state">
      <FolderOpen aria-hidden="true" weight="regular" />
      <p>{message}</p>
      <button onClick={onAction} type="button">
        {actionLabel}
      </button>
    </Surface>
  );
}
