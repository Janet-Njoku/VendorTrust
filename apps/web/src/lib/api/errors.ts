/** Errors from the API, normalized so screens never deal with raw responses. */

export type ErrorKind =
  | "network"
  | "unauthenticated"
  | "no_membership"
  | "mfa_required"
  | "org_required"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "validation"
  | "unavailable"
  | "server";

export class ApiError extends Error {
  readonly status: number;
  readonly kind: ErrorKind;
  /** Field name to message, from FastAPI 422 responses. */
  readonly fieldErrors: Record<string, string>;
  /** Present on the 409 returned when an assessment is scored before it is complete. */
  readonly unanswered: string[];

  constructor(
    status: number,
    message: string,
    options: { fieldErrors?: Record<string, string>; unanswered?: string[] } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.fieldErrors = options.fieldErrors ?? {};
    this.unanswered = options.unanswered ?? [];
    this.kind = classify(status, message);
  }
}

function classify(status: number, message: string): ErrorKind {
  if (status === 0) return "network";
  if (status === 401) return "unauthenticated";
  if (status === 400 && message.includes("X-Org-Id")) return "org_required";
  if (status === 403) {
    if (message.includes("No active membership")) return "no_membership";
    if (message.includes("Multi-factor authentication required")) return "mfa_required";
    return "forbidden";
  }
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 422 || status === 400) return "validation";
  if (status === 503) return "unavailable";
  return "server";
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  return new ApiError(0, "Something went wrong. Please try again.");
}

interface ValidationDetail {
  loc?: unknown[];
  msg?: string;
}

/** Turn a FastAPI error body into an ApiError. Database text is never shown by the API. */
export function fromResponseBody(status: number, body: unknown): ApiError {
  const record = typeof body === "object" && body !== null ? (body as Record<string, unknown>) : {};
  const detail = record.detail;
  const unanswered = Array.isArray(record.unanswered_question_ids)
    ? record.unanswered_question_ids.filter((v): v is string => typeof v === "string")
    : [];
  if (typeof detail === "string") return new ApiError(status, detail, { unanswered });
  if (Array.isArray(detail)) {
    const fieldErrors: Record<string, string> = {};
    for (const raw of detail as ValidationDetail[]) {
      const path = (raw.loc ?? []).filter((p) => p !== "body" && p !== "query" && p !== "path");
      const field = path.map(String).join(".") || "form";
      fieldErrors[field] ??= (raw.msg ?? "Invalid value").replace(/^Value error, /, "");
    }
    return new ApiError(status, "Please correct the highlighted fields.", { fieldErrors });
  }
  return new ApiError(status, `Request failed (${status}).`);
}

/** Short, user-facing explanation for the kinds of error a screen cannot fix itself. */
export function describeKind(error: ApiError): string {
  switch (error.kind) {
    case "network":
      return "The API could not be reached. Check that it is running and that NEXT_PUBLIC_API_BASE_URL is correct.";
    case "unauthenticated":
      return "Your session has expired. Please sign in again.";
    case "forbidden":
      return "Your role does not allow this action.";
    case "mfa_required":
      return "Multi-factor authentication is required for your role. Verify with your authenticator to continue.";
    case "unavailable":
      return "The API is not available right now.";
    default:
      return error.message;
  }
}
