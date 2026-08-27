import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Source } from "../types";
import { SourceList } from "./SourceList";

interface RenderedAnswerProps {
  content: string;
  sources: Source[];
}

export function RenderedAnswer({ content, sources }: RenderedAnswerProps) {
  return (
    <>
      {content && (
        <div className="markdown-body">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
      )}
      <SourceList sources={sources} />
    </>
  );
}
