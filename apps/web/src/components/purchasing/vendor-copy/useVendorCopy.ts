import { apiClient } from "../../../api/client";
import { failure, type Problem } from "../../requisitions/api";
import { useLoad } from "../../requisitions/useLoad";

export const NO_VENDOR_COPY = "This purchase order has no vendor copy: it does not exist, you cannot see it, or it was cancelled before it was issued.";

export function useVendorCopy(id: string) {
  return useLoad(async () => {
    const { data, error, response } = await apiClient.GET("/api/v1/purchase-orders/{id}/vendor-copy", { params: { path: { id } } });
    if (!data) throw new Error(response?.status === 404 ? NO_VENDOR_COPY : failure(error as Problem | undefined, "Loading the vendor copy"));
    return data;
  }, [id]);
}
