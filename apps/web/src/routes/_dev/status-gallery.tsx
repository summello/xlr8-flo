import StatusPill from "../../components/status/StatusPill";
import Surface from "../../components/ui/Surface";
import { DOC_TYPE_LABELS } from "../../components/status/labels";
import { DOC_TYPES, STATUS, type StatusSelection } from "../../components/status/map";

export default function StatusGallery() {
  return (
    <div className="status-gallery" data-testid="status-gallery">
      {DOC_TYPES.map((docType) => (
        <Surface as="section" aria-labelledby={`status-gallery-${docType}`} className="status-gallery-group" key={docType}>
          <h2 id={`status-gallery-${docType}`}>{DOC_TYPE_LABELS[docType]}</h2>
          <ul className="status-gallery-list">
            {Object.keys(STATUS[docType]).map((status) => {
              const selection = { docType, status } as StatusSelection;
              return (
                <li key={status}>
                  <StatusPill {...selection} />
                </li>
              );
            })}
          </ul>
        </Surface>
      ))}
    </div>
  );
}
