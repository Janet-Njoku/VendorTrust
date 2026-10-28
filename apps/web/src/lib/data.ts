"use client";

import { useApi } from "@/components/providers";

import { fetchAll } from "./api/client";
import type { Assessment, Vendor } from "./api/types";
import { type Query, useQuery } from "./hooks";

export interface Portfolio {
  vendors: Vendor[];
  assessments: Assessment[];
}

/** Every vendor and every assessment, read through the typed client (the API pages at 100 rows). */
export function usePortfolio(): Query<Portfolio> {
  const api = useApi();
  return useQuery(async () => {
    const [vendors, assessments] = await Promise.all([
      fetchAll((p) => api.listVendors(p)),
      fetchAll((p) => api.listAssessments(p)),
    ]);
    return { vendors, assessments };
  }, "portfolio");
}
