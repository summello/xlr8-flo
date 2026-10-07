import type { GridDensity } from "../../lib/grid-preferences";

type DensityToggleProps = {
  density: GridDensity;
  onChange: (density: GridDensity) => void;
};

export default function DensityToggle({ density, onChange }: DensityToggleProps) {
  return (
    <div aria-label="Grid density" className="grid-density-toggle" role="group">
      {(["comfortable", "compact"] as const).map((option) => (
        <button
          aria-pressed={density === option}
          key={option}
          onClick={() => onChange(option)}
          type="button"
        >
          {option === "comfortable" ? "Comfortable" : "Compact"}
        </button>
      ))}
    </div>
  );
}
