export type Workspace = "chat" | "academic" | "campus";
export type RetrievalMode = "auto" | "web" | "local";
export type Theme = "light" | "dark";

export interface PublicConfig {
  environment: "development" | "competition" | "production";
  auth_mode: "anonymous" | "demo" | "cas";
  version: string;
  features: {
    chat: boolean;
    web_search: boolean;
    personal_workspace: boolean;
    review_workspace: boolean;
    ingestion_worker: boolean;
    demo_reset_enabled?: boolean;
  };
  time_budget_seconds: {
    search: number;
    evidence: number;
    generation: number;
    total: number;
  };
}

export interface Profile {
  id: string;
  name: string;
  major: string;
  grade: string;
  profile_source?: string;
  logged_in?: boolean;
}

export interface SessionPayload {
  principal: {
    id: string | null;
    auth_mode: "anonymous" | "demo" | "cas";
    authenticated: boolean;
    profile: Profile | null;
    is_admin: boolean;
    review_namespace: "demo" | "production" | null;
  };
  capabilities: {
    public_chat: boolean;
    server_history: boolean;
    personal_academic: boolean;
    knowledge_review: boolean;
    production_publish: boolean;
  };
  csrf_token: string;
}

export interface Source {
  source_id: string;
  title: string;
  display_url: string | null;
  institution?: string;
  domain?: string;
  published_at: string | null;
  fetched_at: string | null;
  level: string;
  validity: string;
  citation: number;
  tags?: string[];
}

export interface Claim {
  claim_id: string;
  text: string;
  kind: "factual" | "recommendation" | "chitchat";
  status: "confirmed" | "conflict" | "insufficient";
  evidence: Array<{
    source_id: string;
    evidence_type: "local" | "web" | "tool";
    relation: "supports" | "contradicts" | "context";
    excerpt_hash: string;
    citation: number;
  }>;
}

export interface SseEnvelope<T = Record<string, unknown>> {
  id: number;
  run_id: string;
  type: string;
  at: string;
  data: T;
}

export interface ThoughtStep {
  round: number;
  decision: string;
  reason: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
  runId?: string;
  answerId?: string;
  mode?: RetrievalMode;
  stage?: string;
  stages?: string[];
  status?: "streaming" | "completed" | "cancelled" | "failed";
  sources?: Source[];
  claims?: Claim[];
  limitations?: string[];
  terminalReason?: string;
  thoughts?: ThoughtStep[];
  truncated?: boolean;
  editing?: boolean;
}

export interface ConversationSummary {
  conversation_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: Array<{
    message_id: string;
    role: "user" | "assistant";
    content: string;
    run_id?: string | null;
    metadata?: {
      answer_id?: string;
      mode?: RetrievalMode;
      claims?: Claim[];
      sources?: Source[];
      limitations?: string[];
    };
    created_at: string;
  }>;
}

export interface ChatRunCreated {
  run_id: string;
  conversation_id: string | null;
  requested_mode: RetrievalMode;
  effective_mode: RetrievalMode;
  events_url: string;
}

export interface DataSource {
  kind: string;
  label: string;
  demo?: boolean;
  stale?: boolean;
}

export interface AcademicCourse {
  course_code?: string;
  course_name?: string;
  teacher?: string;
  credits?: number;
  time?: string;
  location?: string;
  semester?: string;
  meetings?: AcademicMeeting[];
}

export interface AcademicMeeting {
  meeting_id: string;
  weekday: number;
  day: string;
  week_numbers: number[];
  weeks: string;
  periods: number[];
  period_label: string;
  start_time: string;
  end_time: string;
  location: string;
  raw: string;
}

export interface AcademicUnparsedCourse extends AcademicCourse {
  reason: string;
  raw_schedule: string;
}

export interface GradeRecord {
  semester?: string;
  course_name?: string;
  credits?: number;
  score?: number | string;
  grade_point?: number;
}

export interface AcademicOverview {
  identity: Profile;
  metrics: {
    gpa: number | null;
    completed_credits: number | null;
    current_credits: number | null;
    grade_count: number;
  };
  recent_grades: GradeRecord[];
  grades?: GradeRecord[];
  source: DataSource;
  limitations: string[];
}

export interface ProgramCourse {
  code?: string;
  name?: string;
  required?: string;
  credit?: number;
  term?: string;
  category?: string;
}

export interface ProgramModule {
  category?: string;
  required_credits?: number;
  course_count?: number;
}

export interface AcademicProgram {
  program: {
    program_id?: string;
    name?: string;
    college?: string;
    grade?: string;
    personal?: boolean;
    source?: string;
    fallback_from_personal?: boolean;
    totalCredits?: number;
    total_credits?: number;
    modules?: ProgramModule[];
    courses?: ProgramCourse[];
    [key: string]: unknown;
  };
  progress: Record<string, unknown> | null;
  source: DataSource;
  banner: string | null;
  limitations: string[];
}

export interface AcademicCourses {
  courses: AcademicCourse[];
  grades: GradeRecord[];
  source: DataSource;
  limitations: string[];
}

