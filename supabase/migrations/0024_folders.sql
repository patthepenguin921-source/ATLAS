-- =====================================================================
-- ATLAS — 0024 · Folders (per-class organization + a "General" divider)
--
-- Every class already acts as a top-level divider via `courses` — this
-- adds subfolders *within* that divider (e.g. "Unit 3", "Homework",
-- "Study guides"), plus a single singleton "General" tree (course_id
-- null, is_general true) for documents that don't belong to any class.
-- Documents keep their existing `course_id` FK as the class-level
-- divider; `folder_id` is the optional subfolder within that divider (or
-- within General, when `course_id` is null).
--
-- `folder_source` mirrors the `doc_type_source`/`importance_source` idiom
-- (0016, 0022): tracks whether the Archivist auto-sorted a document into
-- a folder or a student manually filed it, so a manual move is never
-- reverted by a later re-enrichment pass.
-- =====================================================================

create table if not exists public.folders (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references auth.users(id) on delete cascade,
  course_id         uuid references public.courses(id) on delete cascade,
  parent_folder_id  uuid references public.folders(id) on delete cascade,
  name              text not null,
  is_general        boolean not null default false,
  sort_order        integer not null default 0,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

-- Exactly one General root per user (course_id null, parent_folder_id
-- null, is_general true) — created lazily on first use, never directly by
-- the client (see app.services.folders.get_or_create_general_folder).
create unique index if not exists idx_folders_one_general_per_user
  on public.folders(user_id) where is_general;

create index if not exists idx_folders_user    on public.folders(user_id);
create index if not exists idx_folders_course  on public.folders(user_id, course_id);
create index if not exists idx_folders_parent  on public.folders(parent_folder_id);

alter table public.folders enable row level security;
drop policy if exists folders_owner on public.folders;
create policy folders_owner on public.folders
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

drop trigger if exists trg_folders_updated on public.folders;
create trigger trg_folders_updated before update on public.folders
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------
-- Documents: optional subfolder within their course (or within General,
-- for a course_id-less document).
-- ---------------------------------------------------------------------
alter table public.documents
  add column if not exists folder_id uuid references public.folders(id) on delete set null,
  add column if not exists folder_source text
    check (folder_source is null or folder_source in ('ai', 'manual'));

create index if not exists idx_documents_folder on public.documents(folder_id);

-- ---------------------------------------------------------------------
-- Scope semantic search to a folder too (mirrors the existing p_course_id
-- filter) — lets a course page's embedded assistant narrow retrieval to
-- just what's filed in one topic/unit folder.
-- ---------------------------------------------------------------------
create or replace function public.match_document_chunks(
  query_embedding vector(1024),
  p_user_id uuid,
  match_count int default 8,
  similarity_threshold float default 0.0,
  p_course_id uuid default null,
  p_folder_id uuid default null
)
returns table (
  id uuid,
  document_id uuid,
  chunk_index int,
  content text,
  similarity float,
  document_title text
)
language sql stable as $$
  select c.id, c.document_id, c.chunk_index, c.content,
         1 - (c.embedding <=> query_embedding) as similarity,
         d.title as document_title
  from public.document_chunks c
  join public.documents d on d.id = c.document_id
  where c.user_id = p_user_id
    and c.embedding is not null
    and (p_course_id is null or d.course_id = p_course_id)
    and (p_folder_id is null or d.folder_id = p_folder_id)
    and 1 - (c.embedding <=> query_embedding) >= similarity_threshold
  order by c.embedding <=> query_embedding
  limit match_count;
$$;
