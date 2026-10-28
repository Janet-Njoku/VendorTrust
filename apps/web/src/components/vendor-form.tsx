"use client";

import { useId, useState } from "react";

import type { Vendor, VendorStatus } from "@/lib/api/types";
import { useAction } from "@/lib/hooks";

import { useApi } from "./providers";
import { ActionError, Button, Field, INPUT, Modal } from "./ui";

/** The same rules the API enforces (1 to 200 characters, no control characters), for fast feedback. */
export function validateVendorName(raw: string): string | null {
  const name = raw.trim();
  if (name.length === 0) return "Enter the vendor's name.";
  if (name.length > 200) return "The name must be 200 characters or fewer.";
  if (/[\u0000-\u001f\u007f]/.test(name)) return "The name must not contain control characters.";
  return null;
}

export function VendorFormModal({
  vendor,
  onClose,
  onSaved,
}: {
  vendor?: Vendor;
  onClose: () => void;
  onSaved: (vendor: Vendor) => void;
}) {
  const api = useApi();
  const nameId = useId();
  const statusId = useId();
  const [name, setName] = useState(vendor?.name ?? "");
  const [status, setStatus] = useState<VendorStatus>(vendor?.status ?? "active");
  const [problem, setProblem] = useState<string | null>(null);
  const save = useAction(async () => {
    const body = { name: name.trim(), status };
    return vendor ? api.updateVendor(vendor.id, body) : api.createVendor(body);
  });

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const invalid = validateVendorName(name);
    setProblem(invalid);
    if (invalid) return;
    const saved = await save.run();
    if (saved) onSaved(saved);
  }

  const serverProblem = save.error?.fieldErrors.name;
  return (
    <Modal title={vendor ? "Edit vendor" : "Add vendor"} onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <Field label="Vendor name" htmlFor={nameId} error={problem ?? serverProblem}>
          <input
            id={nameId}
            className={INPUT}
            value={name}
            maxLength={250}
            onChange={(e) => setName(e.target.value)}
            aria-invalid={Boolean(problem ?? serverProblem)}
          />
        </Field>
        <Field label="Status" htmlFor={statusId} hint="Archived vendors keep their history but cannot be assessed.">
          <select
            id={statusId}
            className={INPUT}
            value={status}
            onChange={(e) => setStatus(e.target.value as VendorStatus)}
          >
            <option value="active">Active</option>
            <option value="archived">Archived</option>
          </select>
        </Field>
        {save.error && !serverProblem && (
          <div className="mt-4">
            <ActionError error={save.error} />
          </div>
        )}
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={save.pending}>
            {vendor ? "Save changes" : "Add vendor"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
