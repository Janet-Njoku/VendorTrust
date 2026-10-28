/**
 * Request and response types for the VendorTrust API. They mirror the backend schemas
 * (apps/api/app/schemas.py and schemas_questionnaires.py, documented in docs/api.md).
 */

export type Uuid = string;

export type VendorStatus = "active" | "archived";
export type ItemStatus = "active" | "archived";
export type VersionStatus = "draft" | "published" | "retired";
export type AssessmentStatus = "in_progress" | "scored";
export type RiskLevel = "low" | "medium" | "high" | "critical";

export interface Page<T> {
  items: T[];
  limit: number;
  offset: number;
}

// --- vendors ---

export interface Vendor {
  id: Uuid;
  org_id: Uuid;
  name: string;
  status: VendorStatus;
  created_at: string;
  updated_at: string;
}
export interface VendorCreate {
  name: string;
  status?: VendorStatus;
}
export interface VendorUpdate {
  name?: string;
  status?: VendorStatus;
}

// --- questionnaires ---

export interface Template {
  id: Uuid;
  org_id: Uuid;
  name: string;
  description: string | null;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
}
export interface VersionSummary {
  id: Uuid;
  version_number: number;
  status: VersionStatus;
  question_count: number;
  published_at: string | null;
}
export interface TemplateDetail extends Template {
  versions: VersionSummary[];
}
export interface TemplateCreate {
  name: string;
  description?: string | null;
}
export interface TemplateUpdate {
  name?: string;
  description?: string | null;
  status?: "active" | "archived";
}

export interface Option {
  id: Uuid;
  position: number;
  label: string;
  /** 0 (best answer) to 100 (worst); null means "not applicable" (excluded from scoring). */
  risk_points: number | null;
  status: ItemStatus;
}
export interface OptionInput {
  label: string;
  risk_points: number | null;
}
export interface OptionUpdate {
  label?: string;
  risk_points?: number | null;
  status?: ItemStatus;
}

export interface Question {
  id: Uuid;
  position: number;
  category: string;
  prompt: string;
  guidance: string | null;
  weight: number;
  status: ItemStatus;
  options: Option[];
}
export interface QuestionInput {
  category: string;
  prompt: string;
  guidance?: string | null;
  weight: number;
  options: OptionInput[];
}
export interface QuestionUpdate {
  category?: string;
  prompt?: string;
  guidance?: string | null;
  weight?: number;
  status?: ItemStatus;
}

export interface Thresholds {
  low_max: number;
  medium_max: number;
  high_max: number;
}
export interface Version extends Thresholds {
  id: Uuid;
  org_id: Uuid;
  template_id: Uuid;
  version_number: number;
  status: VersionStatus;
  published_at: string | null;
  created_at: string;
  updated_at: string;
  questions: Question[];
}

// --- assessments ---

export interface Assessment {
  id: Uuid;
  org_id: Uuid;
  vendor_id: Uuid;
  version_id: Uuid;
  status: AssessmentStatus;
  created_by: Uuid;
  score: number | null;
  risk_level: RiskLevel | null;
  scored_at: string | null;
  scored_by: Uuid | null;
  created_at: string;
  updated_at: string;
}
export type AssessmentCreate = { version_id: Uuid } | { questionnaire_id: Uuid };

export interface AssessmentResponse {
  id: Uuid;
  assessment_id: Uuid;
  question_id: Uuid;
  option_id: Uuid;
  comment: string | null;
  answered_by: Uuid;
  updated_at: string;
}
export interface ResponseInput {
  option_id: Uuid;
  comment?: string | null;
}
export interface AssessmentQuestion extends Question {
  response: AssessmentResponse | null;
}
export interface AssessmentDetail extends Assessment {
  progress: { answered: number; total: number };
  questions: AssessmentQuestion[];
}

export interface CategoryScore {
  score: number | null;
  weight: number;
  questions: number;
}
export interface BreakdownItem {
  question_id: Uuid;
  category: string;
  weight: number;
  risk_points: number | null;
}
export interface Breakdown {
  method: string;
  score: number;
  risk_level: RiskLevel;
  thresholds: Thresholds;
  applicable_weight: number;
  categories: Record<string, CategoryScore>;
  not_applicable: Uuid[];
  items: BreakdownItem[];
}
export interface AssessmentResult {
  assessment_id: Uuid;
  vendor_id: Uuid;
  version_id: Uuid;
  score: number;
  risk_level: RiskLevel;
  scored_at: string;
  scored_by: Uuid;
  breakdown: Breakdown;
}

// --- the signed-in user (GET /me) ---

export type Role = "org_admin" | "analyst" | "reviewer" | "auditor" | "vendor_user";

export interface Membership {
  org_id: Uuid;
  role: Role;
  vendor_id: Uuid | null;
}
export interface MfaState {
  /** The user's role needs aal2 (an internal role) and the API enforces it. */
  required: boolean;
  satisfied: boolean;
  level: "aal1" | "aal2";
}
export interface Me {
  user_id: Uuid;
  email: string;
  status: "active" | "disabled";
  memberships: Membership[];
  active: Membership | null;
  has_access: boolean;
  /** Several memberships and none chosen: send X-Org-Id to pick one. */
  needs_organization: boolean;
  mfa: MfaState;
}

export interface ListParams {
  limit?: number;
  offset?: number;
}
