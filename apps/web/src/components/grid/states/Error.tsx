import { WarningCircle } from "@phosphor-icons/react";

import Surface from "../../ui/Surface";

type GridErrorProps = {
  cause: string;
  onRetry: () => void;
  preserved: string;
};

export default function GridError({ cause, onRetry, preserved }: GridErrorProps) {
  return (
    <Surface className="grid-state grid-error-state" role="alert">
      <WarningCircle aria-hidden="true" weight="regular" />
      <div>
        <p>Records could not be loaded. {cause}</p>
        <p>{preserved}</p>
      </div>
      <button onClick={onRetry} type="button">
        Retry
      </button>
    </Surface>
  );
}
