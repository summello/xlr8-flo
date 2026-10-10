import ProjectForm from "../../components/projects/ProjectForm";
import "../../styles/projects.css";
export default function NewProject({ navigate }: { navigate: (href: string) => void }) {
  return <section className="projects"><ProjectForm onCreated={id => navigate(`/projects/${id}`)} /></section>;
}
