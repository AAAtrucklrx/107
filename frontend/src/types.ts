export type Workspace = "chat" | "academic" | "campus" | "review";
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
  courses: AcademicCourse[];
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
