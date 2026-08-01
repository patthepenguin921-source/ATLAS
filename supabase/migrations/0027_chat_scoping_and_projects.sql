-- =====================================================================
-- ATLAS — 0027 · Auto-sorted chat scoping, pinning, project instructions
--
-- - `conversations.course_id` -- which class a conversation belongs to.
--   Set at creation time from whatever class scope was active (see
--   ChatRequest.course_id in app.routers.agents.chat), or auto-assigned
--   shortly after for a conversation started unscoped, once its content
--   clearly points to one class (see app.agents.chat_scope, best-effort,
--   never runs a second time once a value is set). Null means "General."
-- - `conversations.pinned` -- keeps a chat pinned to the top of its
--   scope's section in the sidebar regardless of recency.
-- - `chat_projects.instructions` -- custom instructions for every chat
--   filed under a project (the same "Projects" concept other AI products
--   have), injected into the system prompt of any conversation with that
--   project_id (see Agent.system_prompt's project_instructions param).
-- =====================================================================

alter table public.conversations
  add column if not exists course_id uuid references public.courses(id) on delete set null,
  add column if not exists pinned boolean not null default false;

create index if not exists idx_conversations_course on public.conversations(user_id, course_id);
create index if not exists idx_conversations_pinned on public.conversations(user_id, pinned);

alter table public.chat_projects
  add column if not exists instructions text;
