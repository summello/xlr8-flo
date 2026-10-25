import Editor from "../../../components/sourcing/editor/Editor";
import "../../../styles/projects.css";
import "../../../styles/requisitions.css";
import "../../../styles/vendors.css";
import "../../../styles/sourcing.css";

export default function EditRfq({ id, navigate }: { id: string; navigate: (href: string) => void }) {
  return <Editor id={id} mode="edit" navigate={navigate} />;
}
