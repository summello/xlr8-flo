import AmendEditor from "../../components/requisitions/changes/AmendEditor";
import "../../styles/projects.css";
import "../../styles/requisitions.css";

export default function AmendRequisition({ id, navigate, onLoaded }: { id: string; navigate: (href: string) => void; onLoaded?: (title: string) => void }) {
  return <AmendEditor id={id} navigate={navigate} onLoaded={onLoaded} />;
}
