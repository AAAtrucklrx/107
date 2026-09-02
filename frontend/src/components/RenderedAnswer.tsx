import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import type { Source } from "../types";
import { SourceList } from "./SourceList";

interface RenderedAnswerProps {
  content: string;
  sources: Source[];
}

/** 把正文中的引用标记 [n] 渲染为药丸角标；fenced/inline 代码内不替换。 */
function withCitePills(content: string): string {
  return content
    .split(/(```[\s\S]*?```|`[^`\n]*`)/g)
    .map((part, index) => {
      if (index % 2 === 1) return part;
      return part.replace(/\[(\d{1,2})\](?!\()/g, (_match, num: string) => `<sup class="cite">${num}</sup>`);
    })
    .join("");
}

export function RenderedAnswer({ content, sources }: RenderedAnswerProps) {
  return (
    <>
      {content && (
        <div className="markdown-body">
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>
            {withCitePills(content)}
          </ReactMarkdown>
        </div>
      )}
      <SourceList sources={sources} />
    </>
  );
}
