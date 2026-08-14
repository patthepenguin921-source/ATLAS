-- A student marking an assignment done themselves ("Mark complete" / the
-- status dropdown) was writing status='graded' -- indistinguishable from a
-- real PowerSchool-synced grade, and misleading (the assignment detail view
-- even has to call out "synced as graded, but no score recorded" to cover
-- for it). 'completed' is the honest label for "I did the work"; 'graded'
-- is reserved for when an actual score/percentage is on file (either via
-- PowerSchool sync or manual grade entry, both of which already flip the
-- status to 'graded' themselves once a real grade lands).
alter type assignment_status add value if not exists 'completed';