export interface AcademicSchedule {
  semester: string;
  semester_code: string;
  semester_start: string;
  total_weeks: number;
  current_week: number | null;
  courses: AcademicCourse[];
  unparsed_courses: AcademicUnparsedCourse[];
  source: DataSource;
  limitations: string[];
}

export interface CampusServiceItem {
  name: string;
  url: string;
  description: string;
  category: string;
  featured: boolean;
  priority: number | null;
}

export interface CampusServices {
  items: CampusServiceItem[];
  categories: string[];
  source: DataSource;
}

export type CampusToolCategory = "study" | "life" | "information" | "community" | "other";
export type CampusToolApplicationStatus = "pending" | "approved" | "rejected";

export interface CampusToolItem {
  tool_id: string;
  application_id: string;
  name: string;
  description: string;
  display_description: string;
  category: CampusToolCategory;
  url: string;
  normalized_url: string;
  status: "active" | "unpublished";
  published_at: number;
  version: number;
}

export interface CampusToolsDirectory {
  items: CampusToolItem[];
  categories: CampusToolCategory[];
  source: DataSource;
}

export interface CampusToolApplication {
  application_id: string;
  applicant_principal_id: string;
  applicant_name_snapshot: string;
  name: string;
  description: string;
  display_description: string;
  category: CampusToolCategory;
  submitted_url: string;
  normalized_url: string;
  status: CampusToolApplicationStatus;
  decision_reason: string | null;
  reviewed_at: number | null;
  version: number;
  created_at: number;
  updated_at: number;
  tool_id: string | null;
  tool_status: "active" | "unpublished" | null;
  unpublish_reason: string | null;
  unread: boolean;
}

export interface CampusToolApplicationsMine {
  items: CampusToolApplication[];
  unread_count: number;
  namespace: "demo" | "production";
}

export interface CampusToolNotification {
  notification_id: string;
  notification_type: "tool_approved" | "tool_rejected" | "tool_unpublished";
  title: string;
  body: string;
  application_id: string;
  tool_id: string | null;
  read_at: number | null;
  created_at: number;
}

export interface CampusToolNotifications {
  items: CampusToolNotification[];
  namespace: "demo" | "production";
}

export interface ManagedCampusTool extends CampusToolItem {
  applicant_principal_id: string;
  applicant_name_snapshot: string;
  unpublished_by: string | null;
  unpublished_at: number | null;
  unpublish_reason: string | null;
}

export interface CampusToolAuditEntry {
  audit_id: string;
  actor_key: string;
  action: "application_submitted" | "application_approved" | "application_rejected" | "tool_unpublished";
  object_type: "application" | "tool" | "notification";
  object_id: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  reason: string | null;
  request_id: string;
  created_at: number;
}

export interface CampusActivity {
  id?: string | number;
  title?: string;
  name?: string;
  category?: string;
  description?: string;
  location?: string;
  start_time?: string;
  end_time?: string;
  deadline?: string;
  url?: string;
  [key: string]: unknown;
}

export interface CampusActivities {
  items: CampusActivity[];
  fetched_at?: string | null;
  source: DataSource;
  limitations?: string[];
}

export type ReviewStatus =
  | "draft"
  | "in_review"
  | "approved"
  | "pending_publish"
  | "publish_failed"
  | "active"
  | "rejected"
  | "expired"
  | "revoked";

export interface ReviewItemSummary {
  item_id: string;
  title: string;
  status: ReviewStatus;
  scope: "campus" | "general";
  category: ReviewCategory;
  ttl_days: number;
  normalized_url: string;
  fetched_at: string | null;
  current_version: number;
  updated_at: number;
}

export type ReviewCategory = "announcement" | "dynamic_service" | "policy" | "stable_general";

export interface ReviewVersion {
  version_id: string;
  version_number: number;
  kind: "raw" | "model" | "human" | "approved";
  content_text: string;
  content_hash: string;
  actor_key: string;
  created_at: number;
}

export interface ReviewChunk {
  chunk_id: string;
  version_id: string;
  position: number;
  content_text: string;
  approval_status?: "pending" | "approved" | "rejected";
  approved: number | boolean;
  expires_at: number | null;
}

export interface ReviewItemDetail extends ReviewItemSummary {
  final_url: string;
  content_type: string;
  snapshot_hash: string;
  raw_snapshot: string;
  versions: ReviewVersion[];
  chunks: ReviewChunk[];
  diff: string;
}

export interface GenerationState {
  namespace: "demo" | "production";
  active_generation_id: string | null;
  previous_generation_id: string | null;
  activated_at: number | null;
  can_rollback: boolean;
  publish_busy: boolean;
}

export interface SourceTrustProposal {
  host: string;
  path_prefix: string;
  level: "official_primary" | "reliable_independent";
  institution: string;
  effective_from: string;
  rationale: string;
}

export interface ApiErrorShape {
  error: {
    code: string;
    message: string;
    fields?: string[];
  };
}
