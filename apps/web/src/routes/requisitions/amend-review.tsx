import ReviewScreen from "../../components/requisitions/changes/ReviewScreen";
import "../../styles/projects.css";
import "../../styles/requisitions.css";

export default function AmendReview({ id, navigate, onLoaded }: { id: string; navigate: (href: string) => void; onLoaded?: (title: string) => void }) {
  return <ReviewScreen id={id} kind="amend" navigate={navigate} onLoaded={onLoaded} />;
}
