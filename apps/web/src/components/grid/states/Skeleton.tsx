import Surface from "../../ui/Surface";

type GridSkeletonProps = {
  columnCount: number;
  rowCount?: number;
};

export default function GridSkeleton({ columnCount, rowCount = 12 }: GridSkeletonProps) {
  return (
    <Surface aria-label="Loading grid" className="grid-skeleton" role="status">
      {Array.from({ length: rowCount }, (_, rowIndex) => (
        <div className="grid-skeleton-row" data-testid="grid-skeleton-row" key={rowIndex}>
          {Array.from({ length: columnCount }, (_, columnIndex) => (
            <span aria-hidden="true" key={columnIndex} />
          ))}
        </div>
      ))}
      <span className="visually-hidden">Loading records</span>
    </Surface>
  );
}
