import VendorRequestForm from "../../components/vendors/request/VendorRequestForm";

export default function EditVendor({ id, navigate }: { id: string; navigate: (href: string) => void }) {
  return <VendorRequestForm id={id} navigate={navigate} />;
}
