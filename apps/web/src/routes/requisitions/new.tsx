import RequisitionForm from "../../components/requisitions/RequisitionForm";
import "../../styles/projects.css";
import "../../styles/requisitions.css";

export default function NewRequisition({ navigate }: { navigate: (href: string) => void }) {
  return <RequisitionForm navigate={navigate} />;
}
