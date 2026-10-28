/**
 * DEMO MODE ONLY. A copy of the backend's starter questionnaire (apps/api/app/questionnaire_starter.py)
 * so the demo's "create from starter" behaves like the real endpoint. The real UI never reads this:
 * against the real API the starter is created by `POST /questionnaires/from-starter`.
 */

export interface StarterOption {
  label: string;
  risk_points: number | null;
}
export interface StarterQuestion {
  category: string;
  prompt: string;
  weight: number;
  options: StarterOption[];
}

const YES_NO: StarterOption[] = [
  { label: "Yes", risk_points: 0 },
  { label: "No", risk_points: 100 },
  { label: "Not applicable", risk_points: null },
];
const MATURITY: StarterOption[] = [
  { label: "Fully implemented", risk_points: 0 },
  { label: "Partially implemented", risk_points: 50 },
  { label: "Not implemented", risk_points: 100 },
  { label: "Not applicable", risk_points: null },
];

export const STARTER_NAME = "Security due diligence (starter)";
export const STARTER_DESCRIPTION =
  "A 15-question baseline covering access control, MFA, encryption, vulnerability management, incident response, business continuity, data protection and vendor governance.";

export const STARTER_QUESTIONS: StarterQuestion[] = [
  { category: "access_control", weight: 4, options: MATURITY, prompt: "Is access to systems and customer data granted on a least-privilege, role-based basis?" },
  { category: "access_control", weight: 3, options: MATURITY, prompt: "Are user access rights reviewed at least quarterly and removed promptly when roles change or people leave?" },
  { category: "mfa", weight: 5, options: YES_NO, prompt: "Is multi-factor authentication required for all administrative and remote access?" },
  { category: "mfa", weight: 4, options: MATURITY, prompt: "Is multi-factor authentication enforced for workforce access to systems that store or process customer data?" },
  { category: "encryption", weight: 4, options: YES_NO, prompt: "Is customer data encrypted in transit using TLS 1.2 or higher?" },
  { category: "encryption", weight: 4, options: MATURITY, prompt: "Is customer data encrypted at rest, with keys managed and rotated under a documented process?" },
  { category: "vulnerability_management", weight: 3, options: MATURITY, prompt: "Are systems scanned for vulnerabilities at least monthly, with defined remediation timelines?" },
  { category: "vulnerability_management", weight: 3, options: MATURITY, prompt: "Is an independent penetration test performed at least annually, with findings tracked to closure?" },
  { category: "incident_response", weight: 4, options: MATURITY, prompt: "Is there a documented incident response plan that is tested at least annually?" },
  { category: "incident_response", weight: 4, options: YES_NO, prompt: "Will customers be notified of a confirmed security breach within a contractually defined period?" },
  { category: "business_continuity", weight: 3, options: MATURITY, prompt: "Are backups taken regularly and restoration tested at least annually, with defined recovery objectives?" },
  { category: "business_continuity", weight: 3, options: MATURITY, prompt: "Is there a business continuity and disaster recovery plan that is tested at least annually?" },
  { category: "data_protection", weight: 3, options: MATURITY, prompt: "Is there a data classification, retention and secure deletion policy that is followed in practice?" },
  { category: "data_protection", weight: 2, options: MATURITY, prompt: "Are staff with access to customer data background-checked and trained in security at least annually?" },
  { category: "vendor_governance", weight: 3, options: MATURITY, prompt: "Does the vendor assess and monitor the security of its own critical subprocessors?" },
];
