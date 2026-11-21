import VendorRequestForm from "../../components/vendors/request/VendorRequestForm";

export default function NewVendor({ navigate }: { navigate: (href: string) => void }) {
  return <VendorRequestForm navigate={navigate} />;
}
