"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { Badge, Empty, Loading, Section } from "@/components/ui";
import { apiGet } from "@/lib/api";

export default function DocumentDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id as string;
  const [doc, setDoc] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    apiGet(`/documents/${id}`)
      .then(setDoc)
      .catch((e: any) => setError(e.message));
  }, [id]);

  if (error) {
    return (
      <AppShell title="Document" subtitle="Something went wrong">
        <div className="card border-atlas-bad/40 text-atlas-bad text-sm">{error}</div>
      </AppShell>
    );
  }
  if (!doc) {
    return (
      <AppShell title="Document">
        <Loading />
      </AppShell>
    );
  }

  return (
    <AppShell
      title={doc.title}
      subtitle={doc.doc_type}
      actions={
        <>
          <Link href="/documents" className="btn-ghost">Back to documents</Link>
          {doc.download_url && (
            <a href={doc.download_url} target="_blank" rel="noopener noreferrer" className="btn-primary">
              Open original
            </a>
          )}
        </>
      }
    >
      <div className="flex flex-wrap items-center gap-2 mb-6">
        <Badge tone={doc.ingested ? "good" : "warn"}>{doc.ingested ? "indexed" : "pending"}</Badge>
        {doc.keywords?.map((k: string) => <Badge key={k}>{k}</Badge>)}
      </div>

      {doc.summary && (
        <Section title="Summary">
          <div className="card text-sm text-atlas-muted">{doc.summary}</div>
        </Section>
      )}

      <Section title="Extracted text">
        {doc.extracted_text ? (
          <div className="card whitespace-pre-wrap text-sm leading-relaxed max-h-[70vh] overflow-y-auto">
            {doc.extracted_text}
          </div>
        ) : (
          <Empty>
            {doc.ingest_error
              ? `No text could be extracted (${doc.ingest_error}).`
              : "No text extracted yet."}
          </Empty>
        )}
      </Section>
    </AppShell>
  );
}
