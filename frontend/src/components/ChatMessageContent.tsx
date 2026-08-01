"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Renders one of Atlas's own replies as markdown (bold, lists, links,
 *  tables, code) instead of a raw `whitespace-pre-wrap` blob. Atlas's system
 *  prompt already tells every agent to "prefer short, scannable structure"
 *  and produces real markdown for it (bullet lists, bold labels, the odd
 *  code snippet in a Tutor explanation) — this just renders what's already
 *  being generated instead of showing the literal asterisks/dashes. Never
 *  used for a user's own message, which is plain text the student typed. */
export function ChatMessageContent({
  content,
  className = "text-[15px] leading-relaxed",
}: {
  content: string;
  className?: string;
}) {
  return (
    <div className={`${className} [&>*:first-child]:mt-0 [&>*:last-child]:mb-0`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: (props) => <p className="mb-2.5 last:mb-0" {...props} />,
          ul: (props) => <ul className="mb-2.5 ml-4 list-disc space-y-1 last:mb-0" {...props} />,
          ol: (props) => <ol className="mb-2.5 ml-4 list-decimal space-y-1 last:mb-0" {...props} />,
          li: (props) => <li className="pl-0.5" {...props} />,
          strong: (props) => <strong className="font-semibold text-atlas-text" {...props} />,
          em: (props) => <em className="italic" {...props} />,
          a: (props) => (
            <a
              className="text-atlas-accent underline underline-offset-2 hover:text-atlas-accent2"
              target="_blank"
              rel="noreferrer"
              {...props}
            />
          ),
          code: (props) => (
            <code className="rounded bg-atlas-panel2 px-1 py-0.5 text-[13px] font-mono" {...props} />
          ),
          pre: (props) => (
            <pre
              className="mb-2.5 rounded-lg bg-atlas-panel2 p-2.5 text-[13px] font-mono overflow-x-auto [&>code]:bg-transparent [&>code]:p-0 last:mb-0"
              {...props}
            />
          ),
          h1: (props) => <h3 className="mb-1.5 mt-3 text-base font-semibold first:mt-0" {...props} />,
          h2: (props) => <h3 className="mb-1.5 mt-3 text-base font-semibold first:mt-0" {...props} />,
          h3: (props) => <h3 className="mb-1.5 mt-3 text-sm font-semibold first:mt-0" {...props} />,
          blockquote: (props) => (
            <blockquote className="mb-2.5 border-l-2 border-atlas-border pl-3 text-atlas-muted last:mb-0" {...props} />
          ),
          table: (props) => (
            <div className="mb-2.5 overflow-x-auto last:mb-0">
              <table className="w-full text-sm border-collapse" {...props} />
            </div>
          ),
          th: (props) => <th className="border-b border-atlas-border px-2 py-1 text-left font-medium" {...props} />,
          td: (props) => <td className="border-b border-atlas-border/50 px-2 py-1" {...props} />,
          hr: (props) => <hr className="my-3 border-atlas-border" {...props} />,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
