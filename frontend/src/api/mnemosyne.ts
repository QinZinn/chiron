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

export interface WeakCard {
  card_id: string;
  question: string;
  added_at: string;
  recent_reviews: number;
  recent_wrong: number;
  /** Listing is append-only for the life of a task, so a listed card may have recovered. */
  still_weak: boolean;
  last_reviewed_at: string | null;
}

export interface WeakTask {
  id: string;
  study_set_id: string;
  study_set_name: string;
  opened_at: string;
  last_weak_card_at: string;
  closed_at: string | null;
  cards: WeakCard[];
  still_weak_count: number;
}

export interface WeakResponse {
  tasks: WeakTask[];
  card_count: number;
  still_weak_count: number;
  /** The rule the numbers come from — reported by the server, not assumed here. */
  window: number;
  error_threshold: number;
}

export interface DayCount {
  day: string;
  total: number;
  correct: number;
}

export interface Stats {
  range_days: number;
  tz_offset_minutes: number;
  /** accuracy is null when nothing was answered — not 0, which would read as "all wrong". */
  reviews: { total: number; correct: number; accuracy: number | null; by_day: DayCount[] };
  quiz: { attempts: number; correct: number; accuracy: number | null };
  cards: { total: number; due_now: number; never_reviewed: number; study_sets: number };
  streak_days: number;
  socratic_sessions: number;
  chat_sessions: number;
}

export interface FeynmanEvaluation {
  evaluation_id: string;
  clarity_score: number;
  completeness_score: number;
  correctness_score: number;
  feedback: string;
  suggestions: string;
}

export interface FeynmanHistoryEntry {
  id: string;
  explanation_text: string;
  clarity_score: number;
  completeness_score: number;
  correctness_score: number;
  feedback: string;
  suggestions: string;
  created_at: string;
}

export type ChatMode = 'ask' | 'solve';

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
}

export interface ChatSessionSummary {
  id: string;
  mode: ChatMode;
  set_id: string | null;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface SocraticSummary {
  id: string;
  set_id: string;
  set_name: string;
  created_at: string;
  ended_at: string | null;
  last_message_at: string | null;
  message_count: number;
  ended: boolean;
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

  /** The browser knows its own timezone; the server must not guess it. */
  stats: (days = 14) =>
    request<Stats>(S, `${base}/stats?days=${days}&tz_offset_minutes=${-new Date().getTimezoneOffset()}`),

  feynmanEvaluate: (setId: string, explanation: string) =>
    request<FeynmanEvaluation>(S, `${base}/study_sets/${encodeURIComponent(setId)}/feynman_evaluate`, {
      method: 'POST',
      body: { explanation_text: explanation },
      timeoutMs: LLM_TIMEOUT_MS,
    }),

  feynmanHistory: (setId: string) =>
    request<{ evaluations: FeynmanHistoryEntry[]; count: number }>(
      S,
      `${base}/study_sets/${encodeURIComponent(setId)}/feynman_evaluate/history`,
    ),

  patchMe: (learningStyle: string | null) =>
    request<User>(S, `${base}/me`, { method: 'PATCH', body: { learning_style: learningStyle } }),

  patchCard: (cardId: string, patch: { question?: string; answer?: string }) =>
    request<{ id: string; question: string; answer: string }>(S, `${base}/cards/${encodeURIComponent(cardId)}`, {
      method: 'PATCH',
      body: patch,
    }),

  deleteCard: (cardId: string) =>
    request<{ deleted: boolean }>(S, `${base}/cards/${encodeURIComponent(cardId)}`, { method: 'DELETE' }),

  patchStudySet: (setId: string, patch: { name?: string; topic?: string | null }) =>
    request<StudySet>(S, `${base}/study_sets/${encodeURIComponent(setId)}`, { method: 'PATCH', body: patch }),

  deleteStudySet: (setId: string) =>
    request<{ deleted: boolean }>(S, `${base}/study_sets/${encodeURIComponent(setId)}`, { method: 'DELETE' }),

  deleteQuizQuestion: (questionId: string) =>
    request<{ deleted: boolean }>(S, `${base}/quiz/${encodeURIComponent(questionId)}`, { method: 'DELETE' }),

  deleteSocratic: (sessionId: string) =>
    request<{ deleted: boolean }>(S, `${base}/socratic/${encodeURIComponent(sessionId)}`, { method: 'DELETE' }),

  deleteChat: (sessionId: string) =>
    request<{ deleted: boolean }>(S, `${base}/chat/${encodeURIComponent(sessionId)}`, { method: 'DELETE' }),

  weakCards: (includeClosed = false) =>
    request<WeakResponse>(S, `${base}/weak_cards?include_closed=${includeClosed}`),

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

  socraticList: () =>
    request<{ sessions: SocraticSummary[]; count: number }>(S, `${base}/socratic`),

  chatStart: (mode: ChatMode, message: string, studySetId?: string) =>
    request<{ session_id: string; mode: ChatMode; title: string; reply: string }>(S, `${base}/chat/start`, {
      method: 'POST',
      body: { mode, message, study_set_id: studySetId ?? null },
      timeoutMs: LLM_TIMEOUT_MS,
    }),

  chatReply: (sessionId: string, message: string) =>
    request<{ reply: string }>(S, `${base}/chat/${encodeURIComponent(sessionId)}/reply`, {
      method: 'POST',
      body: { message },
      timeoutMs: LLM_TIMEOUT_MS,
    }),

  chatSession: (sessionId: string) =>
    request<{ session_id: string; mode: ChatMode; set_id: string | null; title: string; messages: ChatMessage[] }>(
      S,
      `${base}/chat/${encodeURIComponent(sessionId)}`,
    ),

  chatList: () =>
    request<{ sessions: ChatSessionSummary[]; count: number }>(S, `${base}/chat`),

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
