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

/** GitHub Alerts 语法的语义类型；颜色映射见 index.css（遵循「一色一义」）。 */
const CALLOUT_KINDS = new Set(["NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION"]);

type MdastNode = {
  type?: string;
  value?: string;
  children?: MdastNode[];
  data?: { hProperties?: Record<string, unknown> };
};

/** 把 `> [!NOTE] …` 标记为语义提示块（自称"注意/警告/风险/重要/提示"）。
 *
 * 自带实现、**不引入新依赖**：遍历 mdast 给 blockquote 挂上 class 与 data 属性，
 * 颜色、图标、文字标签全部由 CSS 提供——图标+文字保证**不依赖颜色**也能读懂（WCAG 1.4.1）。
 * 只剥离 `[!类型]` 标记本身，块内其余内容（加粗、引用编号 `[n]` 等）照常渲染。 */
function remarkCallouts() {
  const walk = (node: MdastNode): void => {
    if (!Array.isArray(node.children)) return;
    for (const child of node.children) {
      if (child.type === "blockquote") {
        const paragraph = child.children?.[0];
        const text = paragraph?.children?.[0];
        if (paragraph?.type === "paragraph" && text?.type === "text" && typeof text.value === "string") {
          const matched = /^\[!(\w+)\]\s*\n?/.exec(text.value);
          const kind = matched?.[1]?.toUpperCase();
          if (matched && kind && CALLOUT_KINDS.has(kind)) {
            text.value = text.value.slice(matched[0].length);
            child.data = {
              ...(child.data ?? {}),
              hProperties: {
                ...(child.data?.hProperties ?? {}),
                className: ["callout", `callout--${kind.toLowerCase()}`],
                "data-callout": kind,
              },
            };
          }
        }
      }
      walk(child);
    }
  };
  return (tree: MdastNode): void => walk(tree);
}

export function RenderedAnswer({ content, sources }: RenderedAnswerProps) {
  return (
    <>
      {content && (
        <div className="markdown-body">
          <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkCallouts as unknown as typeof remarkGfm]}
            rehypePlugins={[rehypeRaw]}
          >
            {withCitePills(content)}
          </ReactMarkdown>
        </div>
      )}
      <SourceList sources={sources} />
    </>
  );
}
