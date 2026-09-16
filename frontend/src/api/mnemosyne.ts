/**
 * Mnemosyne (Rust/Actix, 127.0.0.1:8081). Called directly from the browser
 * under its CORS policy, with the learner's bearer token attached by
 * `api/http.ts`.
 *
 * No function here takes a `user_id`: the server derives the learner from the
 * token, so the browser cannot ask for someone else's rows even by mistake.
 * Shapes mirror mnemosyne/backend/src/handlers/*.rs.
 */
import { config } from '../config';
import { request } from './http';

const S = 'Mnemosyne' as const;
const base = config.mnemosyneUrl;

/** LLM-backed routes: DeepSeek is a reasoning model and routinely takes 10–60 s. */
const LLM_TIMEOUT_MS = 150_000;

export interface User {
  id: string;
  email: string;
  learning_style: string | null;
  created_at: string;
}

export interface StudySet {
  id: string;
  user_id: string;
  name: string;
  topic: string | null;
  created_at: string;
}

export interface DueCard {
  card_id: string;
  set_id: string;
  question: string;
  answer: string;
  is_new: boolean;
  stability: number | null;
  difficulty: number | null;
  next_review_at: string | null;
}

export interface DueResponse {
  due_cards: DueCard[];
  count: number;
}

export type Rating = 'again' | 'hard' | 'good' | 'easy';

export interface ReviewResponse {
  learning_event_id: string;
  stability: number;
  difficulty: number;
  interval_days: number;
  next_review_at: string;
}

export interface SocraticMessage {
  role: 'user' | 'assistant';
  content: string;
  flagged_misconception: string | null;
  created_at: string;
}

export type KsSyncStatus =
  | { state: 'saved'; transcript_id: string }
  | { state: 'disabled' }
  | { state: 'nothing_to_send' }
  | { state: 'ks_db_unavailable'; error: string }
  | { state: 'failed'; error: string };

export interface EndResponse {
  session_id: string;
  message_count: number;
  knowledge_store: KsSyncStatus;
}

export interface QuizQuestion {
  id: string;
  set_id: string;
  question: string;
  choices: string[];
  source: 'topic' | 'knowledge_store';
  source_node_id: string | null;
  created_at: string;
}

export interface GenerateQuizRequest {
  study_set_id: string;
  source: 'topic' | 'knowledge_store';
  topic?: string;
  subject_filter?: string;
  count: number;
}

/** Mirrors TURN_CAP in socratic.rs: 40 stored messages = 20 exchanges. */
export const SOCRATIC_TURN_CAP_MESSAGES = 40;
/** Mirrors MAX_QUESTION_COUNT in quiz.rs. */
export const QUIZ_MAX_COUNT = 20;

export const mnemosyne = {
  // Liveness needs no token: "is it up" must be answerable before "who am I".
  health: () => request<string>(S, `${base}/health`, { timeoutMs: 4_000, anonymous: true }),

  /// The learner this token belongs to — the frontend's whole idea of "who".
  me: () => request<User>(S, `${base}/me`),

  listStudySets: () => request<StudySet[]>(S, `${base}/study_sets`),

  due: (limit = 100) => request<DueResponse>(S, `${base}/due?limit=${limit}`),

  review: (cardId: string, rating: Rating) =>
    request<ReviewResponse>(S, `${base}/review`, {
      method: 'POST',
      body: { card_id: cardId, rating },
    }),

  socraticStart: (studySetId: string) =>
    request<{ session_id: string; opening_message: string }>(S, `${base}/socratic/start`, {
      method: 'POST',
      body: { study_set_id: studySetId },
      timeoutMs: LLM_TIMEOUT_MS,
    }),

  socraticReply: (sessionId: string, message: string) =>
    request<{ reply: string; flagged_misconception: string | null }>(
      S,
      `${base}/socratic/${encodeURIComponent(sessionId)}/reply`,
      { method: 'POST', body: { message }, timeoutMs: LLM_TIMEOUT_MS },
    ),

  socraticEnd: (sessionId: string) =>
    request<EndResponse>(S, `${base}/socratic/${encodeURIComponent(sessionId)}/end`, {
      method: 'POST',
      timeoutMs: 30_000,
    }),

  socraticHistory: (sessionId: string) =>
    request<{ session_id: string; messages: SocraticMessage[] }>(
      S,
      `${base}/socratic/${encodeURIComponent(sessionId)}`,
    ),

  quizGenerate: (req: GenerateQuizRequest) =>
    request<{ questions: QuizQuestion[]; tokens_used: number }>(S, `${base}/quiz/generate`, {
      method: 'POST',
      body: req,
      timeoutMs: LLM_TIMEOUT_MS,
    }),

  quizList: (setId: string) =>
    request<{ set_id: string; questions: QuizQuestion[]; count: number }>(
      S,
      `${base}/quiz/${encodeURIComponent(setId)}`,
    ),

  quizAttempt: (questionId: string, selectedIndex: number) =>
    request<{ is_correct: boolean; correct_index: number }>(
      S,
      `${base}/quiz/${encodeURIComponent(questionId)}/attempt`,
      { method: 'POST', body: { selected_index: selectedIndex } },
    ),
};
