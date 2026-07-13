-- =====================================================================
-- ATLAS — 0007 · Storage bucket for uploaded documents
-- Private bucket; access is per-user by path prefix (userId/...).
-- =====================================================================

insert into storage.buckets (id, name, public)
values ('atlas-documents', 'atlas-documents', false)
on conflict (id) do nothing;

-- Users may only touch objects under a folder named after their uid.
-- The backend (service role) bypasses these and can manage all objects.

drop policy if exists "atlas docs read own" on storage.objects;
create policy "atlas docs read own" on storage.objects
  for select using (
    bucket_id = 'atlas-documents'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

drop policy if exists "atlas docs insert own" on storage.objects;
create policy "atlas docs insert own" on storage.objects
  for insert with check (
    bucket_id = 'atlas-documents'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

drop policy if exists "atlas docs update own" on storage.objects;
create policy "atlas docs update own" on storage.objects
  for update using (
    bucket_id = 'atlas-documents'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

drop policy if exists "atlas docs delete own" on storage.objects;
create policy "atlas docs delete own" on storage.objects
  for delete using (
    bucket_id = 'atlas-documents'
    and (storage.foldername(name))[1] = auth.uid()::text
  );
