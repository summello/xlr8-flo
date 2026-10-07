import {
  ArrowLeft,
  ArrowRight,
  ArrowsHorizontal,
  DotsThreeVertical,
  EyeSlash,
  PushPin,
} from "@phosphor-icons/react";

type ColumnMenuProps = {
  canHide: boolean;
  canMoveNext: boolean;
  canMovePrevious: boolean;
  isPinned: boolean;
  label: string;
  onHide: () => void;
  onMoveNext: () => void;
  onMovePrevious: () => void;
  onPin: () => void;
  onResize: (direction: -1 | 1) => void;
};

export default function ColumnMenu({
  canHide,
  canMoveNext,
  canMovePrevious,
  isPinned,
  label,
  onHide,
  onMoveNext,
  onMovePrevious,
  onPin,
  onResize,
}: ColumnMenuProps) {
  return (
    <details className="grid-column-menu">
      <summary aria-label={`Column options for ${label}`}>
        <DotsThreeVertical aria-hidden="true" weight="regular" />
      </summary>
      <div className="grid-column-menu-panel material-shadow" data-shadow="md" role="menu">
        <button disabled={!canMovePrevious} onClick={onMovePrevious} role="menuitem" type="button">
          <ArrowLeft aria-hidden="true" weight="regular" />
          Move earlier
        </button>
        <button disabled={!canMoveNext} onClick={onMoveNext} role="menuitem" type="button">
          <ArrowRight aria-hidden="true" weight="regular" />
          Move later
        </button>
        <button onClick={onPin} role="menuitem" type="button">
          <PushPin aria-hidden="true" weight="regular" />
          {isPinned ? "Unpin" : "Pin left"}
        </button>
        <button onClick={() => onResize(-1)} role="menuitem" type="button">
          <ArrowsHorizontal aria-hidden="true" weight="regular" />
          Narrower
        </button>
        <button onClick={() => onResize(1)} role="menuitem" type="button">
          <ArrowsHorizontal aria-hidden="true" weight="regular" />
          Wider
        </button>
        <button disabled={!canHide} onClick={onHide} role="menuitem" type="button">
          <EyeSlash aria-hidden="true" weight="regular" />
          Hide column
        </button>
      </div>
    </details>
  );
}
